"""约束主题匹配：长主题收紧到 3 字，避免短词误伤。"""

from __future__ import annotations

import unittest

from ideaflow.constraints import shares_topic


class SharesTopicTest(unittest.TestCase):
    def test_short_subject_still_matches_payment(self) -> None:
        self.assertTrue(shares_topic("在线支付", "买家付款跳转至微信支付"))

    def test_long_subject_ignores_partial_overlap(self) -> None:
        self.assertFalse(shares_topic("跨校区自提调度或预约", "支持填写自提点（下拉单选）"))

    def test_long_subject_ignores_collection_notice(self) -> None:
        self.assertFalse(shares_topic("实现收藏后的实时通知", "支持收藏，仅前端标记"))


if __name__ == "__main__":
    unittest.main()
