"""Cancel stops the update it is pressed on, says what that left, and never claims a cancel that did
not happen.

Pressing Cancel while an update ran only cleared the overlay and said "Update cancelled by user":
the installer kept running, the checkout stayed on the new release, and the gateway restarted into
it moments later. So the owner was told her install was not changing while it changed.

These drive the dashboard's Cancel (``POST /api/update/cancel``) on the updates ``POST /api/update``
starts, a source checkout's one release behind its origin and a wheel's, and ``personalclaw
update``'s Ctrl-C on the same checkout. The installer is the stand-in ``uv`` of
``test_a_failed_update_puts_the_checkout_back``, which keeps installing until it is stopped. Nothing
is installed and nothing restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from personalclaw import checkout_update, cli_server
from personalclaw.dashboard.handlers import updates as upd
from tests.test_a_failed_update_puts_the_checkout_back import (  # noqa: F401 (the fixtures)
    _PUT_BACK,
    _detached,
    _git,
    _gone,
    _request,
    _settle,
    _State,
    _until_started,
    checkout,
    installer,
    release,
)

_CANCELLED = f"The update was cancelled. {_PUT_BACK}"


def _answer(resp) -> dict:
    return json.loads(resp.text)


def _claims(state: _State) -> list[tuple[str, str]]:
    """Every step the update progress said ended the update: a cancel, a failure."""
    return [
        (step, detail)
        for step, detail in state.progress
        if step in {"cancelled", "error", "failed"}
    ]


def _stop_stand_in(pid_file: Path) -> None:
    """Stop a stand-in installer a test left running, so a red test does not leave it behind."""
    if not pid_file.exists() or not pid_file.read_text().strip():
        return
    try:
        os.kill(int(pid_file.read_text()), signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _cancel(state: _State):
    return await upd.api_update_cancel(_request(state))


# ── a checkout's update: Cancel during the install stops it and puts the checkout back ──────


@pytest.mark.asyncio
async def test_cancel_during_the_install_stops_it_and_puts_the_checkout_back(
    checkout, installer, release, caplog  # noqa: F811 - the imported fixtures
) -> None:
    caplog.set_level(logging.INFO, logger="personalclaw")
    clone, old, _new = checkout
    ran = installer("hangs")
    state = _State()
    resp = await upd.api_update_apply(_request(state))
    assert resp.status == 200, resp.text
    pid = await _until_started(ran.pid_file)
    try:
        answer = await _cancel(state)

        assert _gone(pid), "Cancel left the install running"
        assert (
            _git(clone, "rev-parse", "HEAD") == old
        ), "Cancel left the checkout on the new release"
        assert _detached(clone) and _git(clone, "status", "--porcelain") == ""
        assert answer.status == 200, answer.text
        assert _answer(answer)["update_progress"] == {"step": "cancelled", "detail": _CANCELLED}
        assert state.progress[-1] == ("cancelled", _CANCELLED), state.progress
        assert _CANCELLED in caplog.text, "the gateway log does not say what the cancel left"
        await _settle(state)
        assert not release.restarts, "a cancelled update restarted the gateway"
        assert upd._apply_in_flight is False, "a cancelled update must free the slot"
    finally:
        await checkout_update.stop()
        _stop_stand_in(ran.pid_file)
        await _settle(state)


@pytest.mark.asyncio
async def test_a_cancel_that_cannot_put_the_checkout_back_says_where_it_is_and_how(
    checkout, installer, release  # noqa: F811 - the imported fixtures
) -> None:
    clone, old, new = checkout
    ran = installer("locks-hangs")  # another git holds the repository while the install runs
    state = _State()
    await upd.api_update_apply(_request(state))
    pid = await _until_started(ran.pid_file)
    try:
        answer = await _cancel(state)

        assert _gone(pid), "Cancel left the install running"
        progress = _answer(answer).get("update_progress") or {}
        said = " ".join(str(progress.get("detail", "")).split())
        assert progress.get("step") == "error", answer.text
        assert said.startswith(
            "The update was cancelled. The checkout could not be put back on v0.0.1 (fatal: "
            "Unable to create"
        ), said
        assert "so it is on v0.0.2. To put it back on v0.0.1 before PersonalClaw next" in said
        command = said.split("before PersonalClaw next starts, run: ", 1)[1]
        assert shlex.split(command) == ["git", "-C", str(clone), "checkout", "--detach", old]
        assert _git(clone, "rev-parse", "HEAD") == new
        # Once the other git is done, the command it names puts the checkout back.
        (clone / ".git" / "index.lock").unlink()
        subprocess.run(shlex.split(command), check=True, capture_output=True)
        assert _git(clone, "rev-parse", "HEAD") == old and _detached(clone)
    finally:
        await checkout_update.stop()
        _stop_stand_in(ran.pid_file)
        await _settle(state)


# ── past the point where it can stop: Cancel says the update will finish ───────────────────

_INSTALLED = (
    "The new release is installed, so the update can no longer be cancelled: it builds the "
    "dashboard, then restarts PersonalClaw into it."
)
_RESTARTING = "PersonalClaw is restarting, and a restart cannot be cancelled."


@pytest.mark.asyncio
@pytest.mark.parametrize("at", ["building", "restarting"])
async def test_cancel_once_the_new_release_is_installed_says_it_will_finish(
    checkout, installer, release, monkeypatch, at  # noqa: F811 - the imported fixtures
) -> None:
    clone, _old, new = checkout
    installer("ok")
    reached, go_on = asyncio.Event(), asyncio.Event()

    async def _build(*_args, **_kwargs) -> None:
        reached.set()
        await go_on.wait()

    async def _restart(_state, **kwargs) -> None:
        release.restarts.append(kwargs)
        reached.set()
        await go_on.wait()

    if at == "building":
        monkeypatch.setattr(checkout_update, "build_frontend_async", _build)
    else:
        monkeypatch.setattr(upd, "_graceful_reexec", _restart)
    state = _State()
    await upd.api_update_apply(_request(state))
    await asyncio.wait_for(reached.wait(), timeout=15)
    try:
        answer = await _cancel(state)
    finally:
        go_on.set()
        await _settle(state)

    assert answer.status == 409, answer.text
    error = _answer(answer)["error"]
    assert error["code"] == "update_not_cancellable"
    assert error["message"] == (_INSTALLED if at == "building" else _RESTARTING)
    assert not _claims(state), f"Cancel claimed a cancel that did not happen: {state.progress}"
    assert _git(clone, "rev-parse", "HEAD") == new
    assert len(release.restarts) == 1, "the update did not finish"


@pytest.mark.asyncio
async def test_cancel_during_a_restart_says_a_restart_cannot_be_cancelled(
    release, monkeypatch  # noqa: F811 - the imported fixtures
) -> None:
    reached, go_on = asyncio.Event(), asyncio.Event()

    async def _restart(_state, **_kwargs) -> None:
        reached.set()
        await go_on.wait()

    monkeypatch.setattr(upd, "_graceful_reexec", _restart)
    monkeypatch.setattr("personalclaw.restart_request.pending", lambda: object())
    state = _State()
    request = _request(state)
    request.query = {}
    await upd.api_restart(request)
    await asyncio.wait_for(reached.wait(), timeout=15)
    try:
        answer = await _cancel(state)
    finally:
        go_on.set()
        await _settle(state)

    assert answer.status == 409, answer.text
    assert _answer(answer)["error"]["message"] == _RESTARTING
    assert not _claims(state), state.progress


# ── a wheel's upgrade: Cancel stops the installer and says what it had changed ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("does", ["hangs", "changes-hangs"])
async def test_cancel_during_a_wheel_upgrade_stops_the_installer_and_says_what_it_changed(
    installer, release, monkeypatch, tmp_path, does  # noqa: F811 - the imported fixtures
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "pip")
    ran = installer(does)
    state = _State()
    resp = await upd.api_update_apply(_request(state))
    assert _answer(resp).get("kind") == "pip", resp.text
    pid = await _until_started(ran.pid_file)
    try:
        answer = await _cancel(state)

        assert _gone(pid), "Cancel left the upgrade running"
        if does == "hangs":
            expected = {
                "step": "cancelled",
                "detail": "The update was cancelled. Nothing was changed: PersonalClaw is still on "
                "v0.0.1.",
            }
        else:
            expected = {
                "step": "error",
                "detail": "The update was cancelled, but the upgrade had already changed alpha in "
                "PersonalClaw's environment; update again to finish it.",
            }
        assert _answer(answer).get("update_progress") == expected, answer.text
        assert state.progress[-1] == (expected["step"], expected["detail"])
        await _settle(state)
        assert not release.restarts, "a cancelled upgrade restarted the gateway"
        assert upd._apply_in_flight is False
    finally:
        _stop_stand_in(ran.pid_file)
        await _settle(state)


# ── nothing to cancel, and Dismiss ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_with_no_update_running_says_so_and_changes_nothing(
    release,  # noqa: F811 - the imported fixture
) -> None:
    state = _State()
    state.push_update_progress("error", "uv sync failed. Nothing was changed.")

    answer = await _cancel(state)

    assert answer.status == 200
    assert _answer(answer) == {
        "ok": True,
        "status": "not_running",
        "detail": "No update is running, so there was nothing to cancel.",
    }
    assert state.progress == [("error", "uv sync failed. Nothing was changed.")]


@pytest.mark.asyncio
async def test_dismiss_clears_what_an_update_said_and_never_stops_one(
    checkout, installer, release  # noqa: F811 - the imported fixtures
) -> None:
    """Dismiss closes the outcome the overlay shows, so a reload does not show it again. It is a
    separate request from Cancel, so a Dismiss pressed as another update starts cannot stop it."""
    clone, _old, new = checkout
    ran = installer("hangs")
    state = _State()
    await upd.api_update_apply(_request(state))
    pid = await _until_started(ran.pid_file)
    try:
        refused = await upd.api_update_dismiss(_request(state))
        assert refused.status == 409, refused.text
        assert _answer(refused)["error"]["code"] == "update_in_progress"
        assert not _gone(pid), "Dismiss stopped the update"
        assert _git(clone, "rev-parse", "HEAD") == new
    finally:
        await checkout_update.stop()
        _stop_stand_in(ran.pid_file)
        await _settle(state)

    dismissed = await upd.api_update_dismiss(_request(state))
    assert dismissed.status == 200, dismissed.text
    assert state.progress[-1] == ("cleared", "")


# ── personalclaw update: Ctrl-C stops it the same way, and says so ─────────────────────────


@pytest.mark.parametrize("presses", [1, 2])
def test_ctrl_c_during_personalclaw_update_puts_the_checkout_back_and_says_so(
    checkout, installer, release, capsys, monkeypatch, presses  # noqa: F811 - the imported fixtures
) -> None:
    """A Ctrl-C stops the install and puts the checkout back, and says what that left, as the
    dashboard's Cancel does. A second Ctrl-C while it puts the checkout back must not interrupt
    that: the checkout would stay on the new release with nothing said."""
    clone, old, _new = checkout
    ran = installer("hangs")
    me = os.getpid()
    if presses == 2:
        kill = checkout_update.kill_timed_out

        async def _pressed_again(proc, **kwargs):
            os.kill(me, signal.SIGINT)
            await asyncio.sleep(0.2)
            return await kill(proc, **kwargs)

        monkeypatch.setattr(checkout_update, "kill_timed_out", _pressed_again)

    def _press() -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ran.pid_file.exists() and ran.pid_file.read_text().strip():
                os.kill(me, signal.SIGINT)
                return
            time.sleep(0.05)

    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    pressing = threading.Thread(target=_press, daemon=True)
    pressing.start()
    try:
        try:
            cli_server._update()
            code: object = 0
        except SystemExit as exc:
            code = exc.code
        except KeyboardInterrupt:
            code = "KeyboardInterrupt"
    finally:
        signal.signal(signal.SIGINT, previous)
        pressing.join(timeout=5)
        pid = int(ran.pid_file.read_text()) if ran.pid_file.exists() else 0
        # Read before the stand-in is stopped here, so it is the update's own stop.
        stopped = bool(pid) and _gone(pid)
        _stop_stand_in(ran.pid_file)
    err = capsys.readouterr().err

    assert code == 130, f"exited {code!r}: {err}"
    assert stopped, "the install was left running"
    assert _git(clone, "rev-parse", "HEAD") == old, "the checkout stayed on the new release"
    assert _detached(clone) and _git(clone, "status", "--porcelain") == ""
    assert f"The update was stopped before it finished. {_PUT_BACK}" in " ".join(err.split())
    assert "Traceback" not in err, err


@pytest.mark.parametrize("does", ["hangs", "changes-hangs"])
def test_ctrl_c_during_a_wheel_upgrade_says_what_it_left(
    installer, release, capsys, monkeypatch, tmp_path, does  # noqa: F811 - the imported fixtures
) -> None:
    """A wheel has nothing to put back: the Ctrl-C stops the installer and says what the
    environment holds, as the dashboard's Cancel does, instead of a traceback."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "pip")
    ran = installer(does)
    me = os.getpid()

    def _press() -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ran.pid_file.exists() and ran.pid_file.read_text().strip():
                os.kill(me, signal.SIGINT)
                return
            time.sleep(0.05)

    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    pressing = threading.Thread(target=_press, daemon=True)
    pressing.start()
    try:
        try:
            cli_server._update()
            code: object = 0
        except SystemExit as exc:
            code = exc.code
        except KeyboardInterrupt:
            code = "KeyboardInterrupt"
    finally:
        signal.signal(signal.SIGINT, previous)
        pressing.join(timeout=5)
        pid = int(ran.pid_file.read_text()) if ran.pid_file.exists() else 0
        # Read before the stand-in is stopped here, so it is the update's own stop.
        stopped = bool(pid) and _gone(pid)
        _stop_stand_in(ran.pid_file)
    err = " ".join(capsys.readouterr().err.split())

    assert code == 130, f"exited {code!r}: {err}"
    assert stopped, "the upgrade was left running"
    if does == "hangs":
        said = (
            "The update was stopped before it finished. Nothing was changed: PersonalClaw is "
            "still on v0.0.1."
        )
    else:
        said = (
            "The update was stopped before it finished, but the upgrade had already changed "
            "alpha in PersonalClaw's environment; update again to finish it."
        )
    assert said in err, err
    assert "Traceback" not in err


@pytest.mark.asyncio
async def test_cancelling_a_staged_update_leaves_the_gateway_that_started_it_running(
    checkout, installer, release  # noqa: F811 - the imported fixtures
) -> None:
    """The staged auto-update waits for the update it starts, at the gateway's start among other
    times. The owner's Cancel ends that update, and must not end the wait with it: a cancellation
    escaping the wait would stop the gateway's own start."""
    from personalclaw.gateway import GatewayOrchestrator

    clone, old, _new = checkout
    ran = installer("hangs")
    state = _State()
    orch = GatewayOrchestrator.__new__(GatewayOrchestrator)
    orch.dashboard_state = state
    staged = asyncio.create_task(orch._auto_apply_update())
    pid = await _until_started(ran.pid_file)
    try:
        answer = await _cancel(state)
        await asyncio.wait_for(staged, timeout=15)

        assert not staged.cancelled(), "the cancel escaped into what started the staged update"
        assert _answer(answer)["update_progress"] == {"step": "cancelled", "detail": _CANCELLED}
        assert _gone(pid) and _git(clone, "rev-parse", "HEAD") == old
    finally:
        await checkout_update.stop()
        _stop_stand_in(ran.pid_file)
        await _settle(state)
