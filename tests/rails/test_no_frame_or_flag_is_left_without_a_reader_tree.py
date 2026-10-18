"""The source-tree checks split from
``tests/test_no_frame_or_flag_is_left_without_a_reader.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re
from pathlib import Path

import source_index
from no_frame_or_flag_is_left_without_a_reader_shared import _REPO
from table_rows import row

#: A frame name sent through the dashboard's broadcast, as a literal first argument.
_SENT = re.compile(r"""broadcast_ws(?:_subagent_subscribers)?\(\s*["']([a-z_.:-]+)["']""")


def _python_sources() -> list[Path]:
    return [p for p in source_index.package_files() if "static" not in p.parts]


def _client_sources() -> list[Path]:
    """The code that reads the dashboard socket: the SPA, the desktop shell, the phone page."""
    roots = [_REPO / "web" / "src", _REPO / "desktop", _REPO / "mobile"]
    out: list[Path] = []
    for root in roots:
        # web/src is the suite's one source index; the desktop shell and the phone page, outside
        # its four roots, are walked here.
        walk = source_index.web_files() if root == _REPO / "web" / "src" else root.rglob("*")
        for p in walk:
            if p.suffix not in {".ts", ".tsx", ".js", ".mjs", ".cjs"} or not p.is_file():
                continue
            if "node_modules" in p.parts or ".test." in p.name or "test" in p.parts:
                continue
            out.append(p)
    return sorted(out)


#: The frames retired with nothing left reading them.
RETIRED = ["heartbeat", "chat_status", "turn_steerable"]


def test_no_turn_sends_a_retired_frame(subtests) -> None:
    sent: set[str] = set()
    for p in _python_sources():
        sent.update(_SENT.findall(source_index.text(p)))
    # Positive control: the census sees the frames a turn really sends.
    assert {"chat_done", "chat_chunk", "tool_call", "work_phase"} <= sent, sorted(sent)
    for frame in RETIRED:
        with row(subtests, frame=frame):
            assert frame not in sent


def test_no_client_reads_a_retired_frame(subtests) -> None:
    dispatch = re.compile(r"""["'`]chat_done["'`]""")
    sources = {p: p.read_text(encoding="utf-8") for p in _client_sources()}
    # Positive control: the scan reads the files that dispatch on frame types.
    assert any(dispatch.search(text) for text in sources.values())
    for frame in RETIRED:
        literal = re.compile(rf"""["'`]{frame}["'`]""")
        with row(subtests, frame=frame):
            readers = [str(p.relative_to(_REPO)) for p, t in sources.items() if literal.search(t)]
            assert readers == []


def test_no_caller_passes_a_preference_to_the_approval_picker() -> None:
    # This check's own module, which names the picker to look for it: since the split, the
    # behaviour half, this rail and their shared helper.
    this_module = {
        Path(__file__).resolve(),
        (_REPO / "tests" / "test_no_frame_or_flag_is_left_without_a_reader.py").resolve(),
        (_REPO / "tests" / "no_frame_or_flag_is_left_without_a_reader_shared.py").resolve(),
    }
    callers = []
    package = list(source_index.package_files())
    for p in [*package, *sorted((_REPO / "tests").rglob("*.py"))]:
        if p.resolve() in this_module:
            continue
        text = source_index.text(p) if p in package else p.read_text(encoding="utf-8")
        if "select_allow_option_id(" in text:
            callers.append((p, text))
    # Positive control: the one production caller is found.
    assert any(p.name == "session.py" and p.parent.name == "acp" for p, _ in callers)
    passing = [str(p.relative_to(_REPO)) for p, text in callers if "prefer_always" in text]
    assert passing == []
