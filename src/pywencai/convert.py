import pandas as pd
import json
import pydash as _
import requests as rq
import logging
import re
from urllib.parse import urlparse, parse_qs
from .headers import headers

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

UNKNOWN_SHOW_TYPE_COUNTS = {}
DEFAULT_NESTED_MAX_DEPTH = 3


class ConvertError(Exception):
    """问财 get-robot-data 解析异常基类。"""


class ConvertHttpError(ConvertError):
    """HTTP 层异常。"""


class ConvertEmptyResponseError(ConvertError):
    """响应内容为空。"""


class ConvertInvalidJsonError(ConvertError):
    """顶层 JSON 非法。"""


class ConvertMissingDataError(ConvertError):
    """顶层缺少 data 字段。"""


class ConvertInvalidContentError(ConvertError):
    """content 缺失或不是有效 JSON。"""


class ConvertMissingComponentsError(ConvertError):
    """content 内缺少 components。"""


def _response_snippet(text, limit=500):
    value = (text or "").strip()
    return value[:limit]


def _extract_root_payload(result):
    if not isinstance(result, dict):
        raise ConvertMissingDataError(f'响应顶层不是字典类型: type={type(result).__name__}')
    if 'data' in result:
        return result['data']
    if 'result' in result and isinstance(result['result'], dict):
        return result['result']
    raise ConvertMissingDataError(f'响应结构不正确，缺少data字段。响应keys: {list(result.keys())}')


def _iter_tab_items(comp):
    for tab in comp.get('tab_list') or []:
        tab_name = tab.get('tab_name')
        tab_list = tab.get('list') or []
        if tab_name is None:
            continue
        yield tab_name, tab_list


def _build_tab_result(tab_name, tab_list, resolver):
    tab_result = {}
    for tcomp in tab_list:
        show_type = tcomp.get('show_type')
        if not show_type:
            continue
        tab_result[show_type] = resolver(tcomp, tab_list)
    return tab_result

def get_url(url, depth=0, max_depth=DEFAULT_NESTED_MAX_DEPTH):
    if depth >= max_depth:
        logger.warning(f'获取嵌套问财数据已达到最大深度: url={url}, depth={depth}, max_depth={max_depth}')
        return None
    try:
        res = rq.request(
            method='GET',
            url=f'https://www.iwencai.com{url}',
            headers=headers(),
            timeout=10  # 添加超时，防止请求卡死
        )
        res.raise_for_status()  # 检查HTTP状态码
        result = json.loads(res.text)
        return result.get('data')
    except Exception as e:
        logger.warning(f'获取嵌套问财数据失败: url={url}, error={e}')
        return None

def xuangu_tableV1_handler(comp, comps):
    '''xuangu_tableV1类型'''
    return {
        'condition': _.get(comp, 'data.meta.extra.condition'),
        'comp_id': comp['cid'],
        'uuid': comp['puuid']
    }

def common_handler(comp, comps):
    '''common类型'''
    datas = _.get(comp, 'data.datas')
    if isinstance(datas, list):
        return pd.DataFrame.from_dict(datas)
    else:
        return _.get(comp, 'data')

def container_handler(comp, comps):
    '''container类型'''
    result = {}
    for uuid in _.get(comp, 'config.children', []):
        child = _.find(comps, lambda c: c.get('uuid') == uuid)
        key = _.get(child, 'show_type')
        if key is not None and key != '':
            result[key] = show_type_handler(child, comps)
    return result

def txt_handler(comp, comps):
    '''txt类型'''
    content = _.get(comp, 'data.content')
    return content

def tab4_handler(comp, comps):
    '''tab4类型'''
    result = {}
    for tab_name, tab_list in _iter_tab_items(comp):
        result[tab_name] = _build_tab_result(
            tab_name,
            tab_list,
            lambda tcomp, current_tab_list: show_type_handler(tcomp, current_tab_list),
        )
    return result

