"""No text about reply styles points the owner at a Settings control that does not exist.

The platform default is ``agent.reply_style``. Settings → Agent defaults carries its row,
labelled Reply style, so the CHANGELOG, the chat guide, the module, the config fields' own
descriptions, the agent form and the composer's picker may send the owner there, and only
while that row exists: each text that names Agent defaults (or, in the CHANGELOG, Settings) is
held to it. The behaviour is held in ``tests/test_reply_style.py``.
"""

from __future__ import annotations

from dataclasses import fields

import source_index
from table_rows import row

from personalclaw.config.loader import AgentConfig, AgentProfile

REPO = source_index.REPO


def _settings_has_a_reply_style_control() -> bool:
    """Agent defaults carries a row labelled Reply style that saves ``reply_style``."""
    panel = REPO / "web" / "src" / "pages" / "settings" / "AgentDefaultsPanel.tsx"
    assert source_index.holds(panel), "the Agent defaults panel moved, so this reads nothing"
    text = source_index.text(panel)
    return 'label="Reply style"' in text and "patch('reply_style'" in text


def _region(relative: str, start: str, end: str) -> str:
    text = (REPO / relative).read_text(encoding="utf-8")
    at = text.index(start)
    region = text[at : text.index(end, at + len(start))]
    assert len(region) > len(start), f"{relative}: the region moved, so this reads nothing"
    return region


def _texts() -> dict[str, str]:
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog[: changelog.index("\n## ", changelog.index("## [Unreleased]") + 1)]
    headlines = [line for line in unreleased.splitlines() if "reply style" in line.lower()]
    assert headlines, "the CHANGELOG names no reply style, so this reads nothing"
    agent_meta = {f.name: f.metadata for f in fields(AgentProfile)}["reply_style"]
    platform_meta = {f.name: f.metadata for f in fields(AgentConfig)}["reply_style"]
    return {
        "CHANGELOG": "\n".join(headlines),
        "chat guide": _region("docs/guides/chat-surface.md", "## Reply style", "\n---\n"),
        "reply_style.py": (REPO / "src/personalclaw/reply_style.py").read_text(encoding="utf-8"),
        "AgentProfile.reply_style": str(agent_meta),
        "AgentConfig.reply_style": str(platform_meta),
        "agent form": _region(
            "web/src/pages/agents/AgentForm.tsx", "/** The agent's reply style choices", "\n}\n"
        ),
        "composer picker": _region(
            "web/src/ui/composer/controls.tsx", "/** Reply-style picker", "\n}\n"
        ),
    }


def test_no_reply_style_text_points_at_a_settings_control_there_is_none_of(subtests):
    control = _settings_has_a_reply_style_control()
    for where, text in _texts().items():
        with row(subtests, where=where):
            if "Agent defaults" in text:
                assert control, f"{where} names a Settings row with no control"
            if where == "CHANGELOG" and "Settings" in text:
                assert control, f"the CHANGELOG names a Settings control: {text}"
