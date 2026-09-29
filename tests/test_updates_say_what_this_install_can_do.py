"""Only a source checkout installs updates unattended, and every surface says so for its kind.

`updates.auto = "staged"` is applied by the gateway's `_auto_apply_update`, which moves a git
checkout to the resolved release and restarts. It returns at once without a checkout, so on a
pip or uv install, in the container image and in the desktop app the switch did nothing — and
it did worse than nothing: a staged install skipped the "update available" notice too, so it was
told nothing and changed nothing. First-run setup's done screen offered that switch to every
kind anyway, under "When a new version ships, it installs and restarts unattended".

One predicate now answers "can this install apply an update on its own", and the gateway, the
update check (Settings → Updates) and the first-run read (the done screen) all ask it.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import self_update


@pytest.mark.parametrize("kind", self_update.INSTALL_KINDS)
def test_only_a_source_checkout_applies_updates_unattended(kind):
    assert self_update.applies_updates_unattended(kind) is (kind == "git")


@pytest.mark.parametrize("kind", self_update.INSTALL_KINDS)
def test_the_update_check_says_whether_this_install_can(kind, monkeypatch):
    monkeypatch.setattr(self_update, "detect_install_kind", lambda: kind)
    monkeypatch.setattr(self_update, "fetch_releases", AsyncMock(return_value=[]))
    monkeypatch.setattr(self_update, "commits_behind_upstream", AsyncMock(return_value=0))
    status = asyncio.run(self_update.build_update_status("0.2.0"))
    assert status["kind"] == kind
    assert status["unattended_apply"] is (kind == "git")


async def _get(handler) -> dict:
    request = MagicMock()
    request.get = lambda *_a, **_k: "dashboard"
    request.headers = {}
    response = await handler(request)
    return json.loads(response.body.decode())


@pytest.mark.parametrize("kind", ["container", "git"])
def test_first_run_reads_the_install_kind_without_an_update_check(kind, monkeypatch):
    """The done screen's pointer needs the kind, and asking the update check for it would reach
    GitHub from a first-run screen — so the first-run read carries it, and checks nothing."""
    from personalclaw.dashboard import handlers_system as hs

    monkeypatch.setattr(self_update, "detect_install_kind", lambda: kind)
    fetched = AsyncMock(return_value=[])
    monkeypatch.setattr(self_update, "fetch_releases", fetched)
    state = asyncio.run(_get(hs.api_onboarding))
    assert state["install_kind"] == kind
    assert state["unattended_apply"] is (kind == "git")
    fetched.assert_not_awaited()


def _available_update(orch, *, auto: str, kind: str) -> tuple[MagicMock, AsyncMock]:
    """Run one update check that finds a newer release, on *kind* with ``updates.auto`` *auto*."""
    import personalclaw.dashboard.handlers as handlers

    orch.dashboard_state = MagicMock()
    orch._staged_auto_apply = AsyncMock()
    cfg = MagicMock()
    cfg.updates.auto = auto
    saved = handlers._update_info.copy()
    try:
        handlers._update_info.update({"available": True, "version": "9.9.9"})
        with (
            patch.object(handlers, "_do_update_check", new_callable=AsyncMock),
            patch("personalclaw.config.AppConfig.load", return_value=cfg),
            patch.object(self_update, "detect_install_kind", return_value=kind),
        ):
            asyncio.run(orch._check_for_updates())
    finally:
        handlers._update_info.clear()
        handlers._update_info.update(saved)
    return orch.dashboard_state, orch._staged_auto_apply


def _orchestrator():
    from personalclaw.config.loader import AppConfig
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        return GatewayOrchestrator(cfg)


@pytest.mark.parametrize("kind", ["pip", "container", "desktop"])
def test_staged_on_an_install_that_cannot_apply_it_still_says_an_update_is_there(kind):
    state, staged = _available_update(_orchestrator(), auto="staged", kind=kind)
    staged.assert_not_awaited()
    state.push_refresh.assert_called_with("update_available")


def test_staged_on_a_source_checkout_still_applies_it():
    _state, staged = _available_update(_orchestrator(), auto="staged", kind="git")
    staged.assert_awaited_once()
