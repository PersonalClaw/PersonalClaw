"""Unit tests for zero-token Schedule execution modes.

Covers run_script_sandboxed (ok/skip/done/report/error via fixture scripts under
a fake crons dir), resolve_script_path guards, and the exec-mode strategy axis on
ScheduleJob. (Command-mode execution is the bash action provider — see
test_native_hook_providers.py.)
"""

from __future__ import annotations

import textwrap
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


# ── ctx.call_tool: a refusal is DATA, not a raise (#3407) ─────────────
#
# ``_post`` used bare ``urlopen``, which raises ``HTTPError`` on every 4xx and discards the
# body — so no script could read any refusal the tool route can issue, and the docstring's
# own teaching example (``risk_confirmation_required``) was precisely the broken case: the
# ``if not r["ok"]`` guard it implies never ran, because the call raised first.


def _docstring_example() -> str:
    """The ``if not r["ok"]`` guard, lifted out of the SHIPPED ``call_tool`` docstring.

    Derived, never retyped. A docstring example that does not run is the defect, so the
    example and the thing under test are the same bytes by construction — if one drifts,
    this is what notices.
    """
    lines = ss._LAUNCHER_SRC.splitlines()
    first = next(i for i, ln in enumerate(lines) if 'r = ctx.call_tool("memory_forget"' in ln)
    last = first
    while lines[last + 1].strip():
        last += 1
    return textwrap.dedent("\n".join(lines[first : last + 1]))


@pytest.fixture
def stub_tool_route(monkeypatch: pytest.MonkeyPatch):
    """A loopback stand-in for ``/api/tools/invoke`` answering the way the real route does.

    403 with the real ``json_error`` envelope when ``confirm_risk`` is absent, 200 with the
    real success shape when it names the tier. Yields the recorded requests, so the test can
    show the request side is IDENTICAL and the status code is the only difference.
    """
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[tuple[str, dict]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's contract
            body = _json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            seen.append((self.path, body))
            if body.get("confirm_risk") == "destructive":
                code, payload = 200, {"ok": True, "output": "control: happy path"}
            else:
                code, payload = 403, {
                    "error": {
                        "code": "risk_confirmation_required",
                        "message": (
                            f"{body.get('tool')!r} resolves as a DESTRUCTIVE call. "
                            'Re-send with "confirm_risk": "destructive" to run it.'
                        ),
                        "risk": "destructive",
                        "confirm_field": "confirm_risk",
                    }
                }
            raw = _json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_a) -> None:  # keep the pytest output readable
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv(gateway_base.PORT_ENV, str(srv.server_address[1]))
    try:
        yield seen
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_403_refusal_reaches_the_docstrings_own_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stub_tool_route: list
) -> None:
    """THE rail: the docstring's example, run verbatim, against a real 403."""
    crons = _fake_crons(monkeypatch, tmp_path)
    body = (
        "def run(ctx):\n"
        + textwrap.indent(_docstring_example(), "    ")
        + '\n    return "NOT REFUSED"\n'
    )
    assert 'if not r["ok"]:' in body, "precondition: the guard really came from the docstring"
    spec = _write_script(crons, "refusal.py", body)

    r = ss.run_script_sandboxed(spec, "j", "", timeout=_SCRIPT_TIMEOUT)

    # The guard ran, which it cannot do if the call raised.
    assert r["status"] == "report", r
    assert r["message"] == "tool refused: risk_confirmation_required"
    assert len(stub_tool_route) == 1
    assert stub_tool_route[0][0] == "/api/tools/invoke"


def test_the_same_call_naming_the_tier_still_returns_the_result_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stub_tool_route: list
) -> None:
    """The control, one field apart: the happy path is unchanged and still a dict."""
    crons = _fake_crons(monkeypatch, tmp_path)
    spec = _write_script(
        crons,
        "acked.py",
        """
        def run(ctx):
            r = ctx.call_tool("memory_forget", {"query": "stale note"},
                              confirm_risk="destructive")
            return "ok=%s output=%s" % (r["ok"], r["output"])
    """,
    )
    r = ss.run_script_sandboxed(spec, "j", "", timeout=_SCRIPT_TIMEOUT)
    assert r["status"] == "ok", r
    assert r["message"] == "ok=True output=control: happy path"
    # Same route, same request shape — only the acknowledgement field differs.
    assert [p for p, _ in stub_tool_route] == ["/api/tools/invoke"]
    assert stub_tool_route[0][1]["confirm_risk"] == "destructive"


def test_a_non_json_4xx_body_is_still_a_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A refusal that is not JSON at all must still not raise past the author."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Plain(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"Not Found")

        def log_message(self, *_a) -> None:
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Plain)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv(gateway_base.PORT_ENV, str(srv.server_address[1]))
    try:
        crons = _fake_crons(monkeypatch, tmp_path)
        spec = _write_script(
            crons,
            "plain404.py",
            """
            def run(ctx):
                r = ctx.call_tool("nope")
                return "ok=%s code=%s status=%s" % (
                    r["ok"], r["error"]["code"], r["status"])
        """,
        )
        r = ss.run_script_sandboxed(spec, "j", "", timeout=_SCRIPT_TIMEOUT)
        assert r["status"] == "ok", r
        assert r["message"] == "ok=False code=http_404 status=404"
    finally:
        srv.shutdown()
        srv.server_close()


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
