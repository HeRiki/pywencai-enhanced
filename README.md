# pywencai-enhanced

[English](./README.md) | [简体中文](./README.zh-CN.md)

[![CI](https://github.com/HeRiki/pywencai-enhanced/actions/workflows/ci.yml/badge.svg)](https://github.com/HeRiki/pywencai-enhanced/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/HeRiki/pywencai-enhanced/blob/main/LICENSE)

`pywencai-enhanced` is an enhanced fork of [`pywencai`](https://pypi.org/project/pywencai/) that keeps the familiar `from pywencai import get` API while making the real-world request path more resilient.

This repository is intended for GitHub installation first. The distribution name is `pywencai-enhanced`, while the Python import name remains `pywencai`.

## Upstream

This project is maintained as an enhanced derivative of [`zsrl/pywencai`](https://github.com/zsrl/pywencai). It keeps API compatibility where practical while improving auth refresh, retry behavior, parser resilience, packaging, and test coverage.

## Why this fork exists

Compared with the original `pywencai 0.7.1`, this fork focuses on runtime stability and maintainability:

- Uses HTTPS endpoints throughout.
- Reuses HTTP sessions and short-lived token buckets instead of creating a fresh request flow every time.
- Retries more safely for timeouts, connection errors, `429`, and server-side failures.
- Detects HTML/article-page fallbacks and retries after forcing a fresh token.
- Forces token refresh after `401` or `403` authentication failures.
- Keeps nested follow-up requests on the same request context, including cookie, User-Agent, and explicit proxy settings.
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
pip install git+https://github.com/HeRiki/pywencai-enhanced.git@v0.2.0
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
    strict=False,
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

If you want request/auth/parser failures to raise instead of being converted into an empty `DataFrame`, enable strict mode:

```python
df = pywencai.get(
    query="十日涨幅前10",
    cookie="your iwencai cookie",
    strict=True,
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
- `strict`
- `request_params`
- `pro`
- `find`
- `no_detail`

## Cookie and Node.js notes

- A valid iWenCai cookie is still recommended for reliable live requests.
- Normal users do not need `npm install`. The repository already includes the built `hexin-v.bundle.js`.
- Node.js is still recommended for best token-generation compatibility at runtime.
- If Node.js is not available, the package falls back to a Python-generated token. That fallback is convenient, but it may be less reliable than the Node-based path.
- `page` and `nested` requests reuse tokens only within the same short-lived request bucket: `cookie + resolved User-Agent + explicit proxy identity`. Switching account, User-Agent, or explicit proxy automatically uses a different bucket.
- `get-robot-data` is intentionally excluded from that reuse rule. Live stress runs showed that reusing the same bucket token on the `robot` path can first fail with `initial 401/403` under higher request rates, then recover only after a forced refresh.
- Because of that observed failure mode, the `robot` path now always generates a fresh token for the initial request and the auth retry instead of reusing the bucket cache.
- Nested follow-up requests inherit the top-level `cookie`, `user_agent`, and explicit `request_params` such as `proxies`, `verify`, and `allow_redirects`.

## Logging

- `log=True` keeps request-path logs enabled.
- `log=False` keeps the library silent, including auth and parser retry paths.
- Host applications that need a harder global guarantee can disable runtime logs for the current process. After that, even call sites that still pass `log=True` will stay silent until the host re-enables logging.
- Host applications can route package logs to their own logger with:

```python
import logging
import pywencai

pywencai.configure_logger(logging.getLogger("my-app"))
pywencai.configure_runtime_logging(False)
```

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
PYTHONPATH=src python -m unittest tests.test_pywencai
```

## Live stress check

For controlled live traffic validation, use the built-in stress script with a real cookie loaded from an environment variable, a text file, or a YAML config:

```bash
python scripts/stress_test.py \
  --cookie-config /path/to/config.local.yaml \
  --cookie-key data.cookie \
  --query "平安银行" \
  --phase warmup:1:15:1 \
  --phase medium:2:15:2 \
  --phase hot:4:10:4
```

The script reports:

- success vs. empty `DataFrame` vs. raised failures
- latency percentiles
- HTTP status distribution
- token call / cache-hit / forced-refresh count
- token cache policy usage (`reuse` vs `bypass`)
- forced-refresh reasons and session-reset reasons
- token generation mode distribution
- initial vs. refresh request outcomes
- bucket-level request outcome aggregation and recent request event samples

Current default policy:

- `get-robot-data` bypasses the token cache for both the initial request and auth retry
- this is not a generic preference for "less reuse"; it is a specific mitigation for the observed `robot.initial.401/403 -> refresh success` pattern under higher request rates
- `page` and `nested` requests still use the normal cache reuse policy

Do not commit real cookies or generated reports.

## Attribution

This project is an enhanced derivative of the upstream [`zsrl/pywencai`](https://github.com/zsrl/pywencai) project and keeps the original MIT license. The enhanced request, retry, parsing, packaging, and test coverage in this repository are maintained separately for more reliable day-to-day usage.
