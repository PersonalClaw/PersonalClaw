"""Self-update of a wheel install with the tool that made it, and surfacing the real failure.

The pip-kind updater hardcoded ``python -m pip install -U``, which does not exist
in a ``uv venv`` — so Settings → Updates showed "Update failed — pip upgrade
failed" forever on the uv install path, while the panel itself already LABELLED
that kind "pip / uv install". Worse, the actual cause (``No module named pip``) was
captured and logged but never sent to the UI, so the only way to learn anything was
to read gateway.log. It now upgrades with the tool that made the environment, and
refuses before anything starts when that tool is not there.

Hermetic: which tool made the environment, the installer probes and the subprocess are
all set, so these pass in an environment pip made and one uv made alike.
"""

from __future__ import annotations

import asyncio
import json
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

    def clear_update_progress(self) -> None:
        self.progress.append(("cleared", ""))


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
def tools(monkeypatch, environment_made_by):
    """``tools(made_by=…, uv=…, pip=…)``: the tool that made the environment, and which exist."""

    def _set(*, made_by: str, uv: bool, pip: bool) -> None:
        environment_made_by(made_by)
        monkeypatch.setattr(_installer, "_have_uv", lambda: uv)
        monkeypatch.setattr(_installer, "_have_pip", lambda: pip)

    return _set


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


async def _run_apply(
    state,
    monkeypatch,
    *,
    latest="0.1.2",
    channel="stable",
    pin="",
    releases=None,
    running="0.0.1",
):
    """Drive _apply_pip_update's inner coroutine with a stubbed release list + re-exec.

    The apply resolves the ``updates`` channel/pin over the releases list and
    installs ``personalclaw==<resolved tag>``. When *releases* is omitted a one-entry
    list carrying *latest* stands in, so the simple cases still read as "install
    ``latest``"; a caller that needs channel/pin distinctions passes an explicit list.
    Only the network seam (`fetch_releases`) is stubbed — the real
    `resolve_wheel_target`/`select_target` run.

    The apply installs only a release that is a move from the version *running*, so that is
    fixed here rather than inherited from the project: ``0.0.1`` is below every release these
    tests name, where the project's own version would make each row fail on the one release
    that equals it.
    """
    import types

    if releases is None:
        releases = [{"tag": f"v{latest}", "prerelease": False}] if latest else []

    monkeypatch.setattr(upd, "_local_version", running)
    cfg = types.SimpleNamespace(updates=types.SimpleNamespace(channel=channel, pin=pin))
    monkeypatch.setattr(upd.AppConfig, "load", staticmethod(lambda: cfg))

    async def _list():
        return [dict(r) for r in releases]

    monkeypatch.setattr("personalclaw.self_update.fetch_releases", _list)

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
    resp = await upd._apply_pip_update(req, state)
    # The apply runs as a background task; let it finish.
    for _ in range(50):
        await asyncio.sleep(0)
        if state.progress:
            break
    await asyncio.sleep(0.05)
    return resp


@pytest.mark.asyncio
async def test_uses_uv_when_the_venv_has_no_pip(monkeypatch, spawn, tools):
    """The #51 repro: self-update must work on a uv venv."""
    tools(made_by="uv", uv=True, pip=False)
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
async def test_failure_detail_reaches_the_ui(monkeypatch, spawn, tools):
    """Before the fix the panel showed the static "pip upgrade failed" while the
    real cause sat in gateway.log. The user must be able to SEE the cause."""
    tools(made_by="pip", uv=False, pip=True)
    spawn(_Proc(1, b"ERROR: Could not find a version that satisfies personalclaw==9.9.9\n"))
    state = _StateStub()

    await _run_apply(state, monkeypatch, latest="9.9.9")

    errors = [d for s, d in state.progress if s == "error"]
    assert errors, f"no error progress pushed: {state.progress}"
    assert "Could not find a version" in errors[0]
    assert errors[0] != "pip upgrade failed"


# ── the pip apply honors the `updates` channel/pin ──────────────────────────────

