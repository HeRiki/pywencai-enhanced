import json
from typing import Any, Dict, List
import math
from urllib.parse import quote

import requests as rq
import pandas as pd
import time
import logging
import pydash as _
from .convert import (
    ConvertError,
    ConvertHttpError,
    convert,
)
from .headers import headers

# 使用根日志记录器，不再使用自定义StreamHandler
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # 设置为DEBUG级别，确保所有日志都能被记录

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
        _SESSION.headers.update({"Connection": "keep-alive"})
    return _SESSION


def clear_runtime_state():
    global _SESSION
    if _SESSION is not None:
        _SESSION.close()
        _SESSION = None


def reset_runtime_http_state():
    """丢弃当前进程内复用的 HTTP 会话，避免在鉴权失败后复用脏状态。"""
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


def _summarize_response_for_logging(text, limit=240):
    text = text or ""
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _is_html_response_text(text):
    compact = (text or "").lstrip().lower()
    return compact.startswith("<!doctype html") or compact.startswith("<html")


def _format_log_context(**kwargs):
    parts = []
    for key, value in kwargs.items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={value}")
    return " | ".join(parts)


def _log_with_context(level, message, **context):
    context_text = _format_log_context(**context)
    full_message = f"{message} | {context_text}" if context_text else message
    getattr(logger, level)(full_message)


def _build_request_headers(question, query_type='stock', cookie=None, user_agent=None, extra_headers=None, force_refresh_token=False):
    req_headers = headers(cookie, user_agent, force_refresh_token=force_refresh_token)
    req_headers.update({
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Origin': 'https://www.iwencai.com',
        'Referer': build_result_referer(question, query_type=query_type),
        'X-Requested-With': 'XMLHttpRequest',
    })
    if extra_headers:
        req_headers.update(extra_headers)
    return req_headers


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
):
    response = (session or get_session()).request(
        method=method,
        url=url,
        json=json_body,
        data=form_data,
        headers=headers_dict,
        timeout=timeout,
        **(request_params or {}),
    )
    response.raise_for_status()
    if log:
        _log_with_context(
            "info",
            '请求返回',
            **{
                **(context or {}),
                "status_code": response.status_code,
                "response_bytes": len(response.text),
            },
        )
        logger.debug(f'响应头: {dict(response.headers)}')
        logger.debug(f'响应内容摘要: {_summarize_response_for_logging(response.text)}')
    return response


def _request_text(**kwargs):
    response = _request_response(**kwargs)
    return response.text


def build_result_referer(question, query_type='stock'):
    """构造更贴近浏览器的问财结果页 Referer。"""
    encoded_question = quote(str(question or ''))
    sign = int(time.time() * 1000)
    return (
        f"{IWENCAI_BASE_URL}/unifiedwap/result?"
        f"w={encoded_question}&querytype={query_type}&sign={sign}"
    )


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
    base_sleep = float(configured_sleep or 0)
    transport_backoff = min(1.0, 0.2 * max(attempt, 1))
    return max(base_sleep, transport_backoff)


def while_do(do, retry=10, sleep=0, log=False):
    """
    重试执行函数，带有详细的错误日志
    
    Args:
        do: 要执行的函数
        retry: 最大重试次数
        sleep: 重试间隔（秒）
        log: 是否记录日志
    """
    import traceback
    count = 0
    while count < retry:
        time.sleep(sleep)
        try:
            return do()
        except rq.exceptions.Timeout as e:
            log and _log_with_context("error", f'{count+1}次尝试失败: 请求超时 - {e}', retry_count=retry, attempt=count + 1)
            if not _should_retry_exception(e):
                break
        except rq.exceptions.ConnectionError as e:
            log and _log_with_context("error", f'{count+1}次尝试失败: 连接错误 - {e}', retry_count=retry, attempt=count + 1)
            if not _should_retry_exception(e):
                break
            reset_runtime_http_state()
            if count + 1 < retry:
                backoff_seconds = _connection_retry_backoff_seconds(count + 1, sleep)
                log and _log_with_context(
                    "warning",
                    '连接错误后重置HTTP会话并等待后重试',
                    retry_count=retry,
                    attempt=count + 1,
                    backoff_seconds=backoff_seconds,
                )
                time.sleep(backoff_seconds)
        except rq.exceptions.HTTPError as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            log and _log_with_context(
                "error",
                f'{count+1}次尝试失败: HTTP错误(status={status_code}) - {e}',
                retry_count=retry,
                attempt=count + 1,
                status_code=status_code,
            )
            if not _should_retry_exception(e):
                break
        except Exception as e:
            log and _log_with_context(
                "error",
                f'{count+1}次尝试失败: {type(e).__name__} - {e}',
                retry_count=retry,
                attempt=count + 1,
                error_type=type(e).__name__,
            )
            log and logger.debug(f'异常堆栈: {traceback.format_exc()}')
            if not _should_retry_exception(e):
                break
        count += 1
    return None


