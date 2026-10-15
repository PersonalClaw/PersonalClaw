"""Math in the dashboard is rendered and styled by ONE KaTeX.

``web/src/ui/Markdown.tsx`` renders math with ``rehype-katex``, whose markup comes from the
``katex`` that package resolves from its own location, and styles it with
``katex/dist/katex.min.css``, which resolves to the copy the web workspace declares. KaTeX ships
its stylesheet and fonts for the markup of the same release, and releases do not agree: 0.18
renamed the classes of its internal boxes (``base`` became ``katex-base``, ``strut``
``katex-strut``, ``root`` ``katex-root``, and eighteen more). ``rehype-katex`` 7.0.1, ``mermaid``
12.1.0 and ``micromark-extension-math`` 3.1.0 all declare ``katex ^0.16``, so npm gave each a
nested 0.16 copy while the 0.18 stylesheet styled the page. MEASURED in Chromium: overlines,
underlines, boxed equations, array rules, accents, root indices, size commands and equation tags
came out missing or misplaced, and nothing failed, because every test renders in jsdom.

The root ``overrides`` block puts every dependent on the web workspace's ``katex``. These rails
hold the lockfile to it: every ``katex`` entry, nested copies included, is one version, and it is
a version the web workspace's own declared range admits, so the override and the declaration
cannot drift apart either.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOCKFILE = _ROOT / "package-lock.json"
_WEB_MANIFEST = _ROOT / "web" / "package.json"
_MARKDOWN = _ROOT / "web" / "src" / "ui" / "Markdown.tsx"


def _katex_entries() -> list[tuple[str, str]]:
    """Every (lockfile path, version) of ``katex`` itself, hoisted and nested. ``@types/katex``
    is a different package and its last path segment says so."""
    lock = json.loads(_LOCKFILE.read_text(encoding="utf-8"))
    return [
        (path, meta["version"])
        for path, meta in lock.get("packages", {}).items()
        if path.rsplit("node_modules/", 1)[-1] == "katex" and "node_modules/" in path
    ]


def _caret_admits(spec: str, version: str) -> bool:
    """Whether npm's caret range *spec* (``^X.Y.Z``) admits *version*: the leftmost non-zero
    component may not change, which for a ``0.y`` release means the minor is fixed."""
    m = re.fullmatch(r"\^(\d+)\.(\d+)\.(\d+)", spec.strip())
    assert m, f"web/package.json declares katex {spec!r}; teach _caret_admits that form"
    low = tuple(int(part) for part in m.groups())
    got = tuple(int(part) for part in version.split("-", 1)[0].split("."))
    if got < low:
        return False
    if low[0] > 0:
        return got[0] == low[0]
    if low[1] > 0:
        return got[:2] == low[:2]
    return got == low


def test_the_renderer_and_the_stylesheet_this_rail_reasons_about_are_still_the_ones_used() -> None:
    """Vacuity floor: if math stopped going through rehype-katex and KaTeX's stylesheet, this
    rail would be holding a constraint nothing needs."""
    source = _MARKDOWN.read_text(encoding="utf-8")
    assert "import rehypeKatex from 'rehype-katex'" in source
    assert "import 'katex/dist/katex.min.css'" in source
    declared = json.loads(_WEB_MANIFEST.read_text(encoding="utf-8"))["dependencies"]
    assert "katex" in declared and "rehype-katex" in declared


def test_every_katex_in_the_lockfile_is_one_version() -> None:
    entries = _katex_entries()
    assert entries, "no katex entry in package-lock.json: re-derive this rail"
    versions = {version for _, version in entries}
    assert len(versions) == 1, (
        "math is rendered by one KaTeX and styled by another: package-lock.json holds\n"
        + "\n".join(f"    {path}  {version}" for path, version in entries)
        + "\n  Put every dependent on the web workspace's katex with the root package.json "
        "`overrides` entry, then `npm update katex` from the repository root."
    )


def test_the_one_katex_is_one_the_web_workspace_declares() -> None:
    spec = json.loads(_WEB_MANIFEST.read_text(encoding="utf-8"))["dependencies"]["katex"]
    entries = _katex_entries()
    assert entries, "no katex entry in package-lock.json: re-derive this rail"
    outside = [(path, version) for path, version in entries if not _caret_admits(spec, version)]
    assert not outside, (
        f"web/package.json declares katex {spec}, but package-lock.json installs\n"
        + "\n".join(f"    {path}  {version}" for path, version in outside)
        + "\n  The root `overrides` entry for katex must move with the web workspace's own range."
    )


def test_the_range_check_reads_npm_caret_ranges() -> None:
    """The check above is only as good as this reading of ``^``."""
    assert _caret_admits("^0.19.0", "0.19.0") and _caret_admits("^0.19.0", "0.19.4")
    assert not _caret_admits("^0.19.0", "0.20.0") and not _caret_admits("^0.19.0", "0.16.47")
    assert _caret_admits("^1.2.3", "1.9.0") and not _caret_admits("^1.2.3", "2.0.0")
    assert not _caret_admits("^0.0.3", "0.0.4") and not _caret_admits("^1.2.3", "1.2.2")
