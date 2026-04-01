# pywencai-enhanced

[![CI](https://github.com/HeRiki/pywencai-enhanced/actions/workflows/ci.yml/badge.svg)](https://github.com/HeRiki/pywencai-enhanced/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/HeRiki/pywencai-enhanced/blob/main/LICENSE)

`pywencai-enhanced` is an enhanced fork of [`pywencai`](https://pypi.org/project/pywencai/) that keeps the familiar `from pywencai import get` API while making the real-world request path more resilient.

This repository is intended for GitHub installation first. The distribution name is `pywencai-enhanced`, while the Python import name remains `pywencai`.

## Why this fork exists

Compared with the original `pywencai 0.7.1`, this fork focuses on runtime stability and maintainability:

- Uses HTTPS endpoints throughout.
- Reuses HTTP sessions instead of creating a fresh request flow every time.
- Retries more safely for timeouts, connection errors, `429`, and server-side failures.
- Detects HTML/article-page fallbacks and retries after forcing a fresh token.
- Forces token refresh after `401` or `403` authentication failures.
- Ships a checked-in `hexin-v.bundle.js` so normal users do not need to run `npm install`.
- Supports richer `get-robot-data` parsing and more `show_type` variants.
- Includes standalone fixture-driven tests for token refresh, retry behavior, and parser compatibility.

## Installation

Install directly from GitHub:

```bash
pip install git+https://github.com/HeRiki/pywencai-enhanced.git
```

Install a tagged release:

```bash
pip install git+https://github.com/HeRiki/pywencai-enhanced.git@v0.1.0
```

For local development:

```bash
pip install -e .
```

## Quick start

```python
import pywencai

df = pywencai.get(
    query="十日涨幅前10",
    query_type="stock",
    cookie="your iwencai cookie",
    log=True,
)

print(df.head())
```

The package still supports the original calling style:

```python
import pywencai

df = pywencai.get(
    question="退市股票",
    sort_key="退市@退市日期",
    sort_order="asc",
)
```

## Supported public API

- `from pywencai import get`
- `pywencai.wencai.get`
- `pywencai.headers.headers`
- `pywencai.convert.convert`

The current `get(...)` interface remains compatible with these common parameters:

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

## Cookie and Node.js notes

- A valid iWenCai cookie is still recommended for reliable live requests.
- Normal users do not need `npm install`. The repository already includes the built `hexin-v.bundle.js`.
- Node.js is still recommended for best token-generation compatibility at runtime.
- If Node.js is not available, the package falls back to a Python-generated token. That fallback is convenient, but it may be less reliable than the Node-based path.

## Maintainer workflow

You only need Node/npm if you want to rebuild the bundled token script:

```bash
cd src/pywencai
npm install
npx webpack --config webpack.config.js
```

## Troubleshooting

- If you keep getting an empty `DataFrame`, verify your cookie first.
- If responses look like HTML instead of JSON, the package will retry automatically, but an expired cookie can still fail repeatedly.
- If Node.js is missing, install it before debugging parser issues so you can rule out token-generation differences.
- If a response shape changes upstream, run the fixture tests first and then inspect `convert.py`.

## Tests

Run the standalone package tests with:

```bash
python -m unittest tests.test_pywencai
```

## Attribution

This project is an enhanced derivative of the upstream [`zsrl/pywencai`](https://github.com/zsrl/pywencai) project and keeps the original MIT license. The enhanced request, retry, parsing, packaging, and test coverage in this repository are maintained separately for more reliable day-to-day usage.
