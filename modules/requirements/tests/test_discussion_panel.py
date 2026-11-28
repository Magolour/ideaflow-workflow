"""Web 讨论面板：标签、容器与交互锚点。"""

from __future__ import annotations

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "ideaflow" / "web"


class DiscussionPanelTest(unittest.TestCase):
    def test_result_page_has_discussion_tab_and_panel(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-stab="discussion"', html)
        self.assertIn('id="panelDiscussion"', html)
        self.assertIn('id="btnClarify"', html)
        self.assertIn('id="clarifyPanel"', html)
        for anchor in ('id="flowNav"', 'id="clarifyStage"', 'id="specToc"',
                       'id="specReview"', 'id="btnModelSettings"',
                       'id="btnApiConfig"', 'id="apiBase"', 'id="apiModel"',
                       'id="apiKey"', 'id="apiPreset"'):
            self.assertIn(anchor, html)

    def test_app_js_renders_roles_timeline_and_replay(self) -> None:
        script = (WEB / "app.js").read_text(encoding="utf-8")
        for anchor in ("function renderDiscussionPanel", "watchJobEvents",
                       "EventSource", "btnConfirmSpec", "btnReprompt",
                       "discReplay", "discRole", "discRound", "discTimeline",
                       "关键意见处理", "候选方案与立场矩阵"):
            self.assertIn(anchor, script)

    def test_style_covers_discussion_layout(self) -> None:
        css = (WEB / "app.css").read_text(encoding="utf-8")
        for anchor in (".disc-roles", ".disc-timeline", ".disc-event",
                       ".disc-option", ".disc-stage"):
            self.assertIn(anchor, css)


if __name__ == "__main__":
    unittest.main()
