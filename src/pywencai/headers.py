import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

# 使用模块级日志记录器
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)
TOKEN_CACHE_TTL_SECONDS = 300
TOKEN_CACHE_MAX_BUCKETS = 32

_NODE_AVAILABLE_CACHE: Optional[Tuple[bool, Optional[str]]] = None
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}
_USER_AGENT_CACHE = {"value": None}


def write_log(message, level="INFO"):
    """使用 logger 记录日志，兼容宿主应用的统一输出。"""
    try:
        if level == "INFO":
            logger.info(message)
        elif level == "DEBUG":
            logger.debug(message)
        elif level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
    except Exception as exc:  # pragma: no cover - 日志兜底
        print(f"记录日志失败: {exc}")


def clear_runtime_cache():
    """清理 Node/UA/token 的进程内缓存，供测试和异常恢复使用。"""
    global _NODE_AVAILABLE_CACHE
    _NODE_AVAILABLE_CACHE = None
    _TOKEN_CACHE.clear()
    _USER_AGENT_CACHE["value"] = None


def find_packed_node():
    """查找打包的 Node.js。"""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        executable_path = sys.executable
        executable_dir = os.path.dirname(executable_path)

        possible_paths = []
        if "_MEIPASS" in os.environ:
            meipass_dir = os.environ["_MEIPASS"]
            possible_paths.append(os.path.join(meipass_dir, "node.exe"))
        elif hasattr(sys, "frozen"):
            parent_dir = os.path.dirname(current_dir)
            possible_paths.append(os.path.join(parent_dir, "node.exe"))
            grandparent_dir = os.path.dirname(parent_dir)
            possible_paths.append(os.path.join(grandparent_dir, "node.exe"))

        possible_paths.extend(
            [
                os.path.join(executable_dir, "node.exe"),
                os.path.join(current_dir, "node.exe"),
                os.path.join(os.getcwd(), "node.exe"),
                "./node.exe",
                "node.exe",
            ]
        )

        common_node_paths = [
            r"C:\Program Files\nodejs\node.exe",
            r"C:\Program Files (x86)\nodejs\node.exe",
            r"%USERPROFILE%\AppData\Local\nodejs\node.exe",
            r"%APPDATA%\npm\node_modules\node\bin\node.exe",
        ]
        for path in common_node_paths:
            possible_paths.append(os.path.expandvars(path))

        unique_paths = []
        for path in possible_paths:
            if path not in unique_paths:
                unique_paths.append(path)

        for path in unique_paths:
            normalized_path = os.path.normpath(path)
            if os.path.exists(normalized_path) and os.access(normalized_path, os.X_OK):
                logger.debug(f"找到Node.js: {normalized_path}")
                return normalized_path

        logger.debug("未找到打包的Node.js，所有可能路径都不存在")
        return None
    except Exception as exc:  # pragma: no cover - 运行环境相关
        logger.error(f"查找打包的Node.js时发生异常: {exc}", exc_info=True)
        return None


