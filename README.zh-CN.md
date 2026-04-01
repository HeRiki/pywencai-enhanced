# pywencai-enhanced

[English](./README.md) | [简体中文](./README.zh-CN.md)

[![CI](https://github.com/HeRiki/pywencai-enhanced/actions/workflows/ci.yml/badge.svg)](https://github.com/HeRiki/pywencai-enhanced/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/HeRiki/pywencai-enhanced/blob/main/LICENSE)

`pywencai-enhanced` 是 [`pywencai`](https://pypi.org/project/pywencai/) 的增强版分支，保留了熟悉的 `from pywencai import get` 用法，同时把真实环境下的请求稳定性和可维护性做得更强。

这个仓库当前优先面向 GitHub 安装。distribution name 是 `pywencai-enhanced`，但 Python import 名仍然保持为 `pywencai`。

## 这个分支改进了什么

相对原始的 `pywencai 0.7.1`，这个版本主要强化了稳定性和工程化能力：

- 全链路统一使用 HTTPS 接口。
- 复用 HTTP session，避免每次请求都重新建立一套链路。
- 针对超时、连接错误、`429` 和服务端异常提供更稳的重试策略。
- 能识别问财返回 HTML/article 页面并强制刷新 token 后重试。
- 在 `401` / `403` 鉴权失败时自动刷新 token。
- 仓库内直接附带 `hexin-v.bundle.js`，普通用户不需要额外执行 `npm install`。
- 支持更丰富的 `get-robot-data` 解析和更多 `show_type` 场景。
- 带有独立的 fixture 单测，覆盖 token 刷新、重试行为和解析兼容性。

## 安装

直接从 GitHub 安装：

```bash
pip install git+https://github.com/HeRiki/pywencai-enhanced.git
```

按 tag 安装指定版本：

```bash
pip install git+https://github.com/HeRiki/pywencai-enhanced.git@v0.1.0
```

本地开发安装：

```bash
pip install -e .
```

## 快速开始

```python
import pywencai

df = pywencai.get(
    query="十日涨幅前10",
    query_type="stock",
    cookie="你的问财 cookie",
    log=True,
)

print(df.head())
```

这个包也继续兼容原版常见写法：

```python
import pywencai

df = pywencai.get(
    question="退市股票",
    sort_key="退市@退市日期",
    sort_order="asc",
)
```

## 对外接口

- `from pywencai import get`
- `pywencai.wencai.get`
- `pywencai.headers.headers`
- `pywencai.convert.convert`

当前 `get(...)` 仍兼容这些常见参数：

- `query` / `question`
- `sort_key` / `sort_order`
- `loop`
- `query_type`
- `cookie`
- `user_agent`
- `retry`
- `sleep`
- `log`
- `request_params`
- `pro`
- `find`
- `no_detail`

## Cookie 和 Node.js 说明

- 真实请求场景下，仍建议提供有效的 iWenCai cookie。
- 普通使用者不需要执行 `npm install`，因为仓库已经带了构建好的 `hexin-v.bundle.js`。
- 为了获得更稳定的 token 生成能力，运行环境仍推荐安装 Node.js。
- 如果没有 Node.js，包会回退到 Python 生成 token；这个回退路径可以用，但稳定性可能不如 Node 方案。

## 维护者工作流

只有当你需要重新构建打包后的 token 脚本时，才需要 Node/npm：

```bash
cd src/pywencai
npm install
npx webpack --config webpack.config.js
```

## 常见排查

- 如果一直拿到空 `DataFrame`，先检查 cookie 是否有效。
- 如果返回的是 HTML 而不是 JSON，这个包会自动重试，但过期 cookie 仍然可能持续失败。
- 如果本机没有 Node.js，排查问题前建议先装好 Node.js，先排除 token 生成差异。
- 如果问财上游响应结构变化，先跑 fixture 单测，再检查 `convert.py`。

## 测试

独立包测试命令：

```bash
python -m unittest tests.test_pywencai
```

## 致谢与来源

这个项目是上游 [`zsrl/pywencai`](https://github.com/zsrl/pywencai) 的增强版衍生实现，并保留原始 MIT 许可证。当前仓库在请求重试、鉴权恢复、解析能力、打包方式和测试覆盖方面做了单独增强，用于更稳定的日常使用。
