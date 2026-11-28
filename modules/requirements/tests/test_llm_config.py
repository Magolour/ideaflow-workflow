"""请求级模型配置：只接受 base_url / api_key / model。"""

from __future__ import annotations

import unittest

from ideaflow.server import ApiError, _llm_options


class LlmOptionsTest(unittest.TestCase):
    def test_trims_and_keeps_three_fields(self) -> None:
        self.assertEqual(
            _llm_options({"llm": {"base_url": " https://a/v1 ", "api_key": " k ",
                                  "model": " qwen-plus "}}),
            {"base_url": "https://a/v1", "api_key": "k", "model": "qwen-plus"},
        )

    def test_empty_config_falls_back_to_env(self) -> None:
        self.assertEqual(_llm_options({}), {})
        self.assertEqual(_llm_options({"llm": {}}), {})

    def test_rejects_non_object_and_non_string(self) -> None:
        with self.assertRaises(ApiError):
            _llm_options({"llm": "x"})
        with self.assertRaises(ApiError):
            _llm_options({"llm": {"api_key": 1}})

    def test_ignores_unknown_fields(self) -> None:
        self.assertEqual(_llm_options({"llm": {"provider": "x"}}), {})


if __name__ == "__main__":
    unittest.main()
