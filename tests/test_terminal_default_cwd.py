"""A new terminal session starts in the WORKSPACE, which is what the UI promises (#544).

The Terminal empty state reads "Open a PTY session to run shell commands in your
workspace." Every session the dashboard created started in `$HOME` instead: the plumbing to
do it right existed end to end — the client method takes a `cwd`, the create handler stashes
it, the WS spawn honors it — and both New-session call sites passed nothing, so the fallback
chain went straight from an unset config key to `$HOME`.

Worse than a cosmetic mismatch: a user following the hint runs their first command in the
wrong tree, and on a normal install `$HOME` is also where `~/.personalclaw` itself lives —
the directory an isolated dev home exists to keep a shell away from.

Resolved SERVER-side (`default_terminal_cwd`) rather than by passing a cwd from each caller.
The promise belongs to the endpoint, and the two New-session buttons, the drawer and the CLI
would each have to remember to send it — which is the bug, not a fix for it. So these tests
assert the RESOLUTION and the create response that advertises it, never a request argument.

`default_workspace_dir()` already refuses a missing or sensitive root by returning `""`, so
the chain still terminates somewhere real; `test_home_remains_the_last_resort` pins that.
"""

import inspect
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.dashboard.handlers import terminal


def _request(body=None):
    """A minimal authenticated create request, mirroring `test_terminal_handler.py`."""
    state = MagicMock()
    state._terminal_sessions = {}
    req = MagicMock()
    req.app = {"state": state}
    req.get = lambda k, default=None: "testuser" if k == "user" else default
    req.remote = "127.0.0.1"
    req.body_exists = body is not None

    # `json()` is defined even for the no-body case, and that is not cosmetic. The handler
    # reads through `request_validation.json_object_body`, which awaits `request.json()`
    # unconditionally; a bare `MagicMock` attribute is not awaitable, so leaving it unset
    # made the double raise `TypeError` from inside the reader. That USED to pass only
    # because the handler wrapped the read in `except Exception: pass` — i.e. this double
    # was being propped up by the swallow that #2923 is about. An absent body is spelled as
    # the empty object the reader itself returns for one.
    async def _json():
        return {} if body is None else body

    req.json = _json
    return req


async def _create(cfg, body=None):
    with (
        patch.object(terminal, "_get_config", return_value={"enabled": True, **cfg}),
        patch.object(terminal, "_sel") as sel,
    ):
        sel.return_value.log_api_access = MagicMock()
        resp = await terminal.api_terminal_create(_request(body))
    return resp.status, json.loads(resp.body)


# ── the resolution ───────────────────────────────────────────────────────────


def test_the_workspace_wins_over_home(tmp_path):
    ws = tmp_path / "personalclaw-workspace"
    ws.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    with (
        patch("personalclaw.config.loader.default_workspace_dir", return_value=str(ws)),
        patch.dict(os.environ, {"HOME": str(home)}, clear=False),
    ):
        assert terminal.default_terminal_cwd({}) == str(ws)


def test_an_explicit_config_cwd_wins_over_the_workspace(tmp_path):
    """`dashboard.terminal.cwd` is a choice the user made; a derived default must not
    override it. Whitespace is not a choice and falls through rather than resolving to ""."""
    ws = tmp_path / "personalclaw-workspace"
    ws.mkdir()
    chosen = tmp_path / "elsewhere"
    chosen.mkdir()
    with patch("personalclaw.config.loader.default_workspace_dir", return_value=str(ws)):
        assert terminal.default_terminal_cwd({"cwd": str(chosen)}) == str(chosen)
        assert terminal.default_terminal_cwd({"cwd": "   "}) == str(ws)


def test_home_remains_the_last_resort(tmp_path):
    """VACUITY FLOOR. `default_workspace_dir()` returns "" when the root is missing or
    sensitive, and the chain has to end somewhere a PTY can actually open."""
    home = tmp_path / "home"
    home.mkdir()
    with (
        patch("personalclaw.config.loader.default_workspace_dir", return_value=""),
        patch.dict(os.environ, {"HOME": str(home)}, clear=False),
    ):
        assert terminal.default_terminal_cwd({}) == str(home)


# ── what the endpoint advertises ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_session_created_with_no_cwd_reports_the_workspace(tmp_path):
    """The UI's actual call: `POST /api/terminal/sessions` with no `cwd`. This used to
    answer `$HOME` under an empty state promising the workspace."""
    ws = tmp_path / "personalclaw-workspace"
    ws.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    with (
        patch("personalclaw.config.loader.default_workspace_dir", return_value=str(ws)),
        patch.dict(os.environ, {"HOME": str(home)}, clear=False),
    ):
        status, body = await _create({})
    assert status == 200
    assert body["cwd"] == str(ws)
    assert body["cwd"] != str(home)


@pytest.mark.asyncio
async def test_a_requested_cwd_still_wins(tmp_path):
    """VACUITY FLOOR the other way: the cockpit opens a terminal in a PROJECT workspace by
    passing one, and a server-side default must not take that away."""
    ws = tmp_path / "personalclaw-workspace"
    ws.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    with patch("personalclaw.config.loader.default_workspace_dir", return_value=str(ws)):
        status, body = await _create({}, body={"cwd": str(project)})
    assert status == 200
    assert body["cwd"] == str(project)


@pytest.mark.asyncio
async def test_the_default_is_still_gated_on_a_safe_root(tmp_path):
    """The workspace default is not a way around the credential/system-dir refusal — that
    guard is on the REQUESTED cwd, and `default_workspace_dir` applies `is_sensitive_path`
    to the root it returns. A requested unsafe dir is still a 403."""
    ws = tmp_path / "personalclaw-workspace"
    ws.mkdir()
    with patch("personalclaw.config.loader.default_workspace_dir", return_value=str(ws)):
        status, _ = await _create({}, body={"cwd": os.path.expanduser("~/.ssh")})
    assert status == 403


# ── one resolver, two readers ────────────────────────────────────────────────


def test_the_spawn_and_the_create_response_share_one_resolver():
    """The WS spawn and the REST create sit in handlers that cannot be driven from one call,
    so what matters is that neither RE-DERIVES the chain. Asserted over source: exactly two
    readers, and the duplicated `cfg.get("cwd") or $HOME` they both used to carry is gone."""
    src = inspect.getsource(terminal)
    assert 'cfg.get("cwd") or os.environ.get("HOME"' not in src
    assert src.count("default_terminal_cwd(cfg)") == 2
