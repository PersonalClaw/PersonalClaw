"""Tests for the Files content-search endpoint (Files P3).

Exercises the Python fallback path directly (deterministic, no ripgrep
dependency) plus the HTTP handler's validation + engine reporting.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import files as F


@pytest.fixture
def search_root(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("import os\nNEEDLE_here = 1\n")
    (tmp_path / "b.txt").write_text("nothing relevant\nNEEDLE_here too\n")
    (tmp_path / "notes.md").write_text("NEEDLE_here at the root\n")
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.md").write_text("NEEDLE_here below the root\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "c.py").write_text("NEEDLE_here ignored\n")
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Root", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw, allowed_roots=None: raw if str(raw).startswith(str(tmp_path)) else None,
    )
    monkeypatch.setattr(F, "_sel", lambda: MagicMock())
    return tmp_path


# ── Python fallback (unit) ──


def test_python_search_finds_matches(search_root):
    results, truncated = F._content_search_python(str(search_root), "needle_here", "")
    files = {r["file"].split("/")[-1] for r in results}
    assert "a.py" in files and "b.txt" in files
    assert not truncated


def test_python_search_skips_ignored_dirs(search_root):
    results, _ = F._content_search_python(str(search_root), "needle_here", "")
    assert all("node_modules" not in r["file"] for r in results)


def test_python_search_glob_filter(search_root):
    results, _ = F._content_search_python(str(search_root), "needle_here", "*.py")
    assert {r["file"].split("/")[-1] for r in results} == {"a.py"}


@pytest.mark.parametrize(
    ("include", "required_path"),
    [
        ("*.md", "workspace/notes.md"),
        ("**/*.md", "notes.md"),
        ("**/*.md", "workspace/notes.md"),
        ("workspace/*.md", "workspace/notes.md"),
        ("notes.md", "workspace/notes.md"),
    ],
)
def test_python_search_glob_filter_matches_relative_paths(search_root, include, required_path):
    results, _ = F._content_search_python(str(search_root), "needle_here", include)
    paths = {Path(r["file"]).relative_to(search_root).as_posix() for r in results}

    assert required_path in paths


def test_python_search_reports_line_and_col(search_root):
    results, _ = F._content_search_python(str(search_root), "needle_here", "*.py")
    r = results[0]
    assert r["line"] == 2 and r["col"] >= 1


def test_python_search_resolves_allowlist_once(tmp_path, monkeypatch):
    for index in range(4):
        (tmp_path / f"file-{index}.txt").write_text("ordinary content\n")

    calls = 0

    def counted_roots():
        nonlocal calls
        calls += 1
        return [("Root", str(tmp_path))]

    monkeypatch.setattr(F, "_dashboard_roots", counted_roots)

    results, truncated = F._content_search_python(str(tmp_path), "absent", "")

    assert results == []
    assert not truncated
    assert calls == 1


# ── Deadline / stop-event rail (drives the REAL fallback, not a stand-in) ──


class _TripAfter:
    """``stop_event`` lookalike that answers ``True`` from the ``nth`` call on."""

    def __init__(self, nth: int) -> None:
        self._nth = nth
        self.calls = 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls >= self._nth


@pytest.fixture
def one_file_root(tmp_path, monkeypatch):
    (tmp_path / "one.txt").write_text(
        "needle_here 1\nneedle_here 2\nneedle_here 3\nneedle_here 4\n"
    )
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Root", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw, allowed_roots=None: raw if str(raw).startswith(str(tmp_path)) else None,
    )
    return tmp_path


def test_content_search_timed_out_is_not_an_oserror():
    # TimeoutError is an OSError subclass, so that lineage would put the stop signal
    # inside the reach of the fallback's ``except OSError: continue`` read handler.
    assert not issubclass(F._ContentSearchTimedOut, OSError)


def test_one_file_root_search_is_complete_without_a_stop_event(one_file_root):
    results, truncated = F._content_search_python(str(one_file_root), "needle_here", "")
    assert len(results) == 4
    assert not truncated


@pytest.mark.parametrize("nth", [1, 2, 3, 4, 5, 6])
def test_stop_event_leaves_the_walk_at_every_check(one_file_root, nth):
    # Checks 1-2 are the os.walk and per-file checks (outside the file-read ``try``);
    # 3-6 are the per-line check INSIDE it, which used to be swallowed and return a
    # partial set claiming truncated=False.
    with pytest.raises(F._ContentSearchTimedOut):
        F._content_search_python(str(one_file_root), "needle_here", "", stop_event=_TripAfter(nth))


def test_expired_deadline_leaves_the_walk(one_file_root):
    # Trips at the os.walk check, so this is a sanity rail on the deadline arm, not a
    # discriminator for the swallow — it passed on the unfixed code too.
    with pytest.raises(F._ContentSearchTimedOut):
        F._content_search_python(
            str(one_file_root), "needle_here", "", deadline=time.monotonic() - 1.0
        )


# ── HTTP handler ──


def _call(path: str, q: str = "", include: str = "", *, force_python=True, monkeypatch=None):
    from urllib.parse import urlencode

    if force_python and monkeypatch is not None:
        monkeypatch.setattr(F, "_has_rg", lambda: False)
    qs = urlencode({"path": path, "q": q, "include": include})
    req = make_mocked_request("GET", f"/api/file-content-search?{qs}")
    resp = asyncio.run(F.api_file_content_search(req))
    return resp.status, json.loads(resp.body.decode())


def test_handler_returns_results(search_root, monkeypatch):
    status, body = _call(str(search_root), "needle_here", monkeypatch=monkeypatch)
    assert status == 200
    assert body["engine"] == "python"
    assert len(body["results"]) >= 2


def test_handler_empty_query_returns_empty(search_root, monkeypatch):
    status, body = _call(str(search_root), "", monkeypatch=monkeypatch)
    assert status == 200
    assert body["results"] == []


def test_handler_python_timeout_returns_structured_error(search_root, monkeypatch):
    monkeypatch.setattr(F, "_CONTENT_SEARCH_TIMEOUT", 0.02)

    def slow_search(
        root,
        query,
        include,
        allowed_roots=None,
        *,
        deadline=None,
        stop_event=None,
    ):
        finish = time.monotonic() + 0.5
        while time.monotonic() < finish:
            if stop_event is not None and stop_event.wait(0.001):
                raise F._ContentSearchTimedOut
        return [], False

    monkeypatch.setattr(F, "_content_search_python", slow_search)

    started = time.monotonic()
    status, body = _call(str(search_root), "needle", monkeypatch=monkeypatch)
    elapsed = time.monotonic() - started

    assert status == 504
    assert body == {
        "error": {
            "code": "file_content_search_timeout",
            "message": (
                "File content search exceeded its time limit. "
                "Narrow the directory or include glob and try again."
            ),
        }
    }
    assert elapsed < 0.25


def test_handler_invalid_dir_400(monkeypatch):
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw, allowed_roots=None: None)
    status, _ = _call("/etc", "x", monkeypatch=monkeypatch)
    assert status == 400


def test_handler_redacts_secrets_in_preview(tmp_path, monkeypatch):
    (tmp_path / "leak.txt").write_text(
        "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLEKEY1234567890abcd needle\n"
    )
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("R", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw, allowed_roots=None: raw if str(raw).startswith(str(tmp_path)) else None,
    )
    monkeypatch.setattr(F, "_sel", lambda: MagicMock())
    status, body = _call(str(tmp_path), "needle", monkeypatch=monkeypatch)
    assert status == 200
    assert all("AKIAIOSFODNN7EXAMPLEKEY1234567890abcd" not in r["preview"] for r in body["results"])
