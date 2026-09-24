"""A tile-widget action re-fires the tile's bound workflow — and ONLY inside its frozen set.

AMBIENT-SURFACES §5.4 / atom AS-6, third routing case: *"tile widgets → actions run through
the tile's bound workflow (re-fire with bound args), subject to the trigger's frozen
capability set — a rendered button can never introduce actions the trigger didn't declare."*

🔴 WHY THIS IS A SECURITY TEST, not a plumbing test. A tile's body is MODEL-AUTHORED, so the
action name that arrives at the endpoint is untrusted text. The property to prove is a
NEGATIVE — an action outside the frozen set is refused — and a negative is exactly the kind of
claim that passes vacuously: a fence that refused EVERYTHING would satisfy it while breaking
the feature. So every refusal test here is paired with a CONTROL leg proving the in-set action
is permitted and really re-fires, and the pair is asserted on the same tile.

The frozen set is derived from the tile's SAVED binding (`frozen_capabilities`), never from the
request — see the DEVIATION note in `dashboard/tile_actions.py` about "the trigger's" set: a
tile cannot bind a trigger until AUTOMATION-SUBSTRATE step 8 lands (`TileRefresh.mode: "view"`
is deliberately absent), so the tile's binding is what is frozen today, enforced through the
SAME helper the trigger fence uses (`triggers.screen.unfenced_actions`).
"""

from __future__ import annotations

import pytest

from personalclaw.artifacts import registry as artifact_registry
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.dashboard import tile_actions, tile_refresh, views_store

SKELETON = "<div>items: {{nodes.health.output.item_count}}</div>"
HEALTH_NODE = {"id": "health", "provider": "knowledge-health", "config": {}}

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home AND an artifact provider rooted in it (the AS-2 fixture's reasoning:
    the registry caches a provider whose root was frozen at construction)."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.views_store.config_dir", lambda: tmp_path)
    previous = artifact_registry.get_provider()
    artifact_registry.register_provider(NativeArtifactProvider(root=tmp_path / "artifacts"))
    yield tmp_path
    if previous is not None:
        artifact_registry.register_provider(previous)


def _live_tile(data=None) -> str:
    """Pin a ttl tile whose bound workflow is one allowlisted read-only data node."""
    store = artifact_registry.get_provider()
    store.create(name="Skeleton", content=SKELETON, kind="widget", slug="tile-skeleton")
    store.create(name="Sales", content="<div>first paint</div>", kind="widget", slug="sales")
    views_store.add_tile("overview", "artifact:sales")
    views_store.set_tile_refresh(
        "overview",
        "artifact:sales",
        {
            "mode": "ttl",
            "ttl_secs": 60,
            "skeleton": "tile-skeleton",
            "data": [HEALTH_NODE] if data is None else data,
        },
    )
    return "artifact:sales"


def _tile():
    return views_store.find_tile("overview", "artifact:sales")


# ── the frozen set is the SAVED binding ──────────────────────────────────────


class TestTheFrozenSet:
    def test_it_is_derived_from_the_tiles_own_bound_nodes(self, home) -> None:
        _live_tile()
        assert tile_actions.frozen_capabilities(_tile()) == {"providers": ["knowledge-health"]}

    def test_an_unbound_tile_freezes_nothing(self, home) -> None:
        """`EMPTY_MEANS = "deny"`: a tile with no bound workflow permits no action at all,
        rather than reading as unrestricted."""
        store = artifact_registry.get_provider()
        store.create(name="Static", content="<div/>", kind="widget", slug="static")
        views_store.add_tile("overview", "artifact:static")
        tile = views_store.find_tile("overview", "artifact:static")
        assert tile_actions.frozen_capabilities(tile) == {"providers": []}
        verdict = tile_actions.check(tile, "refresh")
        assert verdict["ok"] is False and verdict["code"] == tile_actions.CODE_NOT_BOUND

    def test_the_request_cannot_widen_the_set(self, home) -> None:
        """The whole point: the requested action is compared against PERSISTED state, so a
        widget asking for `bash` cannot bring its own permission with it."""
        _live_tile()
        assert tile_actions.requested_providers(_tile(), "bash") == ["bash"]
        assert "bash" not in tile_actions.frozen_capabilities(_tile())["providers"]


