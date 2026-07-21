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
