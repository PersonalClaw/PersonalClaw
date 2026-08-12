"""Self-update on uv venvs, and surfacing the real failure (issue #51).

The pip-kind updater hardcoded ``python -m pip install -U``, which does not exist
in a ``uv venv`` — so Settings → Updates showed "Update failed — pip upgrade
failed" forever on the uv install path, while the panel itself already LABELLED
that kind "pip / uv install". Worse, the actual cause (``No module named pip``) was
captured and logged but never sent to the UI, so the only way to learn anything was
to read gateway.log.

Hermetic: the installer probes and the subprocess are both faked, so these pass on
a pip venv and a uv venv alike.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from personalclaw import _installer
from personalclaw import self_update as su
from personalclaw.dashboard.handlers import updates as upd


class _StateStub:
    def __init__(self) -> None:
        self._background_tasks: set = set()
        self.progress: list[tuple[str, str]] = []
        self.refreshes: list[str] = []

    def push_refresh(self, *kinds: str) -> None:
        self.refreshes.extend(kinds)

    def push_update_progress(self, step: str, detail: str = "") -> None:
        self.progress.append((step, detail))


class _Proc:
    """Stand-in for the upgrade subprocess."""

    def __init__(self, rc: int, stderr: bytes = b"") -> None:
        self.returncode = rc
        self._stderr = stderr

    async def communicate(self):
        return b"", self._stderr

    def kill(self):  # pragma: no cover — only the timeout path calls this
        pass


@pytest.fixture
def spawn(monkeypatch):
    """Capture the argv the updater would spawn; serve a canned result."""
    seen: list[list[str]] = []

    def _install(proc: _Proc):
        async def _fake_exec(*argv, **kw):
            seen.append(list(argv))
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
        return seen

    return _install


async def _run_apply(state, monkeypatch, *, latest="0.1.2"):
    """Drive _apply_pip_update's inner coroutine with a stubbed status + re-exec."""

    async def _fake_status(_cur):
        return {"latest": latest}

    monkeypatch.setattr("personalclaw.self_update.build_update_status", _fake_status)

    async def _fake_reexec(_state, **kw):
        state.progress.append(("reexec", ""))

    monkeypatch.setattr(upd, "_graceful_reexec", _fake_reexec)
    monkeypatch.setattr(upd, "_live_auth_mode", lambda _r: "token")

    captured: list = []
    monkeypatch.setattr(
        upd.asyncio,
        "create_task",
        lambda coro: captured.append(coro) or asyncio.ensure_future(coro),
    )
    from aiohttp.test_utils import make_mocked_request

    req = make_mocked_request("POST", "/api/update")
    req.app["state"] = state
    await upd._apply_pip_update(req, state)
    # The apply runs as a background task; let it finish.
    for _ in range(50):
        await asyncio.sleep(0)
        if state.progress:
            break
    await asyncio.sleep(0.05)
    return state.progress


@pytest.mark.asyncio
async def test_uses_uv_when_the_venv_has_no_pip(monkeypatch, spawn):
    """The #51 repro: self-update must work on a uv venv."""
    monkeypatch.setattr(_installer, "_have_uv", lambda: True)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)
    seen = spawn(_Proc(0))
    state = _StateStub()

    await _run_apply(state, monkeypatch)

    assert seen, "no upgrade subprocess was spawned"
    argv = seen[0]
    assert argv[:3] == ["uv", "pip", "install"]
    assert "--python" in argv and argv[argv.index("--python") + 1] == sys.executable
    assert "personalclaw==0.1.2" in argv
    steps = [s for s, _ in state.progress]
    assert "error" not in steps


@pytest.mark.asyncio
async def test_failure_detail_reaches_the_ui(monkeypatch, spawn):
    """Before the fix the panel showed the static "pip upgrade failed" while the
    real cause sat in gateway.log. The user must be able to SEE the cause."""
    monkeypatch.setattr(_installer, "_have_uv", lambda: False)
    monkeypatch.setattr(_installer, "_have_pip", lambda: True)
    spawn(_Proc(1, b"ERROR: Could not find a version that satisfies personalclaw==9.9.9\n"))
    state = _StateStub()

    await _run_apply(state, monkeypatch, latest="9.9.9")

    errors = [d for s, d in state.progress if s == "error"]
    assert errors, f"no error progress pushed: {state.progress}"
    assert "Could not find a version" in errors[0]
    assert errors[0] != "pip upgrade failed"


# ── the UI-facing error summary ────────────────────────────────────────────────


def test_summary_strips_ansi_and_leads_with_uvs_headline():
    """Both defects found by driving the real panel.

    uv COLORIZES its diagnostics, so the raw bytes carry SGR escapes that render
    literally in the browser. And its resolver error is a multi-line tree whose
    headline is FIRST — taking the last line yielded the useless fragment
    "unsatisfiable." with no subject.
    """
    raw = (
        "\x1b[31m×\x1b[0m No solution found when resolving dependencies:\n"
        "\x1b[31m  ╰─▶ \x1b[0mBecause there is no version of personalclaw==99.9.9 and you require\n"
        "\x1b[31m      \x1b[0mpersonalclaw==99.9.9, we can conclude that your requirements are\n"
        "\x1b[31m      \x1b[0munsatisfiable."
    )
    out = su.installer_error_summary(raw)
    assert "\x1b" not in out and "[31m" not in out
    assert out.startswith("No solution found when resolving dependencies")
    assert out != "unsatisfiable."


def test_summary_prefers_pips_explicit_error_line():
    raw = "Collecting personalclaw==9.9.9\nERROR: Could not find a version that satisfies it"
    out = su.installer_error_summary(raw)
    assert out.startswith("ERROR: Could not find a version")


def test_summary_keeps_the_no_module_named_pip_case_readable():
    """The original #46/#51 symptom must still come through intact."""
    out = su.installer_error_summary("/x/.venv/bin/python: No module named pip")
    assert "No module named pip" in out


def test_summary_is_bounded_and_empty_safe():
    assert su.installer_error_summary("") == ""
    assert len(su.installer_error_summary("x" * 5000)) <= 200


@pytest.mark.asyncio
async def test_no_installer_reports_the_real_reason_without_spawning(monkeypatch):
    """With neither installer, don't spawn anything — say what's missing."""
    monkeypatch.setattr(_installer, "_have_uv", lambda: False)
    monkeypatch.setattr(_installer, "_have_pip", lambda: False)

    async def _unreachable(*a, **kw):  # pragma: no cover
        raise AssertionError("spawned a subprocess with no installer available")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _unreachable)
    state = _StateStub()

    await _run_apply(state, monkeypatch)

    errors = [d for s, d in state.progress if s == "error"]
    assert errors, f"no error pushed: {state.progress}"
    assert "uv" in errors[0]
