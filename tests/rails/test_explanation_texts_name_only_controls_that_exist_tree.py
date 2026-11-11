"""No text about explanations points the owner at a Settings control that does not exist.

The owner's default is ``agent.explain``. Settings → Agent → Agent defaults carries its row,
labelled Explanations, so the CHANGELOG's entries, the chat guide, the module, the config field's
own description and the one-time window may send the owner there, and only while that row exists:
each text that names Agent defaults (or, in the CHANGELOG, Settings) is held to it, and a Settings
path that ends at Explanations must be the path the dashboard has, page, section and row. The
behaviour is held in ``tests/test_explanations.py`` and the window's own test.
"""

from __future__ import annotations

import re
from dataclasses import fields

import source_index
from table_rows import row

from personalclaw.config.loader import AgentConfig

REPO = source_index.REPO
SETTINGS = REPO / "web" / "src" / "pages" / "settings"

#: A Settings path to the row as a text writes one, ``Settings → Page → Section → Explanations``,
#: read over the text with its whitespace collapsed, so a path a line break splits is read whole.
_PATH = re.compile(r"Settings(?: → [^→.]+?)*? → Explanations\b")


def _paths(text: str) -> list[str]:
    return _PATH.findall(" ".join(text.split()))


def _read(path) -> str:
    assert source_index.holds(path), f"{path.name} moved, so this reads nothing"
    return source_index.text(path)


def _the_rows_path() -> str | None:
    """``Settings → <page> → <section> → Explanations`` as the dashboard has it, or None when
    the Agent defaults panel has no Explanations row that saves ``explain``."""
    panel = _read(SETTINGS / "AgentDefaultsPanel.tsx")
    if 'label="Explanations"' not in panel or "patch('explain'" not in panel:
        return None
    section = re.search(r"\{ id: 'agent', label: '([^']+)'", _read(SETTINGS / "SettingsPage.tsx"))
    page = re.search(
        r"\{ id: '[^']+', label: '([^']+)'[^\n]*sections: \['agent'",
        _read(SETTINGS / "settingsPages.ts"),
    )
    assert section and page, "the Agent defaults section or its page moved, so this reads nothing"
    return f"Settings → {page.group(1)} → {section.group(1)} → Explanations"


def _region(relative: str, start: str, end: str) -> str:
    text = (REPO / relative).read_text(encoding="utf-8")
    at = text.index(start)
    region = text[at : text.index(end, at + len(start))]
    assert len(region) > len(start), f"{relative}: the region moved, so this reads nothing"
    return region


def _texts() -> dict[str, str]:
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.index("## [Unreleased]")
    headlines = [line for line in changelog[start:].splitlines() if "Explanations" in line]
    assert headlines, "the CHANGELOG names no Explanations setting, so this reads nothing"
    meta = {f.name: f.metadata for f in fields(AgentConfig)}["explain"]
    return {
        "CHANGELOG": "\n".join(headlines),
        "chat guide": _region(
            "docs/guides/chat-surface.md", "**Explanations.**", "**Editing a style.**"
        ),
        "explanations.py": (REPO / "src/personalclaw/explanations.py").read_text(encoding="utf-8"),
        "AgentConfig.explain": str(meta),
        "the window": (REPO / "web/src/pages/chat/hero/ExplainQuestion.tsx").read_text(
            encoding="utf-8"
        ),
    }


def test_the_window_and_the_guide_name_the_rows_path():
    """Held on its own so the rail below cannot pass by reading no path at all."""
    texts = _texts()
    for where in ("the window", "chat guide", "CHANGELOG"):
        assert _paths(texts[where]), f"{where} names no Settings path to Explanations"


def test_no_explanations_text_points_at_a_settings_control_there_is_none_of(subtests):
    there = _the_rows_path()
    for where, text in _texts().items():
        with row(subtests, where=where):
            if "Agent defaults" in text:
                assert there, f"{where} names a Settings row with no control"
            if where == "CHANGELOG" and "Settings" in text:
                assert there, f"the CHANGELOG names a Settings control: {text}"
            for path in _paths(text):
                assert path == there, f"{where} names {path!r}; the dashboard has {there!r}"