# Adversarial to a "blind latest" apply: the newest release is a PRERELEASE, so
# stable and beta resolve to DIFFERENT tags and the pin points at an older one. A
# `-U personalclaw==<releases/latest>` apply would install 0.2.1 for all three and
# fail beta + pin — the exact bug RUM-6 removes.
_RUM6_RELEASES = [
    {"tag": "v0.3.0-rc.1", "prerelease": True},
    {"tag": "v0.2.1", "prerelease": False},
    {"tag": "v0.2.0", "prerelease": False},
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel, pin, expected",
    [
        ("stable", "", "personalclaw==0.2.1"),
        ("beta", "", "personalclaw==0.3.0-rc.1"),
        ("stable", "0.2.0", "personalclaw==0.2.0"),
    ],
)
async def test_pip_apply_installs_the_channel_pin_resolved_spec(
    monkeypatch, spawn, tools, channel, pin, expected
):
    """RUM-6 core (dashboard): POST /api/update upgrades to the channel/pin tag.

    Non-vacuous — beta (0.3.0-rc.1) and the pin (0.2.0) resolve to versions other
    than stable's latest (0.2.1) over the same list, so a `releases/latest` apply
    fails the beta and pin rows. Exercises the REAL resolver (`fetch_releases` is the
    only stub)."""
    tools(made_by="uv", uv=True, pip=False)
    seen = spawn(_Proc(0))
    state = _StateStub()

    await _run_apply(state, monkeypatch, channel=channel, pin=pin, releases=_RUM6_RELEASES)

    assert seen, f"no upgrade subprocess was spawned: {state.progress}"
    assert expected in seen[0], f"expected {expected}; argv={seen[0]}"


@pytest.mark.asyncio
async def test_pip_apply_pin_miss_refuses_and_never_installs_latest(monkeypatch, spawn, tools):
    """A pin naming no release must REFUSE — never silently upgrade to the latest wheel."""
    tools(made_by="uv", uv=True, pip=False)
    seen = spawn(_Proc(0))
    state = _StateStub()

    await _run_apply(state, monkeypatch, channel="stable", pin="0.9.9", releases=_RUM6_RELEASES)

    assert not seen, f"installed despite an unmatched pin: {seen}"
    errors = [d for s, d in state.progress if s == "error"]
    assert errors and "pin" in errors[0].lower(), f"progress={state.progress}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "running, channel, pin, said",
    [
        # A build ahead of every release: `==0.2.1` would take it BACK a minor line.
        ("0.3.0", "stable", "", "You're on v0.3.0, newer than the newest release (v0.2.1)."),
        # A running candidate, in the spelling an installed wheel reports, ahead of stable.
        ("0.3.0rc1", "stable", "", "You're on v0.3.0rc1, newer than the newest release (v0.2.1)."),
        # The same candidate on beta IS the newest release, however each side spells it.
        ("0.3.0rc1", "beta", "", "You're on the newest release (v0.3.0-rc.1)."),
        # Pinned to the candidate it runs: nothing to reinstall.
        ("0.3.0rc1", "stable", "0.3.0-rc.1", "Already on the pinned release (v0.3.0-rc.1)."),
    ],
)
async def test_pip_apply_installs_nothing_that_is_not_a_move(
    monkeypatch, spawn, tools, running, channel, pin, said
):
    """POST /api/update on a wheel installs the resolved release only when it is a move.

    It installed `personalclaw==<resolved>` whatever that was, so an install ahead of the
    channel's newest release was DOWNGRADED by "update", and a pinned candidate was reinstalled
    on every apply because its tag and its installed version are spelled differently. Nothing
    restarts either: a wheel has no local change for a restart to pick up.
    """
    tools(made_by="uv", uv=True, pip=False)
    seen = spawn(_Proc(0))
    state = _StateStub()

    await _run_apply(
        state, monkeypatch, channel=channel, pin=pin, releases=_RUM6_RELEASES, running=running
    )

    assert not seen, f"installed a release that is not a move: {seen}"
    assert ("done", said) in state.progress, f"progress={state.progress}"
    assert ("reexec", "") not in state.progress


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
@pytest.mark.parametrize("made_by", ["uv", "pip"])
async def test_no_installer_reports_the_real_reason_without_spawning(monkeypatch, tools, made_by):
    """Without the tool that made the environment, nothing starts: the Update is answered with
    the sentence that names what to run, before the apply claims the slot or says "updating"."""
    tools(made_by=made_by, uv=False, pip=False)

    async def _unreachable(*a, **kw):  # pragma: no cover
        raise AssertionError("spawned a subprocess with no installer available")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _unreachable)
    state = _StateStub()

    resp = await _run_apply(state, monkeypatch)

    assert resp.status == 409
    error = json.loads(resp.text)["error"]
    assert error["code"] == "update_installer_missing"
    assert error["message"].startswith(f"Nothing was changed: {made_by} made")
    assert "personalclaw update" in error["message"]
    assert state.progress == [] and state.refreshes == []
    assert upd._apply_in_flight is False