def tab1_handler(comp, comps):
    '''tab1类型'''
    result = {}
    data = comp.get('data')
    for tab_name, tab_list in _iter_tab_items(comp):
        def resolve_tab_item(tcomp, current_tab_list):
            data_index = tcomp.get('data_index')
            tcomp_copy = dict(tcomp)
            if isinstance(data, dict):
                tcomp_copy['data'] = data.get(data_index)
            else:
                tcomp_copy['data'] = None
            return show_type_handler(tcomp_copy, current_tab_list)

        result[tab_name] = _build_tab_result(tab_name, tab_list, resolve_tab_item)
    return result

def dragon_tiger_stock_handler(comp, comps):
    '''龙虎榜分析'''
    result ={}
    data = _.get(comp, 'data.datas.0')
    detail = data.pop('detail', None)
    
    result['data'] = pd.DataFrame.from_dict([data])
    if detail is not None:
        result['detail'] = {
            'buy': pd.DataFrame.from_dict(_.get(detail[0], 'buy.datas')),
            'sell': pd.DataFrame.from_dict(_.get(detail[0], 'sell.datas'))
        }
    return result

def wiki1_handler(comp, comps):
    depth = int(comp.get('_nested_depth', 0) or 0)
    max_depth = int(comp.get('_nested_max_depth', DEFAULT_NESTED_MAX_DEPTH) or DEFAULT_NESTED_MAX_DEPTH)
    url = _.get(comp, 'data.url')
    if url is not None:
        wcomp = get_url(url, depth=depth, max_depth=max_depth)
        if wcomp is not None:
            if isinstance(wcomp, dict):
                wcomp = {**wcomp, '_nested_depth': depth + 1, '_nested_max_depth': max_depth}
            return show_type_handler(wcomp, comps)
        else:
            return None
    return None

def textblocklinkone_handler(comp, comps):
    data = _.get(comp, 'data.result.data')
    return pd.DataFrame.from_dict(data)

def nestedblocks_handler(comp, comps):
    '''股东户数分析'''
    depth = int(comp.get('_nested_depth', 0) or 0)
    max_depth = int(comp.get('_nested_max_depth', DEFAULT_NESTED_MAX_DEPTH) or DEFAULT_NESTED_MAX_DEPTH)
    subBlocks = _.get(comp, 'data.result.subBlocks.0.subBlocks')
    result = []
    for sub in subBlocks:
        url = sub.get('url')
        sub_comp = get_url(url, depth=depth, max_depth=max_depth)
        if sub_comp is not None:
            if isinstance(sub_comp, dict):
                sub_comp = {**sub_comp, '_nested_depth': depth + 1, '_nested_max_depth': max_depth}
            result.append(show_type_handler(sub_comp, comps))
        else:
            logger.warning(f'nestedblocks 子块解析失败，已跳过: url={url}, depth={depth}, max_depth={max_depth}')
    return result




show_type_handler_dict = {
    'common': common_handler,
    'container': container_handler,
    'txt1': txt_handler,
    'txt2': txt_handler,
    'tab4': tab4_handler,
    'dragon_tiger_stock': dragon_tiger_stock_handler,
    'tab1': tab1_handler,
    # 'wiki1': wiki1_handler,
    'textblocklinkone': textblocklinkone_handler,
    'nestedblocks': nestedblocks_handler
}


show_type_handler_alias_dict = {
    'txt': 'txt1',
    'list': 'common',
    'line': 'common',
    'bar': 'common',
    'kline': 'common',
}