def get_robot_data(**kwargs):
    '''获取condition'''
    retry = kwargs.get('retry', REQUEST_CONFIG["robot"]["retry"])
    sleep = kwargs.get('sleep', REQUEST_CONFIG["robot"]["sleep"])
    # 同时支持query和question参数
    question = kwargs.get('query') or kwargs.get('question')
    log = kwargs.get('log', True)  # 默认开启日志
    query_type = kwargs.get('query_type', 'stock')
    cookie = kwargs.get('cookie', None)
    user_agent = kwargs.get('user_agent', None)
    request_params = kwargs.get('request_params', {})
    data = {
        'add_info': '{"urp":{"scene":1,"company":1,"business":1},"contentType":"json","searchInfo":true}',
        'perpage': '10',
        'page': 1,
        'source': 'Ths_iwencai_Xuangu',
        'log_info': '{"input_type":"click"}',
        'version': '2.0',
        'secondary_intent': query_type,
        'question': question
    }

    pro = kwargs.get('pro', False)

    if pro:
        data['iwcpro'] = 1

    _log_with_context("info", '获取condition开始', query=question, query_type=query_type)
    logger.debug(f'请求参数: data={data}, request_params={request_params}')

    def do():
        try:
            req_headers = _build_request_headers(
                question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                # hexin-v 对 get-robot-data 的容忍度很低，复用缓存 token
                # 容易出现“首次 401/403，强刷后成功”的假失败。
                force_refresh_token=True,
            )
            request_context = {
                "query": question,
                "query_type": query_type,
                "target": 'get-robot-data',
            }
            _log_with_context("info", '发送请求到get-robot-data', **request_context)
            logger.debug(f'请求头(脱敏): {_sanitize_headers_for_logging(req_headers)}')

            try:
                response = _request_response(
                    method='POST',
                    url=ROBOT_DATA_URL,
                    headers_dict=req_headers,
                    timeout=REQUEST_CONFIG["robot"]["timeout"],
                    request_params=request_params,
                    json_body=data,
                    log=True,
                    context=request_context,
                )
            except rq.exceptions.HTTPError as exc:
                if not _is_auth_http_error(exc):
                    raise
                _log_with_context(
                    "warning",
                    'get-robot-data首次鉴权失败，强制刷新token后重试一次',
                    **request_context,
                    status_code=getattr(getattr(exc, "response", None), "status_code", None),
                )
                reset_runtime_http_state()
                refreshed_headers = _build_request_headers(
                    question,
                    query_type=query_type,
                    cookie=cookie,
                    user_agent=user_agent,
                    force_refresh_token=True,
                )
                logger.debug(f'刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}')
                response = _request_response(
                    method='POST',
                    url=ROBOT_DATA_URL,
                    headers_dict=refreshed_headers,
                    timeout=REQUEST_CONFIG["robot"]["timeout"],
                    request_params=request_params,
                    json_body=data,
                    log=True,
                    context={**request_context, "token_refresh": "forced", "refresh_reason": "auth_error"},
                )

            try:
                params = convert(response, raise_on_error=True)
            except ConvertError:
                _log_with_context("warning", 'get-robot-data首次解析失败，强制刷新token后重试一次', **request_context)
                refreshed_headers = _build_request_headers(
                    question,
                    query_type=query_type,
                    cookie=cookie,
                    user_agent=user_agent,
                    force_refresh_token=True,
                )
                logger.debug(f'刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}')
                response = _request_response(
                    method='POST',
                    url=ROBOT_DATA_URL,
                    headers_dict=refreshed_headers,
                    timeout=REQUEST_CONFIG["robot"]["timeout"],
                    request_params=request_params,
                    json_body=data,
                    log=True,
                    context={**request_context, "token_refresh": "forced"},
                )
                params = convert(response, raise_on_error=True)
            _log_with_context(
                "info",
                '获取get_robot_data成功',
                query=question,
                query_type=query_type,
                target='get-robot-data',
                result_keys=','.join(params.keys()) if params else 'empty',
            )
            logger.debug(f'get_robot_data返回完整结果: {params}')
            return params
        except rq.exceptions.Timeout as e:
            logger.error(f'请求超时: {e}')
            raise
        except rq.exceptions.ConnectionError as e:
            logger.error(f'连接错误: {e}')
            raise
        except rq.exceptions.RequestException as e:
            logger.error(f'请求异常: {e}')
            raise
        except Exception as e:
            logger.error(f'处理响应时发生异常: {e}', exc_info=True)
            raise

    result = while_do(do, retry, sleep, log)

    if result is None:
        _log_with_context("error", '获取get_robot_data失败', query=question, query_type=query_type, retry_count=retry)
    
    return result