class TestTheNegativeAndItsControl:
    """The refusal AND the permission, on ONE tile — a fence that refused everything would
    pass the first half of this class and fail the second."""

    def test_an_action_outside_the_frozen_set_is_refused(self, home) -> None:
        _live_tile()
        verdict = tile_actions.check(_tile(), "bash")
        assert verdict["ok"] is False
        assert verdict["code"] == tile_actions.CODE_REFUSED
        # The row must say WHICH action was outside the set, not merely that one was.
        assert any(v[1] == "bash" for v in verdict["violations"])
        assert "bash" in verdict["message"]

    def test_the_in_set_action_is_permitted(self, home) -> None:
        """The CONTROL leg. Same tile, an action the binding declares."""
        _live_tile()
        verdict = tile_actions.check(_tile(), "health")
        assert verdict["ok"] is True
        assert verdict["providers"] == ["knowledge-health"]

    def test_the_bare_refire_is_permitted(self, home) -> None:
        _live_tile()
        assert tile_actions.check(_tile(), "refresh")["ok"] is True

    @pytest.mark.parametrize(
        "action", ["bash", "run-prompt", "invoke-agent", "run-workflow", "send-message", "notify"]
    )
    def test_every_write_or_model_capable_provider_is_refused(self, home, action) -> None:
        """Enumerated rather than sampled: `notify`/`send-message` are READ-ONLY to the
        trigger fence (they are in `READ_ONLY_PROVIDERS`), so they would pass a fence that
        reused the trigger path's read-only default. A tile that never declared them must
        still be refused — which is why that default is deliberately not applied here."""
        _live_tile()
        verdict = tile_actions.check(_tile(), action)
        assert verdict["ok"] is False, f"{action} is outside this tile's binding"

    def test_an_allowlisted_provider_the_tile_never_DECLARED_is_still_refused(self, home) -> None:
        """🔴 The case ONLY the frozen set catches, and it was MISSING until a falsification run
        found it. With `unfenced_actions` deleted from `check`, all sixteen other tests in this
        file still passed: every action they name (`bash`, `run-prompt`, `notify`, …) is refused
        by the DATA_PROVIDERS allowlist on its own, so the frozen-set check was unmeasured.

        `knowledge-retrieve` IS a tile data provider — the allowlist admits it — but THIS tile
        declared only `knowledge-health`. That is the atom's sentence exactly: a rendered button
        cannot introduce an action the binding never declared.
        """
        _live_tile()
        verdict = tile_actions.check(_tile(), "knowledge-retrieve")
        assert verdict["ok"] is False
        assert verdict["code"] == tile_actions.CODE_REFUSED
        assert any(v[1] == "knowledge-retrieve" for v in verdict["violations"])
        assert "knowledge-retrieve" in tile_refresh.DATA_PROVIDERS, (
            "if this provider ever leaves the allowlist this test stops measuring the frozen "
            "set and starts measuring the allowlist again"
        )
        # The CONTROL, on the same tile: the provider it DID declare is permitted.
        assert tile_actions.check(_tile(), "knowledge-health")["ok"] is True

    def test_a_provider_outside_the_tile_allowlist_is_refused_even_when_declared(
        self, home
    ) -> None:
        """Defense in depth: the frozen set bounds THIS tile, `DATA_PROVIDERS` bounds every
        tile. A binding that somehow persisted `bash` must still not dispatch it."""
        _live_tile(data=[{"id": "shell", "provider": "bash", "config": {}}])
        verdict = tile_actions.check(_tile(), "shell")
        assert verdict["ok"] is False
        assert verdict["code"] == tile_actions.CODE_REFUSED
        assert any("not a tile data provider" in v[2] for v in verdict["violations"])
        assert "bash" not in tile_refresh.DATA_PROVIDERS


class TestTheRefire:
    async def test_a_permitted_action_really_re_fires_the_bound_workflow(self, home) -> None:
        """The feature half: the fence passing is worth nothing if nothing runs. Asserted on
        the tile's BODY changing — the re-fire re-renders the skeleton — not on the return
        value alone."""
        ref = _live_tile()
        result = await tile_actions.refire("overview", ref, action="health", payload={"a": 1})
        assert result["ok"] is True and result["outcome"] == "tile-refired"
        body = artifact_registry.get_provider().get("sales").content
        assert "first paint" not in body, "the bound workflow must have re-rendered the tile"
        assert "items:" in body and "{{" not in body

    async def test_a_refused_action_dispatches_NOTHING(self, home) -> None:
        """The negative at the DISPATCH level, not only at the verdict: the body must be
        untouched. A fence that returned a refusal after re-firing would pass the verdict
        tests above."""
        ref = _live_tile()
        before = artifact_registry.get_provider().get("sales").content
        result = await tile_actions.refire("overview", ref, action="bash", payload=None)
        assert result["ok"] is False and result["code"] == tile_actions.CODE_REFUSED
        assert artifact_registry.get_provider().get("sales").content == before

    async def test_a_missing_tile_is_reported_not_guessed(self, home) -> None:
        result = await tile_actions.refire("overview", "artifact:ghost", action="refresh")
        assert result["ok"] is False and result["code"] == tile_actions.CODE_NOT_FOUND