def _resolve_special_handler_key_from_structure(comp):
    if not isinstance(comp, dict):
        return None

    children = _.get(comp, 'config.children')
    if isinstance(children, list):
        return 'container'

    tab_list = comp.get('tab_list')
    if isinstance(tab_list, list):
        if isinstance(comp.get('data'), dict):
            for _tab_name, current_tab_list in _iter_tab_items(comp):
                for tab_comp in current_tab_list:
                    if isinstance(tab_comp, dict) and tab_comp.get('data_index') is not None:
                        return 'tab1'
        return 'tab4'

    sub_blocks = _.get(comp, 'data.result.subBlocks')
    if isinstance(sub_blocks, list):
        return 'nestedblocks'

    if _.get(comp, 'data.result.data') is not None:
        return 'textblocklinkone'

    first_data = _.get(comp, 'data.datas.0')
    if isinstance(first_data, dict) and 'detail' in first_data:
        detail = first_data.get('detail')
        if detail is None or isinstance(detail, list):
            return 'dragon_tiger_stock'

    content = _.get(comp, 'data.content')
    if content is not None and not isinstance(content, (dict, list)):
        return 'txt1'

    return None


def _can_use_common_handler_from_structure(comp):
    return isinstance(comp, dict) and 'data' in comp


def _describe_component_shape(comp):
    if not isinstance(comp, dict):
        return f'type={type(comp).__name__}'

    top_keys = sorted(comp.keys())
    data_value = comp.get('data')
    data_keys = sorted(data_value.keys()) if isinstance(data_value, dict) else []
    return (
        f'top_keys={top_keys}, '
        f'has_data={"data" in comp}, '
        f'data_type={type(data_value).__name__ if data_value is not None else "None"}, '
        f'data_keys={data_keys}, '
        f'has_tab_list={isinstance(comp.get("tab_list"), list)}, '
        f'has_children={isinstance(_.get(comp, "config.children"), list)}'
    )


def _resolve_show_type_key(show_type):
    if not show_type:
        return None
    if show_type in show_type_handler_dict:
        return show_type

    base_show_type = re.sub(r'\d+$', '', str(show_type))
    if base_show_type in show_type_handler_dict:
        return base_show_type

    alias_show_type = show_type_handler_alias_dict.get(base_show_type)
    if alias_show_type in show_type_handler_dict:
        return alias_show_type

    return None


def get_show_type_handler(show_type, comp=None):
    resolved_show_type = _resolve_special_handler_key_from_structure(comp)
    if resolved_show_type is None:
        resolved_show_type = _resolve_show_type_key(show_type)
    if resolved_show_type is not None:
        return show_type_handler_dict[resolved_show_type]
    if _can_use_common_handler_from_structure(comp):
        return common_handler

    if show_type:
        UNKNOWN_SHOW_TYPE_COUNTS[show_type] = UNKNOWN_SHOW_TYPE_COUNTS.get(show_type, 0) + 1
        count = UNKNOWN_SHOW_TYPE_COUNTS[show_type]
        if count == 1:
            logger.warning(
                '未识别且无法按结构解析的show_type，回退common_handler并静默后续重复日志: '
                f'show_type={show_type}, shape={_describe_component_shape(comp)}'
            )
        else:
            logger.debug(
                '未识别且无法按结构解析的show_type重复出现，继续回退common_handler: '
                f'show_type={show_type}, count={count}, shape={_describe_component_shape(comp)}'
            )
    return common_handler


def show_type_handler(comp, comps):
    '''处理每种不同的show_type类型'''
    show_type = comp.get('show_type')
    handler = get_show_type_handler(show_type, comp=comp)
    return handler(comp, comps)

def get_key(comp):
    '''获取每一项的key'''
    h1 = _.get(comp, 'title_config.data.h1') or _.get(comp, 'config.title') or _.get(comp, 'show_type')
    return h1

def multi_show_type_handler(components):
    '''处理多个show_type类型的数据'''
    result = {}
    for comp in components:
        key = get_key(comp)
        value = show_type_handler(comp, components)
        if key is not None and key != '' and value is not None:
            result[key] = value

    return result

def parse_url_params(url):
    """Parse URL parameters into a dictionary"""
    if not url:
        return {}
    
    # Parse the URL
    parsed_url = urlparse(url)
    
    # Extract query parameters
    query_params = parse_qs(parsed_url.query)
    
    # Convert values from lists to single values if list has only one item
    for key, value in query_params.items():
        if isinstance(value, list) and len(value) == 1:
            query_params[key] = value[0]
            
    return query_params

