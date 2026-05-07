import json
import logging
import math
import random
import time
from contextlib import contextmanager, nullcontext
from typing import Any, Dict, List

import pandas as pd
import pydash as _
import requests as rq

from . import convert as convert_module
from . import headers as headers_module
from .convert import ConvertError, ConvertHttpError, convert
from .headers import (
    allocate_request_id,
    build_request_headers,
    CACHE_POLICY_BYPASS,
    CACHE_POLICY_REUSE,
    format_token_bucket_label,
    record_request_event,
    record_session_reset,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

REQUEST_CONFIG = {
    "robot": {"timeout": (10, 30), "retry": 10, "sleep": 0},
    "page": {"timeout": (5, 10), "retry": 10, "sleep": 0},
}
IWENCAI_BASE_URL = "https://www.iwencai.com"
ROBOT_DATA_URL = f"{IWENCAI_BASE_URL}/customized/chart/get-robot-data"
LANDING_DATA_URL = f"{IWENCAI_BASE_URL}/gateway/urp/v7/landing/getDataList"
STOCK_PICK_FIND_URL = f"{IWENCAI_BASE_URL}/unifiedwap/unified-wap/v2/stock-pick/find"

_SESSION = None


class WencaiResponseError(Exception):
    """问财响应异常基类。"""


class WencaiUnexpectedResponseError(WencaiResponseError):
    """问财返回了非预期结构。"""


class WencaiEmptyDataError(WencaiResponseError):
    """问财返回了空数据。"""


def get_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = rq.Session()
        _SESSION.trust_env = False
        _SESSION.headers.update({"Connection": "keep-alive"})
    return _SESSION


def clear_runtime_state():
    global _SESSION
    if _SESSION is not None:
        _SESSION.close()
        _SESSION = None


def reset_runtime_http_state(reason=None):
    """丢弃当前进程内复用的 HTTP 会话，避免在鉴权失败后复用脏状态。"""
    record_session_reset(reason=reason)
    clear_runtime_state()


def _sanitize_headers_for_logging(raw_headers):
    sanitized = dict(raw_headers or {})
    for key in ("cookie", "Cookie"):
        if key in sanitized and sanitized[key]:
            sanitized[key] = "<redacted>"
    for key in ("hexin-v", "Hexin-V"):
        if key in sanitized and sanitized[key]:
            sanitized[key] = "<redacted>"
    return sanitized


def _summarize_request_params_for_logging(request_params):
    if not isinstance(request_params, dict):
        return request_params
    return {key: "<configured>" for key in sorted(request_params.keys())}


def _summarize_response_for_logging(text, limit=240):
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _summarize_url_params_for_logging(url_params):
    if not isinstance(url_params, dict):
        return {"type": type(url_params).__name__}
    summary = {
        "keys": sorted(url_params.keys()),
    }
    for key in ("page", "perpage", "query_type", "comp_id", "uuid"):
        if key in url_params:
            summary[key] = url_params[key]
    return summary


def _summarize_condition_for_logging(condition, limit=120):
    if condition is None:
        return None
    if isinstance(condition, dict):
        return {"type": "dict", "keys": sorted(condition.keys())}
    if isinstance(condition, list):
        return {"type": "list", "length": len(condition)}
    return _summarize_response_for_logging(condition, limit=limit)


def _summarize_params_for_logging(params):
    if not isinstance(params, dict):
        return {"type": type(params).__name__}
    data = params.get("data")
    summary = {
        "keys": sorted(params.keys()),
        "row_count": params.get("row_count"),
        "has_url": bool(params.get("url")),
    }
    if isinstance(data, pd.DataFrame):
        summary["data_shape"] = list(data.shape)
    elif isinstance(data, dict):
        summary["data_keys"] = sorted(data.keys())
    else:
        summary["data_type"] = type(data).__name__
    url_params = params.get("url_params")
    if isinstance(url_params, dict):
        summary["url_param_keys"] = sorted(url_params.keys())
    return summary


def _is_html_response_text(text):
    compact = (text or "").lstrip().lower()
    return compact.startswith("<!doctype html") or compact.startswith("<html")


def _format_log_context(**kwargs):
    parts = []
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        if str(key).lower() == "cookie":
            value = "<redacted>"
        elif key == "request_params" and isinstance(value, dict):
            value = ",".join(sorted(value.keys())) or "{}"
        parts.append(f"{key}={value}")
    return " | ".join(parts)


def _log_with_context(level, message, **context):
    context_text = _format_log_context(**context)
    full_message = f"{message} | {context_text}" if context_text else message
    getattr(logger, level)(full_message)


@contextmanager
def _library_log_scope(log):
    if log:
        yield
        return

    managed_loggers = [logger, convert_module.logger, headers_module.logger]
    previous_states = [target.disabled for target in managed_loggers]
    try:
        for target in managed_loggers:
            target.disabled = True
        yield
    finally:
        for target, disabled in zip(managed_loggers, previous_states):
            target.disabled = disabled


def _build_request_context(
    question,
    query_type,
    cookie,
    user_agent,
    request_params,
    log,
    target,
    target_kind=None,
    request_id=None,
    parent_request_id=None,
):
    return {
        "request_id": request_id or allocate_request_id(target_kind or target),
        "parent_request_id": parent_request_id,
        "query": question,
        "query_type": query_type,
        "cookie": cookie,
        "user_agent": user_agent,
        "request_params": dict(request_params or {}),
        "log": log,
        "target": target,
        "target_kind": target_kind or target,
    }


def _build_runtime_headers(
    question,
    query_type="stock",
    cookie=None,
    user_agent=None,
    request_params=None,
    extra_headers=None,
    force_refresh_token=False,
    refresh_reason=None,
    cache_policy=CACHE_POLICY_REUSE,
):
    return build_request_headers(
        question=question,
        query_type=query_type,
        cookie=cookie,
        user_agent=user_agent,
        request_params=request_params,
        extra_headers=extra_headers,
        force_refresh_token=force_refresh_token,
        refresh_reason=refresh_reason,
        cache_policy=cache_policy,
    )


def _record_request_outcome(
    context,
    *,
    attempt_stage,
    outcome,
    status_code=None,
    error_type=None,
    refresh_reason=None,
):
    record_request_event(
        request_id=context.get("request_id"),
        parent_request_id=context.get("parent_request_id"),
        target=context.get("target_kind") or context.get("target"),
        attempt_stage=attempt_stage,
        outcome=outcome,
        bucket_label=context.get("bucket"),
        refresh_reason=refresh_reason,
        status_code=status_code,
        error_type=error_type,
        query=context.get("query"),
        query_type=context.get("query_type"),
        page=context.get("page"),
        url=context.get("target"),
    )


def _load_json_response(response_text):
    if not response_text:
        raise WencaiUnexpectedResponseError("响应内容为空")
    if _is_html_response_text(response_text):
        raise WencaiUnexpectedResponseError(
            f"响应返回 HTML 页面: {_summarize_response_for_logging(response_text)}"
        )
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise WencaiUnexpectedResponseError(
            f"响应不是有效 JSON: {_summarize_response_for_logging(response_text)}"
        ) from exc


def _extract_data_list(payload: Dict[str, Any], path: str, page_no: int):
    data_list = _.get(payload, path)
    if isinstance(data_list, list):
        if len(data_list) == 0:
            raise WencaiEmptyDataError(f"第{page_no}页返回空数据列表")
        return data_list

    if data_list is None:
        top_keys = list(payload.keys()) if isinstance(payload, dict) else []
        raise WencaiUnexpectedResponseError(
            f"第{page_no}页缺少数据列表: path={path}, top_keys={top_keys}"
        )

    raise WencaiUnexpectedResponseError(
        f"第{page_no}页数据列表类型异常: path={path}, type={type(data_list).__name__}"
    )


def _request_response(
    *,
    method,
    url,
    headers_dict,
    timeout,
    request_params=None,
    json_body=None,
    form_data=None,
    log=False,
    context=None,
    session=None,
    attempt_stage="initial",
    refresh_reason=None,
):
    try:
        response = (session or get_session()).request(
            method=method,
            url=url,
            json=json_body,
            data=form_data,
            headers=headers_dict,
            timeout=timeout,
            **(request_params or {}),
        )
    except rq.exceptions.RequestException as exc:
        if isinstance(exc, rq.exceptions.HTTPError):
            _record_request_outcome(
                context or {},
                attempt_stage=attempt_stage,
                outcome="http_error",
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
                error_type=type(exc).__name__,
                refresh_reason=refresh_reason,
            )
        else:
            _record_request_outcome(
                context or {},
                attempt_stage=attempt_stage,
                outcome="request_exception",
                error_type=type(exc).__name__,
                refresh_reason=refresh_reason,
            )
        raise
    try:
        response.raise_for_status()
    except rq.exceptions.HTTPError as exc:
        _record_request_outcome(
            context or {},
            attempt_stage=attempt_stage,
            outcome="http_error",
            status_code=getattr(getattr(exc, "response", None), "status_code", None),
            error_type=type(exc).__name__,
            refresh_reason=refresh_reason,
        )
        raise
    _record_request_outcome(
        context or {},
        attempt_stage=attempt_stage,
        outcome="success",
        status_code=response.status_code,
        refresh_reason=refresh_reason,
    )
    if log:
        _log_with_context(
            "info",
            "请求返回",
            **{
                **(context or {}),
                "status_code": response.status_code,
                "response_bytes": len(response.text),
            },
        )
        logger.debug(f"响应头: {dict(response.headers)}")
        logger.debug(f"响应内容摘要: {_summarize_response_for_logging(response.text)}")
    return response


def _request_text(**kwargs):
    response = _request_response(**kwargs)
    return response.text


def _should_retry_exception(exc):
    if isinstance(exc, rq.exceptions.Timeout):
        return True
    if isinstance(exc, rq.exceptions.ConnectionError):
        return True
    if isinstance(exc, rq.exceptions.HTTPError):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code == 429:
            return True
        if status_code is not None and status_code >= 500:
            return True
        return False
    if isinstance(exc, WencaiUnexpectedResponseError):
        return True
    if isinstance(exc, WencaiEmptyDataError):
        return False
    if isinstance(exc, ConvertError):
        return not isinstance(exc, ConvertHttpError)
    return False


def _is_auth_http_error(exc):
    if not isinstance(exc, rq.exceptions.HTTPError):
        return False
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code in {401, 403}


def _connection_retry_backoff_seconds(attempt, configured_sleep):
    exponential_backoff = min(2.0, 0.2 * (2 ** max(attempt - 1, 0)))
    return max(float(configured_sleep or 0), exponential_backoff)


def _retry_sleep_seconds(attempt, configured_sleep):
    base_sleep = _connection_retry_backoff_seconds(attempt, configured_sleep)
    jitter_ceiling = min(0.05, base_sleep * 0.1)
    return base_sleep + random.uniform(0, jitter_ceiling)


def while_do(do, retry=10, sleep=0, log=False, raise_last_exception=False):
    """
    重试执行函数，带有分类错误日志和统一退避策略。

    Args:
        do: 要执行的函数
        retry: 最大重试次数
        sleep: 重试间隔（秒）
        log: 是否记录日志
        raise_last_exception: 失败时是否抛出最后一次异常
    """
    import traceback

    attempt = 0
    last_exception = None
    while attempt < retry:
        try:
            return do()
        except rq.exceptions.Timeout as exc:
            last_exception = exc
            log and _log_with_context(
                "error",
                f"{attempt + 1}次尝试失败: 请求超时 - {exc}",
                retry_count=retry,
                attempt=attempt + 1,
            )
        except rq.exceptions.ConnectionError as exc:
            last_exception = exc
            log and _log_with_context(
                "error",
                f"{attempt + 1}次尝试失败: 连接错误 - {exc}",
                retry_count=retry,
                attempt=attempt + 1,
            )
            reset_runtime_http_state(reason="retry.connection_error")
        except rq.exceptions.HTTPError as exc:
            last_exception = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            log and _log_with_context(
                "error",
                f"{attempt + 1}次尝试失败: HTTP错误(status={status_code}) - {exc}",
                retry_count=retry,
                attempt=attempt + 1,
                status_code=status_code,
            )
        except Exception as exc:  # pragma: no cover - 由具体测试分支覆盖
            last_exception = exc
            log and _log_with_context(
                "error",
                f"{attempt + 1}次尝试失败: {type(exc).__name__} - {exc}",
                retry_count=retry,
                attempt=attempt + 1,
                error_type=type(exc).__name__,
            )
            log and logger.debug(f"异常堆栈: {traceback.format_exc()}")

        attempt += 1
        if last_exception is None or not _should_retry_exception(last_exception):
            break
        if attempt >= retry:
            break

        backoff_seconds = _retry_sleep_seconds(attempt, sleep)
        log and _log_with_context(
            "warning",
            "请求失败后等待后重试",
            retry_count=retry,
            attempt=attempt,
            backoff_seconds=round(backoff_seconds, 3),
        )
        time.sleep(backoff_seconds)

    if raise_last_exception and last_exception is not None:
        raise last_exception
    return None


def replace_key(key):
    """替换兼容参数名。"""
    key_map = {
        "question": "query",
        "sort_key": "urp_sort_index",
        "sort_order": "urp_sort_way",
    }
    return key_map.get(key, key)


def _convert_robot_response_with_retry(
    response,
    *,
    question,
    query_type,
    cookie,
    user_agent,
    request_params,
    data,
    timeout,
    log,
    request_context,
):
    try:
        return convert(response, raise_on_error=True, request_context=request_context)
    except ConvertError as exc:
        _record_request_outcome(
            request_context,
            attempt_stage="initial",
            outcome="convert_error",
            error_type=type(exc).__name__,
            refresh_reason="robot.parse_error",
        )
        refresh_reason = "robot.parse_error"
        log and _log_with_context(
            "warning",
            "get-robot-data首次解析失败，强制刷新token后重试一次",
            **request_context,
            refresh_reason=refresh_reason,
        )
        refreshed_headers, refreshed_bucket_key = _build_runtime_headers(
            question,
            query_type=query_type,
            cookie=cookie,
            user_agent=user_agent,
            request_params=request_params,
            force_refresh_token=True,
            refresh_reason=refresh_reason,
            cache_policy=CACHE_POLICY_BYPASS,
        )
        refreshed_context = {
            **request_context,
            "token_refresh": "forced",
            "refresh_reason": refresh_reason,
            "bucket": format_token_bucket_label(refreshed_bucket_key),
        }
        log and logger.debug(
            f"刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}"
        )
        response = _request_response(
            method="POST",
            url=ROBOT_DATA_URL,
            headers_dict=refreshed_headers,
            timeout=timeout,
            request_params=request_params,
            json_body=data,
            log=log,
            context=refreshed_context,
            attempt_stage="refresh",
            refresh_reason=refresh_reason,
        )
        try:
            return convert(response, raise_on_error=True, request_context=request_context)
        except ConvertError as refresh_exc:
            _record_request_outcome(
                refreshed_context,
                attempt_stage="refresh",
                outcome="convert_error",
                error_type=type(refresh_exc).__name__,
                refresh_reason=refresh_reason,
            )
            raise


def get_robot_data(**kwargs):
    """获取 condition。"""
    retry = kwargs.get("retry", REQUEST_CONFIG["robot"]["retry"])
    sleep = kwargs.get("sleep", REQUEST_CONFIG["robot"]["sleep"])
    question = kwargs.get("query") or kwargs.get("question")
    log = kwargs.get("log", True)
    strict = kwargs.get("strict", False)
    query_type = kwargs.get("query_type", "stock")
    cookie = kwargs.get("cookie", None)
    user_agent = kwargs.get("user_agent", None)
    request_params = kwargs.get("request_params", {})
    data = {
        "add_info": '{"urp":{"scene":1,"company":1,"business":1},"contentType":"json","searchInfo":true}',
        "perpage": "10",
        "page": 1,
        "source": "Ths_iwencai_Xuangu",
        "log_info": '{"input_type":"click"}',
        "version": "2.0",
        "secondary_intent": query_type,
        "question": question,
    }
    if kwargs.get("pro", False):
        data["iwcpro"] = 1

    with _library_log_scope(log):
        log and _log_with_context("info", "获取condition开始", query=question, query_type=query_type)
        log and logger.debug(
            f"请求参数: data={data}, request_params={_summarize_request_params_for_logging(request_params)}"
        )
        request_id = allocate_request_id("robot")

        def do():
            request_context = _build_request_context(
                question=question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                request_params=request_params,
                log=log,
                target="get-robot-data",
                target_kind="robot",
                request_id=request_id,
            )
            req_headers, bucket_key = _build_runtime_headers(
                question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                request_params=request_params,
                force_refresh_token=False,
                # Live stress runs showed that robot-path reuse can trip
                # initial 401/403 on the same bucket under higher rates.
                # Keep robot requests off the token cache unless we have
                # new evidence that this failure mode is gone.
                cache_policy=CACHE_POLICY_BYPASS,
            )
            request_context["bucket"] = format_token_bucket_label(bucket_key)
            log and _log_with_context("info", "发送请求到get-robot-data", **request_context)
            log and logger.debug(f"请求头(脱敏): {_sanitize_headers_for_logging(req_headers)}")

            try:
                response = _request_response(
                    method="POST",
                    url=ROBOT_DATA_URL,
                    headers_dict=req_headers,
                    timeout=REQUEST_CONFIG["robot"]["timeout"],
                    request_params=request_params,
                    json_body=data,
                    log=log,
                    context=request_context,
                    attempt_stage="initial",
                )
            except rq.exceptions.HTTPError as exc:
                if not _is_auth_http_error(exc):
                    raise
                log and _log_with_context(
                    "warning",
                    "get-robot-data首次鉴权失败，强制刷新token后重试一次",
                    **request_context,
                    refresh_reason="robot.auth_error",
                    status_code=getattr(getattr(exc, "response", None), "status_code", None),
                )
                reset_runtime_http_state(reason="robot.auth_error")
                refreshed_headers, refreshed_bucket_key = _build_runtime_headers(
                    question,
                    query_type=query_type,
                    cookie=cookie,
                    user_agent=user_agent,
                    request_params=request_params,
                    force_refresh_token=True,
                    refresh_reason="robot.auth_error",
                    # Auth retry stays on bypass for the same reason as the
                    # initial robot request: do not recycle the unstable
                    # bucket token back into the robot path.
                    cache_policy=CACHE_POLICY_BYPASS,
                )
                refreshed_context = {
                    **request_context,
                    "token_refresh": "forced",
                    "refresh_reason": "auth_error",
                    "bucket": format_token_bucket_label(refreshed_bucket_key),
                }
                log and logger.debug(
                    f"刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}"
                )
                response = _request_response(
                    method="POST",
                    url=ROBOT_DATA_URL,
                    headers_dict=refreshed_headers,
                    timeout=REQUEST_CONFIG["robot"]["timeout"],
                    request_params=request_params,
                    json_body=data,
                    log=log,
                    context=refreshed_context,
                    attempt_stage="refresh",
                    refresh_reason="robot.auth_error",
                )

            params = _convert_robot_response_with_retry(
                response,
                question=question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                request_params=request_params,
                data=data,
                timeout=REQUEST_CONFIG["robot"]["timeout"],
                log=log,
                request_context=request_context,
            )
            log and _log_with_context(
                "info",
                "获取get_robot_data成功",
                query=question,
                query_type=query_type,
                target="get-robot-data",
                result_keys=",".join(params.keys()) if params else "empty",
            )
            log and logger.debug(f"get_robot_data结果摘要: {_summarize_params_for_logging(params)}")
            return params

        result = while_do(
            do,
            retry=retry,
            sleep=sleep,
            log=log,
            raise_last_exception=strict,
        )
        if result is None:
            log and _log_with_context(
                "error",
                "获取get_robot_data失败",
                query=question,
                query_type=query_type,
                retry_count=retry,
            )
            if strict:
                raise WencaiUnexpectedResponseError("get_robot_data返回None")
        return result


def get_page(url_params, **kwargs):
    """获取每页数据。"""
    retry = kwargs.pop("retry", REQUEST_CONFIG["page"]["retry"])
    sleep = kwargs.pop("sleep", REQUEST_CONFIG["page"]["sleep"])
    log = kwargs.pop("log", False)
    strict = kwargs.pop("strict", False)
    cookie = kwargs.pop("cookie", None)
    user_agent = kwargs.get("user_agent", None)
    find = kwargs.pop("find", None)
    query_type = kwargs.get("query_type", "stock")
    request_params = kwargs.get("request_params", {})
    pro = kwargs.get("pro", False)

    if find is None:
        data = {**url_params, "perpage": 100, "page": 1, **kwargs}
        target_url = LANDING_DATA_URL
        if pro:
            target_url = f"{target_url}?iwcpro=1"
        path = "answer.components.0.data.datas"
    else:
        if isinstance(find, List):
            find = ",".join(find)
        data = {
            **url_params,
            "perpage": 100,
            "page": 1,
            "query_type": query_type,
            "question": find,
            **kwargs,
        }
        target_url = STOCK_PICK_FIND_URL
        path = "data.data.datas"

    with _library_log_scope(log):
        log and _log_with_context(
            "info",
            "分页请求开始",
            page=data.get("page"),
            query=data.get("question") or kwargs.get("query") or kwargs.get("question"),
            query_type=query_type,
            target=target_url,
            find=find,
        )
        request_id = allocate_request_id("page")

        def do():
            page_no = data.get("page", 1)
            question = data.get("question") or kwargs.get("query") or kwargs.get("question")
            request_context = _build_request_context(
                question=question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                request_params=request_params,
                log=log,
                target=target_url,
                target_kind="page",
                request_id=request_id,
            )
            request_context["page"] = page_no
            req_headers, bucket_key = _build_runtime_headers(
                question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                request_params=request_params,
                force_refresh_token=False,
                cache_policy=CACHE_POLICY_REUSE,
            )
            request_context["bucket"] = format_token_bucket_label(bucket_key)
            try:
                response_text = _request_text(
                    method="POST",
                    url=target_url,
                    headers_dict=req_headers,
                    timeout=REQUEST_CONFIG["page"]["timeout"],
                    request_params=request_params,
                    form_data=data,
                    log=log,
                    context=request_context,
                    attempt_stage="initial",
                )
            except rq.exceptions.HTTPError as exc:
                if not _is_auth_http_error(exc):
                    raise
                log and _log_with_context(
                    "warning",
                    "分页请求首次鉴权失败，强制刷新token后重试一次",
                    **request_context,
                    refresh_reason="page.auth_error",
                    status_code=getattr(getattr(exc, "response", None), "status_code", None),
                )
                reset_runtime_http_state(reason="page.auth_error")
                refreshed_headers, refreshed_bucket_key = _build_runtime_headers(
                    question,
                    query_type=query_type,
                    cookie=cookie,
                    user_agent=user_agent,
                    request_params=request_params,
                    force_refresh_token=True,
                    refresh_reason="page.auth_error",
                    cache_policy=CACHE_POLICY_REUSE,
                )
                refreshed_context = {
                    **request_context,
                    "token_refresh": "forced",
                    "refresh_reason": "auth_error",
                    "bucket": format_token_bucket_label(refreshed_bucket_key),
                }
                log and logger.debug(
                    f"刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}"
                )
                response_text = _request_text(
                    method="POST",
                    url=target_url,
                    headers_dict=refreshed_headers,
                    timeout=REQUEST_CONFIG["page"]["timeout"],
                    request_params=request_params,
                    form_data=data,
                    log=log,
                    context=refreshed_context,
                    attempt_stage="refresh",
                    refresh_reason="page.auth_error",
                )

            try:
                result_payload = _load_json_response(response_text)
            except WencaiUnexpectedResponseError as exc:
                _record_request_outcome(
                    request_context,
                    attempt_stage="initial",
                    outcome="parse_error",
                    error_type=type(exc).__name__,
                    refresh_reason="page.parse_error",
                )
                log and _log_with_context(
                    "warning",
                    "分页请求首次解析失败，强制刷新token后重试一次",
                    **request_context,
                    refresh_reason="page.parse_error",
                )
                refreshed_headers, refreshed_bucket_key = _build_runtime_headers(
                    question,
                    query_type=query_type,
                    cookie=cookie,
                    user_agent=user_agent,
                    request_params=request_params,
                    force_refresh_token=True,
                    refresh_reason="page.parse_error",
                    cache_policy=CACHE_POLICY_REUSE,
                )
                refreshed_context = {
                    **request_context,
                    "token_refresh": "forced",
                    "refresh_reason": "parse_error",
                    "bucket": format_token_bucket_label(refreshed_bucket_key),
                }
                log and logger.debug(
                    f"刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}"
                )
                response_text = _request_text(
                    method="POST",
                    url=target_url,
                    headers_dict=refreshed_headers,
                    timeout=REQUEST_CONFIG["page"]["timeout"],
                    request_params=request_params,
                    form_data=data,
                    log=log,
                    context=refreshed_context,
                    attempt_stage="refresh",
                    refresh_reason="page.parse_error",
                )
                try:
                    result_payload = _load_json_response(response_text)
                except WencaiUnexpectedResponseError as refresh_exc:
                    _record_request_outcome(
                        refreshed_context,
                        attempt_stage="refresh",
                        outcome="parse_error",
                        error_type=type(refresh_exc).__name__,
                        refresh_reason="page.parse_error",
                    )
                    raise

            data_list = _extract_data_list(result_payload, path, page_no)
            log and _log_with_context(
                "info",
                "分页请求成功",
                page=page_no,
                query=question,
                query_type=query_type,
                target=target_url,
                rows=len(data_list),
            )
            return pd.DataFrame.from_dict(data_list)

        result = while_do(
            do,
            retry=retry,
            sleep=sleep,
            log=log,
            raise_last_exception=strict,
        )
        if result is None:
            log and _log_with_context(
                "error",
                "分页请求失败",
                page=data.get("page"),
                query=data.get("question") or kwargs.get("query") or kwargs.get("question"),
                query_type=query_type,
                target=target_url,
            )
            if strict:
                raise WencaiUnexpectedResponseError("分页请求返回None")
        return result


def can_loop(loop, count):
    return count < loop


def loop_page(loop, row_count, url_params, **kwargs):
    """循环分页。"""
    count = 0
    strict = kwargs.get("strict", False)
    perpage = kwargs.pop("perpage", 100)
    max_page = math.ceil(row_count / perpage)
    if max_page <= 0:
        return pd.DataFrame()
    result = None
    if "page" not in kwargs:
        kwargs["page"] = 1
    init_page = kwargs["page"]
    loop_count = max_page if loop is True else loop
    while can_loop(loop_count, count):
        kwargs["page"] = init_page + count
        result_page = get_page(url_params, **kwargs)
        if result_page is None:
            if strict:
                raise WencaiUnexpectedResponseError("循环分页过程中页面请求失败")
            return pd.DataFrame()
        count += 1
        if result is None:
            result = result_page
        else:
            result = pd.concat([result, result_page], ignore_index=True)
    return result if result is not None else pd.DataFrame()


def _normalize_get_kwargs(kwargs):
    return {replace_key(key): value for key, value in kwargs.items()}


def _extract_dataframe_from_data(data, log=False):
    if isinstance(data, pd.DataFrame):
        log and logger.info(f"data是DataFrame，直接返回，形状: {data.shape}")
        return data
    if isinstance(data, dict):
        log and logger.info(f"data是字典，尝试提取DataFrame，字典键: {list(data.keys())}")
        for key, value in data.items():
            if isinstance(value, pd.DataFrame):
                log and logger.info(f"从字典中提取到DataFrame，键: {key}，形状: {value.shape}")
                return value
    log and logger.warning("data既不是DataFrame也不是包含DataFrame的字典，返回空DataFrame")
    return pd.DataFrame()


def _fetch_result_dataframe(params, loop=False, log=False, strict=False, **kwargs):
    data = params.get("data")
    url_params = params.get("url_params")
    condition = _.get(data, "condition")

    log and logger.info(
        "get_robot_data返回数据: "
        f"data类型={type(data).__name__}, "
        f"url_params摘要={_summarize_url_params_for_logging(url_params)}, "
        f"condition摘要={_summarize_condition_for_logging(condition)}"
    )
    log and logger.debug(f"get_robot_data返回摘要: {_summarize_params_for_logging(params)}")

    if condition is not None:
        page_kwargs = {**kwargs, **data}
        find = page_kwargs.get("find", None)
        if loop and find is None:
            row_count = params.get("row_count", 0)
            log and logger.info(f"开始循环分页，总条数: {row_count}")
            if not row_count:
                log and logger.info("循环分页总条数为0，直接返回空DataFrame")
                return pd.DataFrame()
            result = loop_page(loop, row_count, url_params, strict=strict, log=log, **page_kwargs)
            if result is None:
                if strict:
                    raise WencaiUnexpectedResponseError("循环分页返回None")
                return pd.DataFrame()
            log and logger.info(f"循环分页完成，返回结果形状: {result.shape}")
            return result

        log and logger.info("开始获取单页数据")
        result = get_page(url_params, strict=strict, log=log, **page_kwargs)
        if result is None:
            if strict:
                raise WencaiUnexpectedResponseError("单页请求返回None")
            return pd.DataFrame()
        log and logger.info(f"获取单页数据完成，返回结果形状: {result.shape}")
        return result

    if kwargs.get("no_detail") is not True:
        return _extract_dataframe_from_data(data, log=log)

    log and logger.info("no_detail=True，返回空DataFrame")
    return pd.DataFrame()


def get(loop=False, **kwargs):
    """获取结果。"""
    kwargs = _normalize_get_kwargs(kwargs)
    log = kwargs.get("log", True)
    strict = kwargs.get("strict", False)
    with _library_log_scope(log):
        try:
            log and logger.info(f"开始执行get函数，查询: {kwargs.get('query')}")
            params = get_robot_data(**kwargs)
            if params is None:
                if strict:
                    raise WencaiUnexpectedResponseError("get_robot_data返回None")
                log and logger.error("get_robot_data返回None")
                return pd.DataFrame()

            fetch_kwargs = dict(kwargs)
            fetch_kwargs.pop("log", None)
            fetch_kwargs.pop("strict", None)
            return _fetch_result_dataframe(
                params,
                loop=loop,
                log=log,
                strict=strict,
                **fetch_kwargs,
            )
        except Exception as exc:
            if strict:
                raise
            log and logger.error(f"get函数执行失败: {exc}", exc_info=True)
            return pd.DataFrame()
