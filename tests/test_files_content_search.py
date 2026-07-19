"""Tests for the Files content-search endpoint (Files P3).

Exercises the Python fallback path directly (deterministic, no ripgrep
dependency) plus the HTTP handler's validation + engine reporting.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import files as F


@pytest.fixture
def search_root(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("import os\nNEEDLE_here = 1\n")
    (tmp_path / "b.txt").write_text("nothing relevant\nNEEDLE_here too\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "c.py").write_text("NEEDLE_here ignored\n")
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("Root", str(tmp_path))])
    monkeypatch.setattr(F, "_validate_dashboard_path",
                        lambda raw: raw if str(raw).startswith(str(tmp_path)) else None)
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


def test_python_search_reports_line_and_col(search_root):
    results, _ = F._content_search_python(str(search_root), "needle_here", "*.py")
    r = results[0]
    assert r["line"] == 2 and r["col"] >= 1


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


def test_handler_invalid_dir_400(monkeypatch):
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: None)
    status, _ = _call("/etc", "x", monkeypatch=monkeypatch)
    assert status == 400


def test_handler_redacts_secrets_in_preview(tmp_path, monkeypatch):
    (tmp_path / "leak.txt").write_text("AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLEKEY1234567890abcd needle\n")
    monkeypatch.setattr(F, "_dashboard_roots", lambda: [("R", str(tmp_path))])
    monkeypatch.setattr(F, "_validate_dashboard_path", lambda raw: raw if str(raw).startswith(str(tmp_path)) else None)
    monkeypatch.setattr(F, "_sel", lambda: MagicMock())
    status, body = _call(str(tmp_path), "needle", monkeypatch=monkeypatch)
    assert status == 200
    assert all("AKIAIOSFODNN7EXAMPLEKEY1234567890abcd" not in r["preview"] for r in body["results"])