def _parse_robot_response(res):
    if not res.text or len(res.text.strip()) == 0:
        raise ConvertEmptyResponseError(f'响应内容为空，状态码: {getattr(res, "status_code", "unknown")}')

    try:
        result = json.loads(res.text)
        logger.debug(f'原始响应内容: {_response_snippet(res.text, 1000)}...')
    except json.JSONDecodeError as exc:
        raise ConvertInvalidJsonError(f'JSON解析失败: {exc}; snippet={_response_snippet(res.text)}') from exc

    root_payload = _extract_root_payload(result)
    content = _.get(root_payload, 'answer.0.txt.0.content')
    if content is None:
        raise ConvertInvalidContentError('content 缺失')

    if isinstance(content, str):
        logger.debug(f'解析出的content: {_response_snippet(content)}...')
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ConvertInvalidContentError(
                f'content字符串JSON解析失败: {exc}; snippet={_response_snippet(content)}'
            ) from exc
    else:
        logger.debug(f'解析出的content: {content}')

    if not isinstance(content, dict):
        raise ConvertInvalidContentError(f'content 不是字典类型: type={type(content).__name__}')

    components = _.get(content, 'components')
    if not isinstance(components, list):
        raise ConvertMissingComponentsError(f'components不是列表类型，类型: {type(components).__name__}')
    if not components:
        raise ConvertMissingComponentsError('响应中没有components')

    return components


def convert(res, raise_on_error=False):
    """
    处理get_robot_data的结果
    
    Args:
        res: HTTP响应对象
        
    Returns:
        dict: 解析后的参数字典
    """
    try:
        # 首先检查响应状态码
        res.raise_for_status()
        components = _parse_robot_response(res)
        
        params = {}
        url = None
        
        if components:
            if (len(components) == 1 and _.get(components[0], 'show_type') == 'xuangu_tableV1'):
                url = _.get(components[0], 'config.other_info.footer_info.url')
                params = {
                    'data': xuangu_tableV1_handler(components[0], components),
                    'row_count': _.get(components[0], 'data.meta.extra.row_count', 0),
                    'url': url,
                    'url_params': parse_url_params(url)
                }
            else:
                url = _.get(components[0], 'config.other_info.footer_info.url')
                params = {
                    'data': multi_show_type_handler(components),
                    'url': url,
                    'url_params': parse_url_params(url)
                }
        
        # 添加详细日志
        logger.info(f'convert函数处理结果: components数量={len(components)}, 返回params={params.keys() if params else "空"}')
        # 安全地记录完整结果，避免在优化模式下出现问题
        try:
            logger.debug(f'convert函数完整处理结果: {params}')
        except Exception as e:
            logger.debug(f'convert函数完整处理结果记录失败，仅记录keys: {params.keys() if params else "空"}, 错误: {e}')
        
        return params
    except rq.exceptions.HTTPError as e:
        wrapped = ConvertHttpError(f'HTTP错误: 状态码={getattr(res, "status_code", "unknown")}, 响应={e}')
        logger.error(f'{wrapped}', exc_info=True)
        logger.error(f'响应内容前500字符: {_response_snippet(getattr(res, "text", ""))}')
        if raise_on_error:
            raise wrapped from e
        return {}
    except ConvertError as e:
        logger.error(f'convert函数分类异常: {type(e).__name__} - {e}')
        if raise_on_error:
            raise
        return {}
    except Exception as e:
        # 捕获所有异常，确保函数不会崩溃
        logger.error(f'convert函数处理异常: {type(e).__name__} - {e}', exc_info=True)
        logger.error(f'异常响应内容前500字符: {_response_snippet(getattr(res, "text", "")) if getattr(res, "text", None) else "无响应内容"}')
        return {}