def check_node_available():
    """检查 Node.js 是否可用。"""
    global _NODE_AVAILABLE_CACHE
    if _NODE_AVAILABLE_CACHE is not None:
        return _NODE_AVAILABLE_CACHE
    try:
        packed_node_path = find_packed_node()
        if packed_node_path:
            result = subprocess.run(
                [packed_node_path, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                timeout=5,
            )
            if result.returncode == 0:
                logger.debug(f"打包的Node.js可用，版本: {result.stdout.strip()}")
                _NODE_AVAILABLE_CACHE = (True, packed_node_path)
                return _NODE_AVAILABLE_CACHE

        result = subprocess.run(
            ["node", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            timeout=5,
        )
        if result.returncode == 0:
            logger.debug(f"系统Node.js可用，版本: {result.stdout.strip()}")
            _NODE_AVAILABLE_CACHE = (True, "node")
            return _NODE_AVAILABLE_CACHE

        logger.error(f"系统Node.js不可用: {result.stderr}")
        _NODE_AVAILABLE_CACHE = (False, None)
        return _NODE_AVAILABLE_CACHE
    except FileNotFoundError:
        logger.error("系统Node.js未安装或未添加到PATH环境变量中")
        _NODE_AVAILABLE_CACHE = (False, None)
        return _NODE_AVAILABLE_CACHE
    except Exception as exc:  # pragma: no cover - 运行环境相关
        logger.error(f"检查Node.js可用性时发生异常: {exc}")
        _NODE_AVAILABLE_CACHE = (False, None)
        return _NODE_AVAILABLE_CACHE


def generate_token_python():
    """
    使用 Python 生成 token。

    注意：简单的 Python 实现无法模拟 JavaScript 的复杂逻辑，生成的 token 可能无效。
    """
    try:
        timestamp = int(time.time() * 1000)
        token_str = f"hexin-v{timestamp}hexin"
        token = hashlib.md5(token_str.encode("utf-8")).hexdigest()
        final_token = f"{token}"
        logger.debug(f"生成Python token: {final_token}")
        logger.warning("注意：Python生成的token可能无效，建议安装Node.js以获取有效token")
        return final_token
    except Exception as exc:  # pragma: no cover - 理论上很难触发
        logger.error(f"生成Python token失败: {exc}")
        return "default-token"


def sanitize_cookie(cookie):
    if not cookie:
        return cookie
    cookie = cookie.replace("\n", " ").replace("\r", " ").strip()
    return re.sub(r"\s+", " ", cookie)


def get_user_agent(user_agent=None):
    """获取进程级稳定 User-Agent。"""
    if user_agent:
        return user_agent
    if _USER_AGENT_CACHE["value"]:
        return _USER_AGENT_CACHE["value"]
    try:
        from fake_useragent import UserAgent

        ua = UserAgent()
        resolved = ua.random
    except Exception as exc:  # pragma: no cover - 第三方环境相关
        logger.warning(f"生成随机User-Agent失败，回退默认值: {exc}")
        resolved = DEFAULT_USER_AGENT
    _USER_AGENT_CACHE["value"] = resolved
    return resolved


def _canonicalize_proxy_identity(request_params=None):
    proxies = (request_params or {}).get("proxies")
    if not proxies:
        return "direct"
    if isinstance(proxies, str):
        return proxies.strip() or "direct"
    if isinstance(proxies, dict):
        parts = []
        for key in sorted(proxies):
            value = proxies[key]
            if value is None:
                continue
            text = str(value).strip()
            if text:
                parts.append(f"{key}={text}")
        return ";".join(parts) or "direct"
    return str(proxies).strip() or "direct"


def _build_token_bucket_key(cookie=None, user_agent=None, request_params=None):
    raw = "||".join(
        [
            sanitize_cookie(cookie) or "",
            user_agent or "",
            _canonicalize_proxy_identity(request_params),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def format_token_bucket_label(bucket_key):
    return str(bucket_key or "")[:8] or "unknown"


def _purge_expired_token_buckets(now=None):
    now = now if now is not None else time.time()
    expired_keys = [
        key
        for key, entry in list(_TOKEN_CACHE.items())
        if now >= float(entry.get("expires_at", 0.0))
    ]
    for key in expired_keys:
        _TOKEN_CACHE.pop(key, None)


def _prune_token_bucket_cache():
    if len(_TOKEN_CACHE) <= TOKEN_CACHE_MAX_BUCKETS:
        return
    ordered_keys = sorted(
        _TOKEN_CACHE,
        key=lambda key: (
            float(_TOKEN_CACHE[key].get("expires_at", 0.0)),
            float(_TOKEN_CACHE[key].get("updated_at", 0.0)),
        ),
    )
    while len(_TOKEN_CACHE) > TOKEN_CACHE_MAX_BUCKETS and ordered_keys:
        _TOKEN_CACHE.pop(ordered_keys.pop(0), None)


def _set_cached_token(bucket_key, token, expires_at):
    _TOKEN_CACHE[bucket_key] = {
        "value": token,
        "expires_at": float(expires_at),
        "updated_at": time.time(),
    }
    _prune_token_bucket_cache()


def _get_cached_token(bucket_key, now):
    entry = _TOKEN_CACHE.get(bucket_key)
    if not entry:
        return None
    if now >= float(entry.get("expires_at", 0.0)):
        _TOKEN_CACHE.pop(bucket_key, None)
        return None
    entry["updated_at"] = now
    return entry.get("value")


def get_token(
    force_refresh=False,
    ttl_seconds=TOKEN_CACHE_TTL_SECONDS,
    bucket_key=None,
):
    """获取 token。"""
    now = time.time()
    resolved_bucket_key = bucket_key or _build_token_bucket_key(
        cookie=None,
        user_agent=DEFAULT_USER_AGENT,
        request_params=None,
    )
    _purge_expired_token_buckets(now)
    if not force_refresh:
        cached_token = _get_cached_token(resolved_bucket_key, now)
        if cached_token:
            logger.debug(
                f"命中token缓存: bucket={format_token_bucket_label(resolved_bucket_key)}, "
                f"ttl_remaining={float(_TOKEN_CACHE[resolved_bucket_key]['expires_at']) - now:.2f}"
            )
            return cached_token

    try:
        node_available, node_path = check_node_available()
        if node_available:
            logger.info(
                f"Node.js可用，路径: {node_path}，尝试使用Node.js bundle生成token... "
                f"bucket={format_token_bucket_label(resolved_bucket_key)}"
            )
            result = subprocess.run(
                [node_path, os.path.join(os.path.dirname(__file__), "hexin-v.bundle.js")],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                timeout=10,
            )
            if result.returncode == 0:
                bundle_token = result.stdout.strip()
                logger.info(
                    f"成功使用Node.js bundle生成token: {bundle_token[:10]}..., "
                    f"bucket={format_token_bucket_label(resolved_bucket_key)}"
                )
                _set_cached_token(resolved_bucket_key, bundle_token, now + ttl_seconds)
                return bundle_token

            logger.error(f"使用hexin-v.bundle.js生成token失败: {result.stderr}")
            logger.info("尝试使用hexin-v.js生成token...")
            result = subprocess.run(
                [node_path, os.path.join(os.path.dirname(__file__), "hexin-v.js")],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
                timeout=10,
            )
            if result.returncode == 0:
                node_token = result.stdout.strip()
                logger.info(
                    f"成功使用Node.js脚本生成token: {node_token[:10]}..., "
                    f"bucket={format_token_bucket_label(resolved_bucket_key)}"
                )
                _set_cached_token(resolved_bucket_key, node_token, now + ttl_seconds)
                return node_token

            logger.error(f"使用hexin-v.js生成token失败: {result.stderr}")
            logger.warning("Node.js可用但生成token失败，尝试使用Python生成token")
        else:
            logger.warning("Node.js不可用，尝试使用Python生成token")

        python_token = generate_token_python()
        if python_token and python_token != "default-token":
            logger.info(
                f"使用Python生成的token: {python_token[:10]}..., "
                f"bucket={format_token_bucket_label(resolved_bucket_key)}"
            )
            _set_cached_token(resolved_bucket_key, python_token, now + ttl_seconds)
            return python_token

        logger.error("所有token生成方式失败，使用默认token")
        logger.error("建议安装Node.js以获取有效token，否则可能无法获取数据")
        return "default-token"
    except Exception as exc:  # pragma: no cover - 外部环境相关
        logger.error(f"获取token时发生异常: {exc}")
        logger.error("建议安装Node.js以获取有效token，否则可能无法获取数据")
        try:
            fallback_token = generate_token_python()
            logger.debug(f"使用fallback token: {fallback_token[:10]}...")
            _set_cached_token(resolved_bucket_key, fallback_token, now + ttl_seconds)
            return fallback_token
        except Exception:  # pragma: no cover - 极端兜底
            logger.error("fallback token生成失败")
            return "default-token"


def build_auth_headers(
    cookie=None,
    user_agent=None,
    request_params=None,
    force_refresh_token=False,
):
    """
    生成认证请求头，并返回对应的 token bucket key。

    Returns:
        tuple[dict, str]: 请求头字典与 token bucket key
    """
    resolved_user_agent = get_user_agent(user_agent)
    sanitized_cookie = sanitize_cookie(cookie)
    bucket_key = _build_token_bucket_key(
        cookie=sanitized_cookie,
        user_agent=resolved_user_agent,
        request_params=request_params,
    )
    return (
        {
            "hexin-v": get_token(
                force_refresh=force_refresh_token,
                bucket_key=bucket_key,
            ),
            "User-Agent": resolved_user_agent,
            "cookie": sanitized_cookie,
        },
        bucket_key,
    )


def build_result_referer(question, query_type="stock"):
    """构造更贴近浏览器的问财结果页 Referer。"""
    encoded_question = quote(str(question or ""))
    sign = int(time.time() * 1000)
    return (
        "https://www.iwencai.com/unifiedwap/result?"
        f"w={encoded_question}&querytype={query_type}&sign={sign}"
    )


def build_request_headers(
    question,
    query_type="stock",
    cookie=None,
    user_agent=None,
    request_params=None,
    extra_headers=None,
    force_refresh_token=False,
):
    """
    构造完整请求头，并返回对应的 token bucket key。

    Returns:
        tuple[dict, str]: 请求头字典与 token bucket key
    """
    req_headers, bucket_key = build_auth_headers(
        cookie=cookie,
        user_agent=user_agent,
        request_params=request_params,
        force_refresh_token=force_refresh_token,
    )
    req_headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Origin": "https://www.iwencai.com",
            "Referer": build_result_referer(question, query_type=query_type),
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    if extra_headers:
        req_headers.update(extra_headers)
    return req_headers, bucket_key


def headers(cookie=None, user_agent=None, force_refresh_token=False):
    """
    兼容旧接口：仅返回基础认证头。
    """
    req_headers, _bucket_key = build_auth_headers(
        cookie=cookie,
        user_agent=user_agent,
        request_params=None,
        force_refresh_token=force_refresh_token,
    )
    return req_headers
