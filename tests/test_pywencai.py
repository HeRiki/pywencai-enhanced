#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from pywencai import convert as convert_module
from pywencai import headers as headers_module
from pywencai import wencai as wencai_module


class TestPyWencaiHelpers(unittest.TestCase):
    def tearDown(self):
        headers_module.clear_runtime_cache()
        wencai_module.clear_runtime_state()

    def _read_fixture(self, name):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "pywencai" / name
        return fixture_path.read_text(encoding="utf-8")

    def test_tab1_handler_uses_dict_copy_instead_of_invalid_set(self):
        comp = {
            "data": {
                "block_a": {
                    "datas": [{"股票代码": "600001", "股票名称": "测试股份"}]
                }
            },
            "tab_list": [
                {
                    "tab_name": "示例",
                    "list": [
                        {
                            "show_type": "common",
                            "data_index": "block_a",
                        }
                    ],
                }
            ],
        }

        result = convert_module.tab1_handler(comp, [])

        self.assertIn("示例", result)
        self.assertIn("common", result["示例"])
        frame = result["示例"]["common"]
        self.assertIsInstance(frame, pd.DataFrame)
        self.assertEqual(frame.iloc[0]["股票代码"], "600001")

    def test_tab4_handler_skips_empty_show_type_entries(self):
        comp = {
            "tab_list": [
                {
                    "tab_name": "示例",
                    "list": [
                        {"show_type": "", "data": {"datas": [{"a": 1}]}},
                        {"show_type": "common", "data": {"datas": [{"股票代码": "600001"}]}},
                    ],
                }
            ]
        }

        result = convert_module.tab4_handler(comp, [])

        self.assertIn("示例", result)
        self.assertEqual(list(result["示例"].keys()), ["common"])

    def test_get_show_type_handler_falls_back_to_common_handler(self):
        convert_module.UNKNOWN_SHOW_TYPE_COUNTS.clear()
        handler = convert_module.get_show_type_handler("unknown_type")
        self.assertIs(handler, convert_module.common_handler)
        with self.assertLogs(convert_module.logger, level="WARNING") as logs:
            second_handler = convert_module.get_show_type_handler("another_unknown_type")
        self.assertIs(second_handler, convert_module.common_handler)
        self.assertIn("未识别的show_type", "\n".join(logs.output))
        convert_module.get_show_type_handler("another_unknown_type")
        self.assertEqual(convert_module.UNKNOWN_SHOW_TYPE_COUNTS["another_unknown_type"], 2)

    def test_sanitize_headers_for_logging_redacts_sensitive_values(self):
        sanitized = wencai_module._sanitize_headers_for_logging(
            {"cookie": "a=b", "hexin-v": "token", "User-Agent": "ua"}
        )

        self.assertEqual(sanitized["cookie"], "<redacted>")
        self.assertEqual(sanitized["hexin-v"], "<redacted>")
        self.assertEqual(sanitized["User-Agent"], "ua")

    def test_format_log_context_skips_empty_values(self):
        context = wencai_module._format_log_context(
            query="测试",
            page=1,
            query_type="stock",
            empty="",
            none_value=None,
        )

        self.assertEqual(context, "query=测试 | page=1 | query_type=stock")

    def test_normalize_get_kwargs_replaces_known_keys(self):
        normalized = wencai_module._normalize_get_kwargs(
            {"question": "测试", "sort_key": "a", "sort_order": "desc", "foo": "bar"}
        )

        self.assertEqual(normalized["query"], "测试")
        self.assertEqual(normalized["urp_sort_index"], "a")
        self.assertEqual(normalized["urp_sort_way"], "desc")
        self.assertEqual(normalized["foo"], "bar")

    def test_extract_dataframe_from_data_returns_embedded_frame(self):
        frame = pd.DataFrame([{"股票代码": "600001"}])
        result = wencai_module._extract_dataframe_from_data({"detail": frame}, log=False)

        self.assertTrue(result.equals(frame))

    def test_headers_cache_token_and_user_agent_within_process(self):
        fake_module = types.SimpleNamespace(
            UserAgent=lambda: types.SimpleNamespace(random="ua-fixed")
        )
        with patch.dict(sys.modules, {"fake_useragent": fake_module}):
            with patch.object(headers_module, "check_node_available", return_value=(False, None)):
                with patch.object(headers_module, "generate_token_python", return_value="token-fixed") as mock_token:
                    first = headers_module.headers(cookie="a=b")
                    second = headers_module.headers(cookie="a=b")

        self.assertEqual(first["User-Agent"], "ua-fixed")
        self.assertEqual(second["User-Agent"], "ua-fixed")
        self.assertEqual(first["hexin-v"], "token-fixed")
        self.assertEqual(second["hexin-v"], "token-fixed")
        self.assertEqual(mock_token.call_count, 1)

    def test_get_session_reuses_singleton(self):
        fake_session = Mock()
        with patch.object(wencai_module.rq, "Session", return_value=fake_session) as mock_session:
            first = wencai_module.get_session()
            second = wencai_module.get_session()

        self.assertIs(first, fake_session)
        self.assertIs(second, fake_session)
        self.assertEqual(mock_session.call_count, 1)

    def test_get_page_rejects_non_list_data_list(self):
        response = Mock()
        response.text = '{"answer":{"components":[{"data":{"datas":null}}]}}'
        response.raise_for_status = Mock()
        session = Mock()
        session.request.return_value = response
        with patch.object(wencai_module, "get_session", return_value=session):
            result = wencai_module.get_page(
                {"question": "测试"},
                query="测试",
                cookie="a=b",
                retry=1,
                log=False,
            )

        self.assertIsNone(result)

    def test_get_page_rejects_html_response(self):
        response = Mock()
        response.text = self._read_fixture("article_page.html")
        response.raise_for_status = Mock()
        session = Mock()
        session.request.return_value = response
        with patch.object(wencai_module, "get_session", return_value=session):
            result = wencai_module.get_page(
                {"question": "测试"},
                query="测试",
                cookie="a=b",
                retry=1,
                log=False,
            )

        self.assertIsNone(result)

    def test_get_page_refreshes_token_after_html_response(self):
        html_response = Mock()
        html_response.text = self._read_fixture("article_page.html")
        html_response.raise_for_status = Mock()
        json_response = Mock()
        json_response.text = '{"answer":{"components":[{"data":{"datas":[{"股票代码":"600001"}]}}]}}'
        json_response.raise_for_status = Mock()
        session = Mock()
        session.request.side_effect = [html_response, json_response]

        headers_mock = Mock(side_effect=[
            {"hexin-v": "token-a", "User-Agent": "ua", "cookie": "a=b"},
            {"hexin-v": "token-b", "User-Agent": "ua", "cookie": "a=b"},
        ])

        with patch.object(wencai_module, "get_session", return_value=session):
            with patch.object(wencai_module, "headers", headers_mock):
                result = wencai_module.get_page(
                    {"question": "测试"},
                    query="测试",
                    cookie="a=b",
                    retry=1,
                    log=False,
                )

        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(result.iloc[0]["股票代码"], "600001")
        self.assertEqual(headers_mock.call_count, 2)
        self.assertEqual(headers_mock.call_args_list[0].kwargs["force_refresh_token"], True)
        self.assertEqual(headers_mock.call_args_list[1].kwargs["force_refresh_token"], True)

    def test_get_page_refreshes_token_after_401_response(self):
        auth_response = Mock()
        auth_response.status_code = 401
        auth_error = requests.exceptions.HTTPError("unauthorized")
        auth_error.response = auth_response
        json_response = Mock()
        json_response.text = '{"answer":{"components":[{"data":{"datas":[{"股票代码":"600001"}]}}]}}'
        json_response.raise_for_status = Mock()
        session = Mock()
        session.request.side_effect = [auth_error, json_response]

        headers_mock = Mock(side_effect=[
            {"hexin-v": "token-a", "User-Agent": "ua", "cookie": "a=b"},
            {"hexin-v": "token-b", "User-Agent": "ua", "cookie": "a=b"},
        ])

        with patch.object(wencai_module, "get_session", return_value=session):
            with patch.object(wencai_module, "headers", headers_mock):
                result = wencai_module.get_page(
                    {"question": "测试"},
                    query="测试",
                    cookie="a=b",
                    retry=1,
                    log=False,
                )

        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(result.iloc[0]["股票代码"], "600001")
        self.assertEqual(headers_mock.call_count, 2)
        self.assertEqual(headers_mock.call_args_list[0].kwargs["force_refresh_token"], True)
        self.assertEqual(headers_mock.call_args_list[1].kwargs["force_refresh_token"], True)

    def test_get_page_resets_session_after_403_response(self):
        auth_response = Mock()
        auth_response.status_code = 403
        auth_error = requests.exceptions.HTTPError("forbidden")
        auth_error.response = auth_response
        json_response = Mock()
        json_response.text = '{"answer":{"components":[{"data":{"datas":[{"股票代码":"600001"}]}}]}}'
        json_response.raise_for_status = Mock()
        session = Mock()
        session.request.side_effect = [auth_error, json_response]

        headers_mock = Mock(side_effect=[
            {"hexin-v": "token-a", "User-Agent": "ua", "cookie": "a=b"},
            {"hexin-v": "token-b", "User-Agent": "ua", "cookie": "a=b"},
        ])

        with patch.object(wencai_module, "get_session", return_value=session):
            with patch.object(wencai_module, "headers", headers_mock):
                with patch.object(wencai_module, "reset_runtime_http_state") as reset_mock:
                    result = wencai_module.get_page(
                        {"question": "测试"},
                        query="测试",
                        cookie="a=b",
                        retry=1,
                        log=False,
                    )

        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(result.iloc[0]["股票代码"], "600001")
        reset_mock.assert_called_once()

    def test_get_robot_data_uses_https_endpoint(self):
        response = Mock()
        response.text = self._read_fixture("robot_data_xuangu_table_v1.json")
        response.raise_for_status = Mock()
        response.status_code = 200
        response.headers = {}
        session = Mock()
        session.request.return_value = response

        with patch.object(wencai_module, "get_session", return_value=session):
            result = wencai_module.get_robot_data(
                query="十日涨幅前十",
                cookie="a=b",
                retry=1,
                log=False,
            )

        self.assertIsNotNone(result)
        self.assertEqual(
            session.request.call_args.kwargs["url"],
            wencai_module.ROBOT_DATA_URL,
        )
        self.assertTrue(session.request.call_args.kwargs["url"].startswith("https://"))

    def test_get_robot_data_refreshes_token_after_401_response(self):
        auth_response = Mock()
        auth_response.status_code = 401
        auth_error = requests.exceptions.HTTPError("unauthorized")
        auth_error.response = auth_response
        json_response = Mock()
        json_response.text = self._read_fixture("robot_data_xuangu_table_v1.json")
        json_response.raise_for_status = Mock()
        json_response.status_code = 200
        json_response.headers = {}
        session = Mock()
        session.request.side_effect = [auth_error, json_response]

        headers_mock = Mock(side_effect=[
            {"hexin-v": "token-a", "User-Agent": "ua", "cookie": "a=b"},
            {"hexin-v": "token-b", "User-Agent": "ua", "cookie": "a=b"},
        ])

        with patch.object(wencai_module, "get_session", return_value=session):
            with patch.object(wencai_module, "headers", headers_mock):
                result = wencai_module.get_robot_data(
                    query="十日涨幅前十",
                    cookie="a=b",
                    retry=1,
                    log=False,
                )

        self.assertIsNotNone(result)
        self.assertEqual(headers_mock.call_count, 2)
        self.assertEqual(headers_mock.call_args_list[0].kwargs["force_refresh_token"], True)
        self.assertEqual(headers_mock.call_args_list[1].kwargs["force_refresh_token"], True)

    def test_get_robot_data_resets_session_after_403_response(self):
        auth_response = Mock()
        auth_response.status_code = 403
        auth_error = requests.exceptions.HTTPError("forbidden")
        auth_error.response = auth_response
        json_response = Mock()
        json_response.text = self._read_fixture("robot_data_xuangu_table_v1.json")
        json_response.raise_for_status = Mock()
        json_response.status_code = 200
        json_response.headers = {}
        session = Mock()
        session.request.side_effect = [auth_error, json_response]

        headers_mock = Mock(side_effect=[
            {"hexin-v": "token-a", "User-Agent": "ua", "cookie": "a=b"},
            {"hexin-v": "token-b", "User-Agent": "ua", "cookie": "a=b"},
        ])

        with patch.object(wencai_module, "get_session", return_value=session):
            with patch.object(wencai_module, "headers", headers_mock):
                with patch.object(wencai_module, "reset_runtime_http_state") as reset_mock:
                    result = wencai_module.get_robot_data(
                        query="十日涨幅前十",
                        cookie="a=b",
                        retry=1,
                        log=False,
                    )

        self.assertIsNotNone(result)
        reset_mock.assert_called_once()

    def test_get_page_uses_https_endpoint(self):
        response = Mock()
        response.text = '{"answer":{"components":[{"data":{"datas":[{"股票代码":"600001"}]}}]}}'
        response.raise_for_status = Mock()
        session = Mock()
        session.request.return_value = response

        with patch.object(wencai_module, "get_session", return_value=session):
            result = wencai_module.get_page(
                {"question": "测试"},
                query="测试",
                cookie="a=b",
                retry=1,
                log=False,
            )

        self.assertIsInstance(result, pd.DataFrame)
        self.assertEqual(
            session.request.call_args.kwargs["url"],
            wencai_module.LANDING_DATA_URL,
        )
        self.assertTrue(session.request.call_args.kwargs["url"].startswith("https://"))

    def test_while_do_retries_on_429_http_error(self):
        response = Mock()
        response.status_code = 429
        error = requests.exceptions.HTTPError("rate limited")
        error.response = response
        call_count = {"value": 0}

        def flaky():
            call_count["value"] += 1
            if call_count["value"] < 3:
                raise error
            return "ok"

        result = wencai_module.while_do(flaky, retry=3, sleep=0, log=False)

        self.assertEqual(result, "ok")
        self.assertEqual(call_count["value"], 3)

    def test_while_do_does_not_retry_on_400_http_error(self):
        response = Mock()
        response.status_code = 400
        error = requests.exceptions.HTTPError("bad request")
        error.response = response
        call_count = {"value": 0}

        def bad_request():
            call_count["value"] += 1
            raise error

        result = wencai_module.while_do(bad_request, retry=5, sleep=0, log=False)

        self.assertIsNone(result)
        self.assertEqual(call_count["value"], 1)

    def test_while_do_does_not_retry_on_empty_data_error(self):
        call_count = {"value": 0}

        def empty_result():
            call_count["value"] += 1
            raise wencai_module.WencaiEmptyDataError("empty")

        result = wencai_module.while_do(empty_result, retry=5, sleep=0, log=False)

        self.assertIsNone(result)
        self.assertEqual(call_count["value"], 1)

    def test_while_do_resets_session_and_retries_on_connection_error(self):
        error = requests.exceptions.ConnectionError("connection reset")
        call_count = {"value": 0}

        def flaky():
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise error
            return "ok"

        with patch.object(wencai_module, "reset_runtime_http_state") as reset_mock:
            result = wencai_module.while_do(flaky, retry=3, sleep=0, log=False)

        self.assertEqual(result, "ok")
        self.assertEqual(call_count["value"], 2)
        reset_mock.assert_called_once()

    def test_connection_retry_backoff_uses_transport_floor(self):
        self.assertEqual(
            wencai_module._connection_retry_backoff_seconds(1, 0),
            0.2,
        )
        self.assertEqual(
            wencai_module._connection_retry_backoff_seconds(3, 0.8),
            0.8,
        )

    def test_convert_parses_robot_fixture(self):
        response = Mock()
        response.text = self._read_fixture("robot_data_xuangu_table_v1.json")
        response.raise_for_status = Mock()
        response.status_code = 200

        result = convert_module.convert(response, raise_on_error=True)

        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["data"]["condition"], "十日涨幅前十")
        self.assertEqual(result["url_params"]["querytype"], "stock")

    def test_convert_raises_missing_components_error(self):
        response = Mock()
        response.text = self._read_fixture("robot_data_missing_components.json")
        response.raise_for_status = Mock()
        response.status_code = 200

        with self.assertRaises(convert_module.ConvertMissingComponentsError):
            convert_module.convert(response, raise_on_error=True)

    def test_convert_supports_result_wrapper_payload(self):
        response = Mock()
        response.text = self._read_fixture("robot_data_result_wrapper.json")
        response.raise_for_status = Mock()
        response.status_code = 200

        result = convert_module.convert(response, raise_on_error=True)

        self.assertEqual(result["row_count"], 24)
        self.assertEqual(result["data"]["condition"], "2026-03-19 涨停")


if __name__ == "__main__":
    unittest.main()