def replace_key(key):
    '''替换key'''
    key_map = {
        'question': 'query',
        'sort_key': 'urp_sort_index',
        'sort_order': 'urp_sort_way'
    }
    return key_map.get(key, key)


def get_page(url_params, **kwargs):
    '''获取每页数据'''
    retry = kwargs.pop('retry', REQUEST_CONFIG["page"]["retry"])
    sleep = kwargs.pop('sleep', REQUEST_CONFIG["page"]["sleep"])
    log = kwargs.pop('log', False)
    cookie = kwargs.pop('cookie', None)
    user_agent = kwargs.get('user_agent', None)
    find = kwargs.pop('find', None)
    query_type = kwargs.get('query_type', 'stock')
    request_params = kwargs.get('request_params', {})
    pro = kwargs.get('pro', False)
    if find is None:
        data = {
            **url_params,
            'perpage': 100,
            'page': 1,
            **kwargs
        }
        target_url = LANDING_DATA_URL
        if pro:
            target_url = f'{target_url}?iwcpro=1'
        path = 'answer.components.0.data.datas'
    else:
        if isinstance(find, List):
            # 传入股票代码列表时，拼接
            find = ','.join(find)
        data = {
             **url_params,
            'perpage': 100,
            'page': 1,
            'query_type': query_type,
            'question': find,
            **kwargs
        }
        target_url = STOCK_PICK_FIND_URL
        path = 'data.data.datas'
    
    log and _log_with_context(
        "info",
        '分页请求开始',
        page=data.get("page"),
        query=data.get("question") or kwargs.get("query") or kwargs.get("question"),
        query_type=query_type,
        target=target_url,
        find=find,
    )

    def do():
        page_no = data.get("page", 1)
        question = data.get('question') or kwargs.get('query') or kwargs.get('question')
        request_context = {
            "page": page_no,
            "query": question,
            "query_type": query_type,
            "target": target_url,
        }
        req_headers = _build_request_headers(
            question,
            query_type=query_type,
            cookie=cookie,
            user_agent=user_agent,
            # 分页主请求同样优先使用新 token，避免每次第一页先鉴权失败。
            force_refresh_token=True,
        )
        try:
            response_text = _request_text(
                method='POST',
                url=target_url,
                headers_dict=req_headers,
                timeout=REQUEST_CONFIG["page"]["timeout"],
                request_params=request_params,
                form_data=data,
                log=log,
                context=request_context,
            )
        except rq.exceptions.HTTPError as exc:
            if not _is_auth_http_error(exc):
                raise
            log and _log_with_context(
                "warning",
                '分页请求首次鉴权失败，强制刷新token后重试一次',
                **request_context,
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
            )
            reset_runtime_http_state()
            refreshed_headers = _build_request_headers(
                question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                force_refresh_token=True,
            )
            logger.debug(f'刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}')
            response_text = _request_text(
                method='POST',
                url=target_url,
                headers_dict=refreshed_headers,
                timeout=REQUEST_CONFIG["page"]["timeout"],
                request_params=request_params,
                form_data=data,
                log=log,
                context={**request_context, "token_refresh": "forced", "refresh_reason": "auth_error"},
            )
        try:
            result_do = _load_json_response(response_text)
        except WencaiUnexpectedResponseError:
            log and _log_with_context("warning", '分页请求首次解析失败，强制刷新token后重试一次', **request_context)
            refreshed_headers = _build_request_headers(
                question,
                query_type=query_type,
                cookie=cookie,
                user_agent=user_agent,
                force_refresh_token=True,
            )
            logger.debug(f'刷新token后的请求头(脱敏): {_sanitize_headers_for_logging(refreshed_headers)}')
            response_text = _request_text(
                method='POST',
                url=target_url,
                headers_dict=refreshed_headers,
                timeout=REQUEST_CONFIG["page"]["timeout"],
                request_params=request_params,
                form_data=data,
                log=log,
                context={**request_context, "token_refresh": "forced"},
            )
            result_do = _load_json_response(response_text)
        data_list = _extract_data_list(result_do, path, page_no)
        log and _log_with_context(
            "info",
            '分页请求成功',
            page=page_no,
            query=question,
            query_type=query_type,
            target=target_url,
            rows=len(data_list),
        )
        return pd.DataFrame.from_dict(data_list)
    
    result = while_do(do, retry, sleep, log)

    if result is None:
        log and _log_with_context(
            "error",
            '分页请求失败',
            page=data.get("page"),
            query=data.get("question") or kwargs.get("query") or kwargs.get("question"),
            query_type=query_type,
            target=target_url,
        )

    return result


