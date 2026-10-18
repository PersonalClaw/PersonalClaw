"""The source-tree checks split from
``tests/test_inert_settings_controls.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import pytest
import source_index
from inert_settings_controls_shared import DELETED, REPO

WEB_SRC = REPO / "web/src"


_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/")


_LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _frontend_sources() -> dict[str, str]:
    """Every non-test ``.ts``/``.tsx`` under ``web/src``, comments stripped."""
    out: dict[str, str] = {}
    for p in [p for p in source_index.web_files() if p.suffix in {".ts", ".tsx"}]:
        if ".test." in p.name:
            continue
        out[str(p.relative_to(REPO))] = _strip_comments(source_index.text(p))
    return out


def _control_sites(leaf: str, sources: dict[str, str]) -> list[str]:
    """Files rendering a Settings control bound to ``leaf`` (the last dotted segment).

    Matched on the row renderers' own binding — ``field="<leaf>"`` — and on the leaf quoted as
    a string, which together cover every control shape the settings panels use. Deliberately
    NOT the ``patchConfig(`ambient.${key}`)`` template shape that
    ``test_settings_control_coverage.py`` matches: that template structurally covers every
    one-segment ``ambient.*`` path, so it would report a writer for a row that no longer
    exists. The question here is whether a CONTROL is rendered, and a control names its field.
    """
    needles = (f'field="{leaf}"', f"field='{leaf}'", f"'{leaf}'", f'"{leaf}"')
    return [rel for rel, text in sources.items() if any(n in text for n in needles)]


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    return _frontend_sources()


def test_the_frontend_scan_is_not_vacuous(sources: dict[str, str]) -> None:
    """VACUITY. Every deletion assertion below is a ZERO from :func:`_control_sites`, so a
    matcher that stopped resolving — a renamed prop, a reworked row renderer, an empty file
    walk — would make all four trivially green. The floor: the scan must find the sources at
    all, and must find the controls for the two ambient leaves that KEPT theirs.
    """
    assert len(sources) >= 400, f"the frontend walk must find the sources, got {len(sources)}"
    for leaf in ("tiles_enabled", "genui_enabled"):
        hits = _control_sites(leaf, sources)
        assert hits, f"the scan cannot see the surviving control for {leaf}"
        assert any("AmbientPanel" in h for h in hits), hits


def test_comment_stripping_actually_strips(sources: dict[str, str]) -> None:
    """The stripper's own control, both directions.

    Forward: a leaf named only inside a line comment and a block comment must read as ABSENT.
    Backward: the same leaf in real code must read as present — a stripper that ate everything
    would also pass the forward half.
    """
    commented = '// field="tray_enabled"\n/* field="tray_enabled" */\nconst x = 1\n'
    assert _control_sites("tray_enabled", {"fake.tsx": _strip_comments(commented)}) == []
    live = 'const x = 1\n<ToggleRow field="tray_enabled" />\n'
    assert _control_sites("tray_enabled", {"fake.tsx": _strip_comments(live)}) == ["fake.tsx"]
    # And the real panels DO carry the explanatory comments this guards, so the guard is not
    # hypothetical: a raw scan of the tree would read them as controls.
    raw = (WEB_SRC / "pages/settings/AmbientPanel.tsx").read_text(encoding="utf-8")
    assert "tray_enabled" in raw, "the panel should still EXPLAIN what it removed"
    assert "tray_enabled" not in _strip_comments(raw), "…and only in a comment"


@pytest.mark.parametrize("path_key,cls_name,leaf", DELETED, ids=[d[0] for d in DELETED])
def test_a_deleted_leaf_has_no_settings_control(
    path_key: str, cls_name: str, leaf: str, sources: dict[str, str]
) -> None:
    """Contract point 6: no frontend control. Comments stripped — see the module docstring."""
    hits = _control_sites(leaf, sources)
    assert hits == [], f"{path_key} still has a control in {hits}"
