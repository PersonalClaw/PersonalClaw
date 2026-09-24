"""Tests for the Files path-completion endpoint (Files P4)."""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import files as F


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alps").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "afile.txt").write_text("x")
    # Allow anything under tmp_path.
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("R", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw: str(raw) if str(raw).startswith(str(tmp_path)) else None,
    )
    return tmp_path


def _call(path: str, kind: str = ""):
    qs = urlencode({"path": path, "kind": kind})
    req = make_mocked_request("GET", f"/api/file-complete?{qs}")
    resp = asyncio.run(F.api_file_complete(req))
    return resp.status, json.loads(resp.body.decode())


def test_completes_prefix(root):
    status, body = _call(f"{root}/al")
    names = {s["name"] for s in body["suggestions"]}
    assert status == 200
    assert "alpha" in names and "alps" in names
    assert "beta" not in names


def test_kind_dir_excludes_files(root):
    _, body = _call(f"{root}/a", "dir")
    names = {s["name"] for s in body["suggestions"]}
    assert "afile.txt" not in names
    assert "alpha" in names


def test_trailing_slash_lists_all_children(root):
    _, body = _call(f"{root}/")
    names = {s["name"] for s in body["suggestions"]}
    assert {"alpha", "alps", "beta"} <= names


def test_outside_roots_returns_empty(root, monkeypatch):
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: None)
    status, body = _call("/etc/")
    assert status == 200
    assert body["suggestions"] == []


# ── #426: the window must be the real top-N, not an arbitrary slice ──────────
# The completer used to `break` out of the scandir loop at `limit` and sort the
# survivors, so the answer was `limit` entries in FILESYSTEM order, re-sorted to look
# alphabetical — a directory that exists and matches the prefix was silently never
# offered, with no `truncated` flag to admit it. These tests drive the iteration order
# explicitly rather than trusting the filesystem's: scandir order is arbitrary, so a
# test that merely asserted "the result is sorted" would pass on the broken code
# whenever the two orders happened to agree.


class _ReversedScandir:
    """`os.scandir` that yields a directory's entries in DESCENDING name order.

    Pins the ordering contract against the one variable the defect depended on. The
    entries are the real `DirEntry`s, so `is_dir`/`path` behave normally.
    """

    def __init__(self, real):
        self._real = real
        self._ctx = None

    def __call__(self, path):
        with self._real(path) as it:
            entries = sorted(it, key=lambda de: de.name, reverse=True)
        self._ctx = entries
        return self

    def __enter__(self):
        return iter(self._ctx)

    def __exit__(self, *exc):
        return False


@pytest.fixture
def wide_root(tmp_path, monkeypatch):
    """Ten directories `d00`…`d09` plus ten files, listed in descending order."""
    for i in range(10):
        (tmp_path / f"d{i:02d}").mkdir()
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("R", str(tmp_path))])
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw: str(raw) if str(raw).startswith(str(tmp_path)) else None,
    )
    monkeypatch.setattr(F.os, "scandir", _ReversedScandir(os.scandir))
    return tmp_path


def _names(path: str, limit: int) -> list[str]:
    qs = urlencode({"path": path, "limit": str(limit)})
    req = make_mocked_request("GET", f"/api/file-complete?{qs}")
    body = json.loads(asyncio.run(F.api_file_complete(req)).body.decode())
    return [s["name"] for s in body["suggestions"]]


def test_window_is_the_sorted_top_n_not_a_filesystem_order_slice(wide_root):
    """`limit=3` over 20 entries returns the three alphabetically-first DIRECTORIES.

    Under the truncate-then-sort order this returned `d09`, `d08`, `d07` — internally
    sorted, and not the top three. This is the whole of #426: the omission is invisible
    because what comes back *is* ordered.
    """
    assert _names(f"{wide_root}/", 3) == ["d00", "d01", "d02"]


def test_dirs_still_precede_files_across_the_cut(wide_root):
    """The sort key is unchanged — directories first, then files, each case-folded. A
    `limit` that spans the boundary must not promote a file above a directory."""
    assert _names(f"{wide_root}/", 11) == [f"d{i:02d}" for i in range(10)] + ["f00.txt"]


def test_refused_candidates_do_not_consume_the_window(wide_root, monkeypatch):
    """The allowlist runs AFTER the sort (that is what keeps it at ~`limit` calls on a
    9684-entry directory), so a refused entry must be skipped over rather than counted:
    the window still fills to `limit` from further down the ordered set."""
    real = F._validate_dashboard_path
    monkeypatch.setattr(
        F,
        "_validate_dashboard_path",
        lambda raw: None if os.path.basename(str(raw)) in {"d00", "d01"} else real(raw),
    )
    assert _names(f"{wide_root}/", 3) == ["d02", "d03", "d04"]


# ── Screenshot capture endpoint (POST /api/screenshot) ──────────────────────
# The desktop screenshot bridge: macOS `screencapture -i` → attach the PNG. The
# interactive crosshair + a real display can't run headlessly, so these cover the
# two deterministic branches: non-macOS degradation (400) and user-cancel (empty
# path, no error). The success path is exercised as-a-user via Chrome DevTools MCP.


def test_screenshot_unavailable_off_macos(monkeypatch):
    """Non-macOS hosts have no `screencapture` — degrade with a clear 400, never
    spawn a subprocess. This is the server half of the FE `useIsMac` gate."""
    monkeypatch.setattr(F.sys, "platform", "linux")

    def _boom(*a, **k):  # must never be called on a non-mac host
        raise AssertionError("screencapture must not be spawned off macOS")

    monkeypatch.setattr(F.asyncio, "create_subprocess_exec", _boom)
    req = make_mocked_request("POST", "/api/screenshot")
    resp = asyncio.run(F.api_screenshot(req))
    assert resp.status == 400
    assert "macOS" in json.loads(resp.body.decode())["error"]


def test_screenshot_cancel_returns_empty_path(monkeypatch):
    """User cancels the region select (Esc) → `screencapture` writes no file →
    endpoint returns an empty path (NOT an error) so the FE no-ops cleanly."""
    monkeypatch.setattr(F.sys, "platform", "darwin")

    class _Proc:
        returncode = 0

        async def wait(self):
            return 0

    async def _fake_exec(*a, **k):
        return _Proc()  # note: writes NO file → dest.exists() is False

    monkeypatch.setattr(F.asyncio, "create_subprocess_exec", _fake_exec)
    req = make_mocked_request("POST", "/api/screenshot")
    resp = asyncio.run(F.api_screenshot(req))
    assert resp.status == 200
    assert json.loads(resp.body.decode())["path"] == ""
