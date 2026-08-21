"""A config PATCH must reach the post-write seam time-travel listens on (DAS-9).

`atomic_write._post_write_hooks` is the ONE place "state just changed on disk" is knowable,
and time-travel's debounced committer is its subscriber. So a writer that rolls its own
tmp+rename is invisible to history: the file lands, and the root it belongs to never gets a
commit.

`PATCH /api/config/personalclaw` is the primary writer of `config.json` — every toggle in
Settings goes through it — and it used `agent._atomic_json_write`, which does exactly that own
mkstemp+rename. Found by driving a live gateway: three real PATCHes left the `config`
state-history root at `exists=False, commits=0`, while the same write issued through
`atomic_write` produced a `config.git` with a real commit at the debounce boundary. After the
fix the identical drive yields `exists=True, commits=1`.

These tests assert the CALL SITE, not the seam. `atomic_write` has its own tests; what was
broken here was that this handler did not use it, and only a test that drives the handler and
watches the hook can tell those apart.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def _make_app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


def _seed_config() -> dict:
    return {
        "agents": {"personalclaw": {"provider_agent": "personalclaw", "workspace": "default"}},
        "default_agent": "personalclaw",
        "agent": {"approval_mode": "auto", "sandbox": "auto"},
        "local_models": {"pressure_warn_pct": 85},
        "auto_update": False,
    }


@pytest.fixture
def tmp_config(tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(_seed_config()), encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=cfg_path):
        yield cfg_path


@pytest.fixture
def seen_writes():
    """Subscribe a recording hook to the real seam, and always unsubscribe.

    Uses the public register/unregister pair rather than reaching into the module global, so
    the test breaks if the seam's own contract changes rather than silently drifting.
    """
    from personalclaw.atomic_write import register_post_write_hook, unregister_post_write_hook

    paths: list[Path] = []

    def hook(path: Path) -> object:
        paths.append(Path(path))
        return None

    register_post_write_hook(hook)
    try:
        yield paths
    finally:
        unregister_post_write_hook(hook)


@pytest.mark.asyncio
async def test_a_config_patch_lands_a_commit_in_the_config_history_root(tmp_path) -> None:
    """The load-bearing assertion: a settings change becomes a history commit.

    🪤 READ THIS BEFORE TRUSTING IT AS THE REGRESSION PIN — IT IS NOT ONE. Two successive
    falsifications established the limit. Counting post-write hook invocations does not
    discriminate: something else in the PATCH flow already notifies the seam once for the config
    path (measured — bypass 1 notification, `atomic_write` 2). Asserting the commit does not
    discriminate either, because that other notification creates a pending entry, so both
    `flush()` and `run_pending()` drain it and a commit lands under EITHER writer in this
    harness. The pin for the specific bypass is therefore the source rail at the bottom of this
    file, and the evidence for the change is the live A/B on a real gateway with a fresh home:
    bypass → `config` root `exists=False, commits=0` (reproduced on two separate runs);
    through `atomic_write` → `exists=True, commits=1`. Something about the live path — most
    likely the ordering of that other notification against the final content — makes the
    handler's own notification load-bearing there and not here.

    What this test IS worth keeping for: it pins that a config PATCH ends in a history commit at
    all, so a future change that stops config writes reaching the debouncer entirely goes red.
    """
    from personalclaw.durability import history_debounce, state_history

    home = tmp_path / "home"
    home.mkdir()
    cfg_path = home / "config.json"
    cfg_path.write_text(json.dumps(_seed_config()), encoding="utf-8")

    if not state_history.git_available():
        pytest.skip("git is unavailable, so time-travel records nothing by design")

    debouncer = history_debounce.install(home=home)
    assert debouncer is not None, "the debouncer must install for this test to mean anything"
    try:
        with patch("personalclaw.config.loader.config_path", return_value=cfg_path):
            async with TestClient(TestServer(_make_app())) as c:
                resp = await c.patch(
                    "/api/config/personalclaw",
                    json={"path": "local_models.pressure_warn_pct", "value": 70},
                )
                assert resp.status == 200, await resp.text()

        # Flush rather than sleep: a fixed sleep measures the skeleton, not the behaviour.
        debouncer.flush()
        root = {r.id: r for r in state_history.roots(home)}["config"]
        assert state_history.repo_exists(root, home=home), (
            "the config PATCH left no state-history repo, so 'roll back my settings' has "
            "nothing to roll back to"
        )
        entries = state_history.timeline(root, home=home)
        assert entries, f"config root exists but holds no commit: {entries}"
    finally:
        history_debounce.uninstall()


@pytest.mark.asyncio
async def test_the_patch_still_persists_the_value(tmp_config, seen_writes) -> None:
    """Routing through the seam must not change what lands on disk."""
    async with TestClient(TestServer(_make_app())) as c:
        resp = await c.patch(
            "/api/config/personalclaw",
            json={"path": "local_models.pressure_warn_pct", "value": 70},
        )
        assert resp.status == 200

    data = json.loads(tmp_config.read_text(encoding="utf-8"))
    assert data["local_models"]["pressure_warn_pct"] == 70
    assert data["default_agent"] == "personalclaw"
    # This handler writes the NORMALIZED document (the loaded config re-serialized), so the file
    # gains every defaulted section and drops keys the dataclasses do not model. That is
    # pre-existing behaviour of the PATCH path and independent of which writer it uses —
    # asserted here only so a future reader does not mistake the rewrite for a regression
    # introduced by routing through the seam.
    assert len(data) > 20, "the PATCH writes the full normalized config, not a narrow patch"


@pytest.mark.asyncio
async def test_a_rejected_patch_notifies_nothing(tmp_config, seen_writes) -> None:
    """The counterexample, so the first test cannot pass by the seam firing on every request.

    A validator rejection must write nothing at all — and therefore notify nothing. Without
    this, a handler that notified the seam unconditionally (even on a refusal) would look
    correct above while telling history a change happened that did not.
    """
    async with TestClient(TestServer(_make_app())) as c:
        resp = await c.patch(
            "/api/config/personalclaw", json={"path": "nonexistent.field", "value": "x"}
        )
        assert resp.status == 400

    assert seen_writes == [], f"a refused PATCH still notified the seam: {seen_writes}"


def test_the_handler_does_not_use_a_seam_bypassing_writer() -> None:
    """A source rail on the specific regression, so a future edit cannot reintroduce it quietly.

    `agent._atomic_json_write` is a legitimate helper for the ACP agent-config files it was
    written for; what is not legitimate is this handler using it for `config.json`, whose root
    time-travel is supposed to cover. Named rather than pattern-matched, because the failure
    mode is this exact substitution.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "dashboard"
    text = (src / "handlers" / "core.py").read_text(encoding="utf-8")
    # Strip comments so the explanatory note naming the bypass does not read as a use of it.
    code = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
    assert "_atomic_json_write" not in code, (
        "handlers/core.py calls the seam-bypassing writer again; config writes must go through "
        "atomic_write or time-travel silently stops recording settings changes"
    )
    # Vacuity floor: the file really does write config through the seam.
    assert "atomic_write(" in code