def can_loop(loop, count):
    return count < loop


def loop_page(loop, row_count, url_params, **kwargs):
    '''循环分页'''
    count = 0
    perpage = kwargs.pop('perpage', 100)
    max_page = math.ceil(row_count / perpage)
    result = None
    if 'page' not in kwargs:
        kwargs['page'] = 1
    initPage = kwargs['page']
    loop_count = max_page if loop is True else loop
    while can_loop(loop_count, count):
        kwargs['page'] = initPage + count
        resultPage = get_page(url_params, **kwargs)
        count = count + 1
        if result is None:
            result = resultPage
        else:
            result = pd.concat([result, resultPage], ignore_index=True)

    return result


def _normalize_get_kwargs(kwargs):
    return {replace_key(key): value for key, value in kwargs.items()}


def _extract_dataframe_from_data(data, log=False):
    if isinstance(data, pd.DataFrame):
        log and logger.info(f'data是DataFrame，直接返回，形状: {data.shape}')
        return data
    if isinstance(data, dict):
        log and logger.info(f'data是字典，尝试提取DataFrame，字典键: {list(data.keys())}')
        for key, value in data.items():
            if isinstance(value, pd.DataFrame):
                log and logger.info(f'从字典中提取到DataFrame，键: {key}，形状: {value.shape}')
                return value
    log and logger.warning('data既不是DataFrame也不是包含DataFrame的字典，返回空DataFrame')
    return pd.DataFrame()


def _fetch_result_dataframe(params, loop=False, log=False, **kwargs):
    data = params.get('data')
    url_params = params.get('url_params')
    condition = _.get(data, 'condition')

    log and logger.info(
        f'get_robot_data返回数据: data类型={type(data)}, url_params={url_params}, condition={condition}'
    )
    log and logger.debug(f'get_robot_data完整返回: {params}')

    if condition is not None:
        page_kwargs = {**kwargs, **data}
        find = page_kwargs.get('find', None)
        if loop and find is None:
            row_count = params.get('row_count', 0)
            log and logger.info(f'开始循环分页，总条数: {row_count}')
            result = loop_page(loop, row_count, url_params, **page_kwargs)
            log and logger.info(f'循环分页完成，返回结果形状: {result.shape if result is not None else "None"}')
            return result

        log and logger.info('开始获取单页数据')
        result = get_page(url_params, **page_kwargs)
        log and logger.info(f'获取单页数据完成，返回结果形状: {result.shape if result is not None else "None"}')
        return result

    no_detail = kwargs.get('no_detail')
    if no_detail is not True:
        return _extract_dataframe_from_data(data, log=log)

    log and logger.info('no_detail=True，返回空DataFrame')
    return pd.DataFrame()


def get(loop=False, **kwargs):
    '''获取结果'''
    try:
        kwargs = _normalize_get_kwargs(kwargs)
        log = kwargs.get('log', True)  # 默认开启日志
        log and logger.info(f'开始执行get函数，查询: {kwargs.get("query")}')
        
        params = get_robot_data(**kwargs)
        
        # 确保params不为None
        if params is None:
            log and logger.error(f'get_robot_data返回None')
            return pd.DataFrame()
        fetch_kwargs = dict(kwargs)
        fetch_kwargs.pop("log", None)
        return _fetch_result_dataframe(params, loop=loop, log=log, **fetch_kwargs)
    except Exception as e:
        # 捕获所有异常，确保函数不会崩溃
        logger.error(f'get函数执行失败: {e}', exc_info=True)
        return pd.DataFrame()
