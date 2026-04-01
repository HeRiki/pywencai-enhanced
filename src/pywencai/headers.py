import os
import time
import hashlib
import subprocess
import logging
import re
import sys
from typing import Optional, Tuple

# 使用模块级日志记录器
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)
TOKEN_CACHE_TTL_SECONDS = 300

_NODE_AVAILABLE_CACHE: Optional[Tuple[bool, Optional[str]]] = None
_TOKEN_CACHE = {"value": None, "expires_at": 0.0}
_USER_AGENT_CACHE = {"value": None}

# 添加一个日志记录函数，确保Node.js检测的日志能被记录
def write_log(message, level="INFO"):
    """
    使用logger记录日志，确保Node.js检测的日志能被记录
    """
    try:
        # 使用logger记录日志
        if level == "INFO":
            logger.info(message)
        elif level == "DEBUG":
            logger.debug(message)
        elif level == "ERROR":
            logger.error(message)
        elif level == "WARNING":
            logger.warning(message)
    except Exception as e:
        error_msg = f"记录日志失败: {e}"
        print(error_msg)


def clear_runtime_cache():
    """清理 Node/UA/token 的进程内缓存，供测试和异常恢复使用。"""
    global _NODE_AVAILABLE_CACHE
    _NODE_AVAILABLE_CACHE = None
    _TOKEN_CACHE["value"] = None
    _TOKEN_CACHE["expires_at"] = 0.0
    _USER_AGENT_CACHE["value"] = None

def find_packed_node():
    '''查找打包的Node.js''' 
    try:
        # 获取当前脚本所在目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        executable_path = sys.executable
        executable_dir = os.path.dirname(executable_path)
        
        # 检查几个可能的位置
        possible_paths = []
        
        # 1. 对于PyInstaller单文件模式，检查_MEIPASS根目录
        if '_MEIPASS' in os.environ:
            meipass_dir = os.environ['_MEIPASS']
            # 检查_MEIPASS根目录下的node.exe
            meipass_node_path = os.path.join(meipass_dir, 'node.exe')
            possible_paths.append(meipass_node_path)
        else:
            # _MEIPASS环境变量不存在，但可能是PyInstaller打包的
            if hasattr(sys, 'frozen'):
                # 检查当前脚本目录的父目录
                parent_dir = os.path.dirname(current_dir)
                parent_node_path = os.path.join(parent_dir, 'node.exe')
                possible_paths.append(parent_node_path)
                
                # 检查当前脚本目录的父父目录
                grandparent_dir = os.path.dirname(parent_dir)
                grandparent_node_path = os.path.join(grandparent_dir, 'node.exe')
                possible_paths.append(grandparent_node_path)
        
        # 2. 检查可执行文件所在目录
        executable_dir_node_path = os.path.join(executable_dir, 'node.exe')
        possible_paths.append(executable_dir_node_path)
        
        # 3. 检查当前脚本所在目录
        script_dir_node_path = os.path.join(current_dir, 'node.exe')
        possible_paths.append(script_dir_node_path)
        
        # 4. 检查当前工作目录
        cwd_path = os.path.join(os.getcwd(), 'node.exe')
        possible_paths.append(cwd_path)
        
        # 5. 检查相对路径
        possible_paths.append('./node.exe')
        possible_paths.append('node.exe')
        
        # 6. 检查常见的Node.js安装路径（作为最后的备用）
        common_node_paths = [
            r'C:\Program Files\nodejs\node.exe',
            r'C:\Program Files (x86)\nodejs\node.exe',
            r'%USERPROFILE%\AppData\Local\nodejs\node.exe',
            r'%APPDATA%\npm\node_modules\node\bin\node.exe',
        ]
        for path in common_node_paths:
            # 展开环境变量
            expanded_path = os.path.expandvars(path)
            possible_paths.append(expanded_path)
        
        # 去重，确保每个路径只被检查一次
        unique_paths = []
        for path in possible_paths:
            if path not in unique_paths:
                unique_paths.append(path)
        possible_paths = unique_paths
        
        # 检查每个路径
        for path in possible_paths:
            # 标准化路径，确保Windows路径格式正确
            normalized_path = os.path.normpath(path)
            
            # 检查路径是否存在
            if os.path.exists(normalized_path):
                # 验证是否为可执行文件
                if os.access(normalized_path, os.X_OK):
                    logger.debug(f"找到Node.js: {normalized_path}")
                    return normalized_path
        
        logger.debug("未找到打包的Node.js，所有可能路径都不存在")
        return None
    except Exception as e:
        error_msg = f"查找打包的Node.js时发生异常: {e}"
        logger.error(error_msg, exc_info=True)
        return None

