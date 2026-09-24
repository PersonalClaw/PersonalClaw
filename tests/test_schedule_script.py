"""Unit tests for zero-token Schedule execution modes.

Covers run_script_sandboxed (ok/skip/done/report/error via fixture scripts under
a fake crons dir), resolve_script_path guards, and the exec-mode strategy axis on
ScheduleJob. (Command-mode execution is the bash action provider — see
test_native_hook_providers.py.)
"""

from __future__ import annotations

import json
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import personalclaw.schedule_script as ss
from personalclaw import gateway_base
from personalclaw.schedule import (
    ScheduleJob,
    make_agent_action,
    make_command_action,
    make_script_action,
)

# The test scripts return instantly; the timeout is only a hung-script safety
# net. It must clear worst-case latency, though: each run spawns a fresh
# interpreter through the sandbox, and under full-suite xdist load (10 workers
# all forking at once) a spawn that takes 0.3s in isolation can take 40-50s of
# wall time from pure CPU contention. Give wide headroom over that — still well
# under pytest's 120s per-test ceiling, and a genuinely hung script is caught.
_SCRIPT_TIMEOUT = 90


@pytest.fixture(autouse=True)
def _a_gateway_to_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Declare a gateway for the cron launcher to address.

    ``run_script_sandboxed`` resolves the launcher's port from ``gateway_base`` at CALL time
    and REFUSES rather than assuming the default port when it cannot (#2539) — the previous
    ``config.loader.DASHBOARD_PORT`` was an import-time constant that happily named a port
    another instance was listening on. These tests exercise the sandbox, the resource ceiling
    and the child environment, not the port, so they state one and move on.
    """
    monkeypatch.setenv(gateway_base.PORT_ENV, "7777")


# ── exec_mode strategy axis ───────────────────────────────────────────


def test_exec_mode_axis() -> None:
    assert (
        ScheduleJob(id="a", name="n", action=make_command_action("echo x")).exec_mode == "command"
    )
    assert (
        ScheduleJob(id="b", name="n", action=make_script_action("crons/x.py:run")).exec_mode
        == "script"
    )
    assert ScheduleJob(id="c", name="n", action=make_agent_action(message="m")).exec_mode == "agent"


# ── resolve_script_path guards ────────────────────────────────────────


def _fake_crons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    crons = tmp_path / "crons"
    crons.mkdir()
    monkeypatch.setattr(ss, "_crons_dir", lambda: crons)
    # validate_file_path must accept paths under tmp; patch it to a thin guard
    # so the test doesn't depend on the global sensitive-path config.
    monkeypatch.setattr(ss, "validate_file_path", lambda p: p)
    return crons


def test_resolve_script_path_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crons = _fake_crons(monkeypatch, tmp_path)
    script = crons / "mon.py"
    script.write_text("def run(ctx):\n    return 'ok'\n")
    resolved, func = ss.resolve_script_path(f"{script}:run")
    assert resolved == script.resolve()
    assert func == "run"


def test_resolve_script_path_rejects_missing_func(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_crons(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        ss.resolve_script_path("crons/x.py")  # no :func


def test_resolve_script_path_rejects_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_crons(monkeypatch, tmp_path)
    outside = tmp_path / "evil.py"
    outside.write_text("def run(ctx): pass\n")
    with pytest.raises(ValueError):
        ss.resolve_script_path(f"{outside}:run")  # not under crons/


def test_resolve_script_path_rejects_non_py(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    crons = _fake_crons(monkeypatch, tmp_path)
    f = crons / "x.sh"
    f.write_text("echo hi")
    with pytest.raises(ValueError):
        ss.resolve_script_path(f"{f}:run")


# ── script mode (ok / skip / done / report / error) ───────────────────


def _write_script(crons: Path, name: str, body: str) -> str:
    (crons / name).write_text(textwrap.dedent(body))
    return f"{crons / name}:run"


def test_run_script_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "ok.py",
        """
        def run(ctx):
            return "done-value"
    """,
    )
    r = ss.run_script_sandboxed(spec, "job1", "the message", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "ok"
    assert r["message"] == "done-value"


def test_run_script_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "skip.py",
        """
        from personalclaw.schedule_script import Skip
        def run(ctx):
            raise Skip()
    """,
    )
    r = ss.run_script_sandboxed(spec, "job2", "", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "skip"


def test_run_script_done_and_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crons = _fake_crons(monkeypatch, tmp_path)
    done_spec = _write_script(
        crons,
        "done.py",
        """
        from personalclaw.schedule_script import Done
        def run(ctx):
            raise Done("all finished")
    """,
    )
    r = ss.run_script_sandboxed(done_spec, "j", "", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "done"
    assert r["message"] == "all finished"

    rep_spec = _write_script(
        crons,
        "report.py",
        """
        from personalclaw.schedule_script import Report
        def run(ctx):
            raise Report("status update")
    """,
    )
    r2 = ss.run_script_sandboxed(rep_spec, "j", "", timeout=_SCRIPT_TIMEOUT)
    assert r2["status"] == "report"
    assert r2["message"] == "status update"


def test_run_script_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "boom.py",
        """
        def run(ctx):
            raise RuntimeError("kaboom")
    """,
    )
    r = ss.run_script_sandboxed(spec, "j", "", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "error"
    assert "kaboom" in r["error"]


def test_run_script_receives_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "echo.py",
        """
        def run(ctx):
            return "msg=" + ctx.message
    """,
    )
    r = ss.run_script_sandboxed(spec, "j", "hello-args", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "ok"
    assert r["message"] == "msg=hello-args"


# ── ctx.call_tool reads a refusal instead of dying on it (#3407) ──────


class _StubGatewayHandler(BaseHTTPRequestHandler):
    """Answers /api/tools/invoke the way the real route does, both arms.

    403 + the ``{"error": {...}}`` envelope without ``confirm_risk``; 200 + the flat
    success dict with it. Same request shape both ways, so the only difference the
    launcher sees is the status code.
    """

    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args: object) -> None:  # keep pytest output clean
        return

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw.decode("utf-8")) if raw else {}
        if body.get("confirm_risk") == "destructive":
            payload = {"ok": True, "output": "control: happy path", "error": ""}
            status = 200
        else:
            payload = {
                "error": {
                    "code": "risk_confirmation_required",
                    "message": "'bash' resolves as a DESTRUCTIVE call. Re-send with "
                    '"confirm_risk": "destructive" to run it.',
                    "risk": "destructive",
                    "confirm_field": "confirm_risk",
                }
            }
            status = 403
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture
def stub_gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[HTTPServer]:
    """A real socket the launcher's ``_post`` can address, on an ephemeral port."""
    server = HTTPServer(("127.0.0.1", 0), _StubGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(gateway_base.PORT_ENV, str(server.server_address[1]))
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_call_tool_returns_a_refusal_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stub_gateway: HTTPServer
) -> None:
    """A 403 refusal must reach the author as a dict, carrying the reason (#3407).

    ``urlopen`` raises ``HTTPError`` on every 4xx and discards the body, so every
    refusal the invoke route can issue used to be unreadable — including the one
    ``call_tool``'s own docstring teaches authors to guard against. The script below
    is that guard, verbatim from the docstring.
    """
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "refused.py",
        """
        def run(ctx):
            r = ctx.call_tool("bash", {"command": "rm -rf /tmp/cache"})
            if not r["ok"]:
                return "guard ran: %s | code=%s | status=%s" % (
                    r["error"], r.get("code"), r.get("status"))
            return "guard did NOT run"
    """,
    )
    r = ss.run_script_sandboxed(spec, "j", "", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "ok", r
    assert r["message"].startswith("guard ran: ")
    assert "DESTRUCTIVE call" in r["message"]
    assert "code=risk_confirmation_required" in r["message"]
    assert "status=403" in r["message"]


def test_call_tool_happy_path_still_returns_the_documented_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stub_gateway: HTTPServer
) -> None:
    """Control for the test above: the 200 arm is unchanged by the 4xx handling.

    Same stub, same request, one field added. If this red the refusal test would be
    measuring a broken instrument rather than the ``except`` clause.
    """
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "confirmed.py",
        """
        def run(ctx):
            r = ctx.call_tool("bash", {"command": "rm -rf /tmp/cache"},
                              confirm_risk="destructive")
            return "ok=%s out=%s" % (r["ok"], r["output"])
    """,
    )
    r = ss.run_script_sandboxed(spec, "j", "", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "ok", r
    assert r["message"] == "ok=True out=control: happy path"


def test_launcher_handles_every_4xx_the_invoke_route_can_send() -> None:
    """The launcher source must name ``HTTPError``, not just happen to pass one case.

    Cited by attribute rather than by line: ``_LAUNCHER_SRC`` is a string literal, so
    line numbers inside it drift with unrelated edits above.
    """
    assert "urllib.error.HTTPError" in ss._LAUNCHER_SRC
    assert "import urllib.error" in ss._LAUNCHER_SRC
    # Exactly one exit from _post's success path, so no second unhandled urlopen.
    assert ss._LAUNCHER_SRC.count("urlopen(") == 1


def test_secret_not_in_script_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The internal secret must not be readable from the script's environment."""
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "envcheck.py",
        """
        import os
        def run(ctx):
            leaked = [k for k in os.environ if 'SECRET' in k.upper()]
            return "leaked=" + ",".join(leaked)
    """,
    )
    r = ss.run_script_sandboxed(spec, "j", "", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "ok"
    assert r["message"] == "leaked="
