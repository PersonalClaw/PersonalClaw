"""No text about reply styles points the owner at a Settings control that does not exist.

The platform default is ``agent.reply_style``, set through the config API: Settings has no
reply-style row, so the CHANGELOG, the chat guide, the module, the config fields' own
descriptions, the agent form and the composer's picker may not send the owner to one. Once a
Settings page carries the control, they may name it. The behaviour is held in
``tests/test_reply_style.py``.
"""

from __future__ import annotations

from dataclasses import fields

import source_index
from table_rows import row

from personalclaw.config.loader import AgentConfig, AgentProfile

REPO = source_index.REPO


def _settings_has_a_reply_style_control() -> bool:
    pages = REPO / "web" / "src" / "pages" / "settings"
    found = source_index.under(source_index.web_files(), pages, (".tsx",))
    assert found, "no Settings page was found, so this reads nothing"
    return any(
        "reply_style" in source_index.text(path)
        for path in found
        if not path.name.endswith(".test.tsx")
    )


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
            "web/src/pages/agents/AgentForm.tsx", "const REPLY_STYLE_OPTIONS", "]"
        ),
        "composer picker": _region(
            "web/src/ui/composer/controls.tsx", "/** Reply-style picker", "\n}\n"
        ),
    }


def test_no_reply_style_text_points_at_a_settings_control_there_is_none_of(subtests):
    if _settings_has_a_reply_style_control():
        return
    for where, text in _texts().items():
        with row(subtests, where=where):
            assert "Agent defaults" not in text, f"{where} names a Settings row with no control"
            if where == "CHANGELOG":
                assert "Settings" not in text, f"the CHANGELOG names a Settings control: {text}"