def check_node_available():
    '''检查Node.js是否可用'''
    global _NODE_AVAILABLE_CACHE
    if _NODE_AVAILABLE_CACHE is not None:
        return _NODE_AVAILABLE_CACHE
    try:
        # 首先查找打包的Node.js
        packed_node_path = find_packed_node()
        if packed_node_path:
            result = subprocess.run(
                [packed_node_path, '--version'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',
                timeout=5
            )
            if result.returncode == 0:
                logger.debug(f"打包的Node.js可用，版本: {result.stdout.strip()}")
                _NODE_AVAILABLE_CACHE = (True, packed_node_path)
                return _NODE_AVAILABLE_CACHE
        
        # 如果打包的Node.js不可用，尝试系统Node.js
        result = subprocess.run(
            ['node', '--version'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding='utf-8',
            timeout=5
        )
        if result.returncode == 0:
            logger.debug(f"系统Node.js可用，版本: {result.stdout.strip()}")
            _NODE_AVAILABLE_CACHE = (True, 'node')
            return _NODE_AVAILABLE_CACHE
        else:
            logger.error(f"系统Node.js不可用: {result.stderr}")
            _NODE_AVAILABLE_CACHE = (False, None)
            return _NODE_AVAILABLE_CACHE
    except FileNotFoundError:
        logger.error("系统Node.js未安装或未添加到PATH环境变量中")
        _NODE_AVAILABLE_CACHE = (False, None)
        return _NODE_AVAILABLE_CACHE
    except Exception as e:
        logger.error(f"检查Node.js可用性时发生异常: {e}")
        _NODE_AVAILABLE_CACHE = (False, None)
        return _NODE_AVAILABLE_CACHE


def generate_token_python():
    '''
    使用Python生成token，不依赖Node.js
    注意：简单的Python实现无法模拟JavaScript的复杂逻辑，生成的token可能无效
    '''
    try:
        # 获取当前时间戳，精确到毫秒
        timestamp = int(time.time() * 1000)
        
        # 使用更复杂的哈希算法，模拟原始JavaScript逻辑
        # 结合固定字符串和时间戳生成更接近原始格式的token
        token_str = f"hexin-v{timestamp}hexin"
        token = hashlib.md5(token_str.encode('utf-8')).hexdigest()
        
        # 添加更接近原始格式的前缀
        final_token = f"{token}"
        logger.debug(f"生成Python token: {final_token}")
        logger.warning("注意：Python生成的token可能无效，建议安装Node.js以获取有效token")
        return final_token
    except Exception as e:
        logger.error(f"生成Python token失败: {e}")
        return "default-token"


def get_token(force_refresh=False, ttl_seconds=TOKEN_CACHE_TTL_SECONDS):
    '''获取token'''
    now = time.time()
    if (
        not force_refresh
        and _TOKEN_CACHE["value"]
        and now < float(_TOKEN_CACHE["expires_at"])
    ):
        return _TOKEN_CACHE["value"]
    try:
        # 检查Node.js是否可用
        node_available, node_path = check_node_available()
        
        if node_available:
            # 运行时优先使用 bundle，避免依赖 node_modules/jsdom。
            logger.info(f"Node.js可用，路径: {node_path}，尝试使用Node.js bundle生成token...")
            result = subprocess.run(
                [node_path, os.path.join(os.path.dirname(__file__), 'hexin-v.bundle.js')], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                encoding='utf-8',
                timeout=10  # 添加超时，防止命令卡死
            )
            if result.returncode == 0:
                bundle_token = result.stdout.strip()
                logger.info(f"成功使用Node.js bundle生成token: {bundle_token[:10]}...")
                _TOKEN_CACHE["value"] = bundle_token
                _TOKEN_CACHE["expires_at"] = now + ttl_seconds
                return bundle_token
            else:
                logger.error(f"使用hexin-v.bundle.js生成token失败: {result.stderr}")
                # bundle 失败时，保留对开发脚本的最后兜底，兼容手工调试环境。
                logger.info("尝试使用hexin-v.js生成token...")
                result = subprocess.run(
                    [node_path, os.path.join(os.path.dirname(__file__), 'hexin-v.js')], 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.PIPE, 
                    encoding='utf-8',
                    timeout=10
                )
                if result.returncode == 0:
                    node_token = result.stdout.strip()
                    logger.info(f"成功使用Node.js脚本生成token: {node_token[:10]}...")
                    _TOKEN_CACHE["value"] = node_token
                    _TOKEN_CACHE["expires_at"] = now + ttl_seconds
                    return node_token
                logger.error(f"使用hexin-v.js生成token失败: {result.stderr}")
                logger.warning("Node.js可用但生成token失败，尝试使用Python生成token")
        else:
            logger.warning("Node.js不可用，尝试使用Python生成token")
        
        # 使用Python生成token作为fallback
        python_token = generate_token_python()
        if python_token and python_token != "default-token":
            logger.info(f"使用Python生成的token: {python_token[:10]}...")
            _TOKEN_CACHE["value"] = python_token
            _TOKEN_CACHE["expires_at"] = now + ttl_seconds
            return python_token
        
        # 所有token生成方式失败
        logger.error("所有token生成方式失败，使用默认token")
        logger.error("建议安装Node.js以获取有效token，否则可能无法获取数据")
        return "default-token"
    except Exception as e:
        logger.error(f"获取token时发生异常: {e}")
        logger.error("建议安装Node.js以获取有效token，否则可能无法获取数据")
        # 尝试使用Python生成token作为最后的 fallback
        try:
            fallback_token = generate_token_python()
            logger.debug(f"使用fallback token: {fallback_token[:10]}...")
            _TOKEN_CACHE["value"] = fallback_token
            _TOKEN_CACHE["expires_at"] = now + ttl_seconds
            return fallback_token
        except:
            logger.error("fallback token生成失败")
            return "default-token"


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
    except Exception as exc:
        logger.warning(f"生成随机User-Agent失败，回退默认值: {exc}")
        resolved = DEFAULT_USER_AGENT
    _USER_AGENT_CACHE["value"] = resolved
    return resolved


def sanitize_cookie(cookie):
    if not cookie:
        return cookie
    cookie = cookie.replace('\n', ' ').replace('\r', ' ').strip()
    return re.sub(r'\s+', ' ', cookie)


def headers(cookie=None, user_agent=None, force_refresh_token=False):
    """
    生成请求头，包含token、User-Agent和cookie
    
    Args:
        cookie: cookie字符串
        user_agent: User-Agent字符串
        
    Returns:
        dict: 请求头字典
    """
    user_agent = get_user_agent(user_agent)
    cookie = sanitize_cookie(cookie)
    return {
        'hexin-v': get_token(force_refresh=force_refresh_token),
        'User-Agent': user_agent,
        'cookie': cookie
    }
