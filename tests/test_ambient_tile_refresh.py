"""AMBIENT-SURFACES AS-2 — the chatless refresh, proved at the properties that can lie.

Four claims, each of which passes a naive test while being false:

1. **"Zero LLM calls."** Trivially true of a no-op. Proved at the SINK
   (`resolve_provider_for_use_case` raises) and paired with a VACUITY FLOOR: the same test
   asserts the refresh produced a changed, fully-interpolated body. A refresh that did
   nothing would satisfy "no model call" and fail the floor.
2. **"Deterministic."** A single run cannot see a stamped timestamp or a reordered key.
   Asserted as byte-identity across repeated renders, plus a re-render of a real refresh.
3. **"A ledger-only row carrying near-zero token cost + duration."** A refresh that wrote NO
   row also has no tokens — and is worse, because the tile then cannot tell the user what a
   refresh cost. So the row's existence AND its fields are asserted.
4. **"TTL mode."** A refresh that fires on every read and one that never fires both pass "it
   refreshed". Asserted at the boundary: refused just under the TTL, taken just over it.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.artifacts import registry as artifact_registry
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.dashboard import tile_refresh, views_store
from personalclaw.ledger import TILE_REFRESHED

SKELETON = "<div>items: {{nodes.health.output.item_count}} ({{nodes.health.output.note}})</div>"
#: `knowledge-health` on an empty store — a real, allowlisted, zero-token data source, so the
#: happy path is driven through the shipped provider rather than a stand-in that could pass while
#: the registry dispatch was broken.
HEALTH_NODE = {"id": "health", "provider": "knowledge-health", "config": {}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home AND an artifact provider rooted in it.

    The provider matters as much as `config_dir`: `artifacts.registry` caches the native
    provider in a module global whose root was frozen at construction, so patching the home
    alone would leave writes landing wherever the first test in the session put them.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.views_store.config_dir", lambda: tmp_path)
    previous = artifact_registry.get_provider()
    artifact_registry.register_provider(NativeArtifactProvider(root=tmp_path / "artifacts"))
    yield tmp_path
    if previous is not None:
        artifact_registry.register_provider(previous)


def _live_tile(*, ttl_secs: int = 60, skeleton_body: str = SKELETON, data=None) -> str:
    """Pin a tile in ttl mode over a stored skeleton. Returns the tile ref."""
    store = artifact_registry.get_provider()
    store.create(name="Skeleton", content=skeleton_body, kind="widget", slug="tile-skeleton")
    store.create(name="Sales", content="<div>first paint</div>", kind="widget", slug="sales")
    views_store.add_tile("overview", "artifact:sales")
    views_store.set_tile_refresh(
        "overview",
        "artifact:sales",
        {
            "mode": "ttl",
            "ttl_secs": ttl_secs,
            "skeleton": "tile-skeleton",
            "data": [HEALTH_NODE] if data is None else data,
        },
    )
    return "artifact:sales"


def _body(slug: str = "sales") -> str:
    art = artifact_registry.get_provider().get(slug)
    return (art.content or "") if art is not None else ""


# ── 1. the render transform: deterministic, LLM-free ─────────────────────────


class TestTheRenderTransformIsDeterministic:
    def test_the_same_inputs_render_byte_identical_bodies(self):
        outputs = {"a": {"n": 7, "rows": [{"x": 1}, {"x": 2}]}}
        template = "<b>{{nodes.a.output.n}}</b><i>{{nodes.a.output.rows}}</i>"
        first = tile_refresh.render_skeleton(template, outputs)
        for _ in range(5):
            assert tile_refresh.render_skeleton(template, outputs) == first
        # And the render actually substituted — a transform that returned the template
        # unchanged would also be byte-identical every time.
        assert "{{" not in first
        assert "<b>7</b>" in first

    def test_an_unresolvable_slot_raises_instead_of_emptying_the_panel(self):
        from personalclaw.workflows.bindings import BindingError

        with pytest.raises(BindingError):
            tile_refresh.render_skeleton("<b>{{nodes.missing.output.n}}</b>", {})


class TestTheWF2NodeRendersTheStoredSkeleton:
    """The atom names a WF2 NODE, so the node's dispatcher is exercised — not only the
    helper it calls. A `transform` that could render a skeleton in `render_skeleton` but not
    through `dispatch_transform` would be a transform nobody can author."""

    @pytest.mark.asyncio
    async def test_dispatch_transform_interpolates_a_skeleton_artifact(self, home):
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.engine import dispatch_transform
        from personalclaw.workflows.models import InstanceState, Node, NodeKind

        artifact_registry.get_provider().create(
            name="Sk", content="<p>{{nodes.data.output.k}}</p>", kind="widget", slug="sk"
        )
        node = Node(kind=NodeKind.TRANSFORM, id="render", config={"skeleton": "sk"})
        result = await dispatch_transform(node, BindingContext(node_outputs={"data": {"k": "v"}}))

        assert result.state is InstanceState.DONE
        assert result.output == "<p>v</p>"

    @pytest.mark.asyncio
    async def test_a_missing_skeleton_is_a_typed_user_failure(self, home):
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.engine import dispatch_transform
        from personalclaw.workflows.models import InstanceState, Node, NodeKind

        node = Node(kind=NodeKind.TRANSFORM, id="render", config={"skeleton": "nope"})
        result = await dispatch_transform(node, BindingContext(node_outputs={}))

        assert result.state is InstanceState.FAILED
        assert "nope" in (result.failure.cause_plain if result.failure else "")

    def test_the_validator_accepts_a_skeleton_transform_with_no_expr(self):
        from personalclaw.workflows.validator import validate_spec

        spec = {
            "name": "live-tile",
            "root": {
                "kind": "sequence",
                "id": "root",
                "children": [
                    {"kind": "transform", "id": "render", "config": {"skeleton": "sk"}},
                ],
            },
        }
        codes = {issue.code for issue in validate_spec(spec).issues}
        assert "WF_MISSING_EXPR" not in codes

    def test_a_transform_with_neither_expr_nor_skeleton_is_still_rejected(self):
        from personalclaw.workflows.validator import validate_spec

        spec = {
            "name": "broken",
            "root": {
                "kind": "sequence",
                "id": "root",
                "children": [{"kind": "transform", "id": "render", "config": {}}],
            },
        }
        codes = {issue.code for issue in validate_spec(spec).issues}
        assert "WF_MISSING_EXPR" in codes


# ── 2. zero LLM calls, with a vacuity floor ──────────────────────────────────


class TestASteadyStateRefreshMakesZeroModelCalls:
    @pytest.mark.asyncio
    async def test_the_model_sink_is_never_reached_and_the_render_still_changed(
        self, home, monkeypatch
    ):
        """Proved at the chokepoint every non-interactive model call resolves through, and
        floored on the render actually changing: "no model call" is trivially true of a
        refresh that did nothing at all."""
        import personalclaw.providers.provider_bridge as bridge

        def explode(*_args, **_kwargs):
            raise AssertionError("a tile refresh resolved a model provider")

        monkeypatch.setattr(bridge, "resolve_provider_for_use_case", explode)

        ref = _live_tile()
        before = _body()
        result = await tile_refresh.refresh_tile("overview", ref)

        assert result.refreshed is True, result.reason
        # VACUITY FLOOR — the refresh produced a real, fully-interpolated, CHANGED body.
        after = _body()
        assert after != before
        assert "{{" not in after
        assert "items: 0" in after
        # And the attempt audit recorded no non-interactive model call.
        assert not (home / "model_calls.jsonl").exists()

    @pytest.mark.asyncio
    async def test_two_refreshes_of_unchanged_data_produce_byte_identical_bodies(self, home):
        """Determinism where it is load-bearing: a second refresh over the same inputs must
        write the same bytes. A transform that stamped a render time would pass every
        single-run assertion above and fail here."""
        ref = _live_tile()
        assert (await tile_refresh.refresh_tile("overview", ref)).refreshed is True
        first = _body()
        assert (await tile_refresh.refresh_tile("overview", ref, force=True)).refreshed is True
        assert _body() == first


# ── 3. the ledger row ────────────────────────────────────────────────────────


class TestTheLedgerRow:
    @pytest.mark.asyncio
    async def test_a_refresh_writes_one_row_carrying_zero_tokens_and_a_duration(self, home):
        ref = _live_tile()
        result = await tile_refresh.refresh_tile("overview", ref)

        row = tile_refresh.last_row("overview", ref)
        assert row, "a refresh that writes no row leaves the tile unable to say what it cost"
        assert row["kind"] == TILE_REFRESHED
        assert row["tokens"] == 0
        assert row["cost_usd"] == 0.0
        assert row["duration_ms"] >= 0
        assert row["ok"] is True
        assert row["rendered_bytes"] > 0
        assert row["event_id"].endswith("-evt-1")
        assert row == result.row

    @pytest.mark.asyncio
    async def test_the_row_carries_one_outcome_per_data_node(self, home):
        ref = _live_tile()
        await tile_refresh.refresh_tile("overview", ref)

        row = tile_refresh.last_row("overview", ref)
        assert [n["id"] for n in row["nodes"]] == ["health"]
        assert row["nodes"][0]["ok"] is True
        assert row["nodes"][0]["provider"] == "knowledge-health"

    @pytest.mark.asyncio
    async def test_a_second_refresh_gets_its_OWN_event_id(self, home):
        """🔴 Caught on a real drive. A writer rebuilt per refresh restarts `seq` at 1 and
        re-mints `<tile>-evt-1` forever — duplicate event ids, which is the exact signature of
        "two writers are racing over one ledger". Asserted on TWO refreshes, because the first
        one is `-evt-1` either way."""
        ref = _live_tile()
        first = await tile_refresh.refresh_tile("overview", ref)
        second = await tile_refresh.refresh_tile("overview", ref, force=True)

        assert first.row["event_id"] == "overview__sales-evt-1"
        assert second.row["event_id"] == "overview__sales-evt-2"
        assert second.row["seq"] == 2

        rows = tile_refresh.read_events(tile_refresh._STORE, tile_refresh.tile_key("overview", ref))
        ids = [r["event_id"] for r in rows]
        assert len(ids) == len(set(ids)), f"duplicate ledger event ids: {ids}"

    @pytest.mark.asyncio
    async def test_the_row_lands_in_the_tile_scoped_ledger_file(self, home):
        ref = _live_tile()
        await tile_refresh.refresh_tile("overview", ref)

        path = tile_refresh.ledger_path("overview", ref)
        assert path.exists()
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        assert len(rows) == 1
        # One directory PER TILE, not per refresh — the run-weight §2.3 refuses.
        assert path.parent.name == "overview__sales"
        assert path.parent.parent.name == tile_refresh.LEDGER_DIRNAME


# ── 4. the TTL boundary ──────────────────────────────────────────────────────


class TestTheTTLBoundary:
    @pytest.mark.asyncio
    async def test_a_refresh_is_refused_before_the_ttl_and_taken_after(self, home):
        ref = _live_tile(ttl_secs=600)
        first = await tile_refresh.refresh_tile("overview", ref)
        assert first.refreshed is True

        # Anchor the injected clock to the stamp `due()` actually subtracts, NOT to a live
        # `time.time()` read taken after the refresh returned. Two separate errors used to
        # ride that read, both in the same direction, against a ONE-second margin:
        #   1. `time.time()` was evaluated AFTER `first` completed, so the offset was
        #      `T_return + 599` while the code ages from the stamp written mid-call — the
        #      elapsed it saw was `599 + (T_return - T_stamp)`, i.e. every millisecond the
        #      first refresh took ate into the margin.
        #   2. the ledger `ts` is whole-second (`%Y-%m-%dT%H:%M:%SZ`), so the parsed stamp is
        #      TRUNCATED — up to another second of apparent age on top.
        # Together those routinely reached 600s on a loaded xdist worker and turned the
        # refusal this test exists to assert into a refresh (seen on PR #1610, whose diff
        # contained zero .py files, and again on #1879).
        #
        # This is a TIGHTENING, not a widened margin: offsetting from the recorded stamp makes
        # `age` exactly 599 and exactly 601, so each side of the boundary is now pinned to the
        # second instead of drifting with how long the call before it took.
        stamp = tile_refresh._epoch(str(tile_refresh.last_row("overview", ref).get("ts", "")))
        assert stamp > 0, "no stamp to age from — the first refresh wrote no ledger row"

        # A second read seconds later must NOT re-fetch (a cadence, not a fetch-per-paint).
        early = await tile_refresh.refresh_tile("overview", ref, now=stamp + 599)
        assert early.refreshed is False
        assert early.reason == "within_ttl"

        # One second past the boundary it fires.
        late = await tile_refresh.refresh_tile("overview", ref, now=stamp + 601)
        assert late.refreshed is True, late.reason

    @pytest.mark.asyncio
    async def test_a_manual_tile_never_fires_unattended(self, home):
        ref = _live_tile()
        views_store.set_tile_refresh("overview", ref, {"mode": "manual"})
        result = await tile_refresh.refresh_tile("overview", ref)
        assert result.refreshed is False
        assert result.reason == "not_bound"

    @pytest.mark.asyncio
    async def test_the_button_bypasses_the_ttl(self, home):
        ref = _live_tile(ttl_secs=86400)
        await tile_refresh.refresh_tile("overview", ref)
        forced = await tile_refresh.refresh_tile("overview", ref, force=True)
        assert forced.refreshed is True

    def test_an_unset_ttl_falls_back_to_the_ambient_config_cadence(self, home, monkeypatch):
        tile = views_store.DashboardTile(
            ref="artifact:x", refresh=views_store.TileRefresh(mode="ttl", ttl_secs=0)
        )
        monkeypatch.setattr(tile_refresh, "_default_ttl", lambda: 1234)
        # No prior row ⇒ due, and the fallback is what a later comparison would use.
        assert tile_refresh.due("overview", tile, 0.0) == (True, 0.0)
        assert tile_refresh._default_ttl() == 1234

    @pytest.mark.asyncio
    async def test_the_boundary_itself_is_pinned_without_any_live_clock(self, home):
        """`due()` is the scheduling decision, so assert it AT the boundary with a stamp read
        back from the ledger and no live `time.time()` anywhere.

        The end-to-end test above can only reach `due()` through a refresh, which is what let a
        one-second timing margin hide in it. This one has no margin to have: every `now` is
        computed from the recorded stamp, so 599/600/601 mean exactly that.
        """
        ref = _live_tile(ttl_secs=600)
        assert (await tile_refresh.refresh_tile("overview", ref)).refreshed is True
        tile = views_store.find_tile("overview", ref)
        assert tile is not None
        stamp = tile_refresh._epoch(str(tile_refresh.last_row("overview", ref).get("ts", "")))
        assert stamp > 0, "no stamp to age from"

        # `due` is `age >= ttl`, so 600 is INSIDE the fire half — the boundary is inclusive.
        assert tile_refresh.due("overview", tile, stamp + 599) == (False, 599.0)
        assert tile_refresh.due("overview", tile, stamp + 600) == (True, 600.0)
        assert tile_refresh.due("overview", tile, stamp + 601) == (True, 601.0)


# ── 5. a failed refresh keeps the last-good paint ────────────────────────────


class TestAFailedRefreshKeepsLastGood:
    @pytest.mark.asyncio
    async def test_a_failing_data_node_leaves_the_body_untouched_and_reddens_the_chip(
        self, home, monkeypatch
    ):
        ref = _live_tile(data=[{"id": "health", "provider": "knowledge-retrieve", "config": {}}])
        first = _body()

        from personalclaw.action_providers.base import ActionResult
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )

        _ensure_default_providers_registered()
        provider = get_action_provider("knowledge-retrieve")
        assert provider is not None

        async def failing(*_a, **_k):
            return ActionResult(success=False, error="the store is unreachable")

        monkeypatch.setattr(provider, "execute", failing)
        result = await tile_refresh.refresh_tile("overview", ref)

        assert result.refreshed is False
        assert result.reason == "data_failed"
        assert _body() == first, "a failed refresh must never blank the panel"

        row = tile_refresh.last_row("overview", ref)
        assert row["ok"] is False
        assert row["nodes"][0]["ok"] is False
        assert "unreachable" in row["nodes"][0]["error"]
        assert row["tokens"] == 0

    @pytest.mark.asyncio
    async def test_a_provider_outside_the_allowlist_is_refused_not_dispatched(self, home):
        """The allowlist is the reason a TTL tile is not an unattended-execution surface, so
        the refusal is asserted rather than assumed — and asserted on a provider that really
        is registered, or the test would pass on "not registered" instead."""
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )

        _ensure_default_providers_registered()
        assert get_action_provider("bash") is not None
        assert "bash" not in tile_refresh.DATA_PROVIDERS

        ref = _live_tile(data=[{"id": "shell", "provider": "bash", "config": {"command": "id"}}])
        result = await tile_refresh.refresh_tile("overview", ref)

        assert result.refreshed is False
        assert result.reason == "data_failed"
        assert "not a tile data source" in result.nodes[0].error

    @pytest.mark.asyncio
    async def test_incident_mode_suspends_the_unattended_refresh(self, home, monkeypatch):
        """🔴 The kill switch, at the CALL SITE. A TTL tile is a fourth unattended dispatch seam
        (AUTONOMY-GUARDRAILS §1.2), so a dashboard that kept fetching through an incident would be
        the quiet exception that makes the switch useless."""
        ref = _live_tile()
        monkeypatch.setattr("personalclaw.guardrails.incident.incident_active", lambda: True)
        before = _body()

        result = await tile_refresh.refresh_tile("overview", ref)

        assert result.refreshed is False
        assert result.reason == "data_failed"
        assert "incident mode is active" in result.nodes[0].error
        assert _body() == before

    @pytest.mark.asyncio
    async def test_the_denylist_is_threaded_with_a_NON_EMPTY_session_key(self, home, monkeypatch):
        """`enforce_action(session_key="")` classifies as ATTENDED and skips the SafetyProfile
        layer — a quieter version of not enforcing. Asserted on the ARGUMENT the seam passes,
        because the call being present proves nothing about that."""
        seen: list[str] = []

        def spy(provider_name, action_config, ctx=None, session_key=""):
            from personalclaw.guardrails.denylist import DenyDecision

            seen.append(session_key)
            return DenyDecision(blocked=False, verdict="allow", reason="", matched="")

        monkeypatch.setattr("personalclaw.guardrails.denylist.enforce_action", spy)
        ref = _live_tile()
        assert (await tile_refresh.refresh_tile("overview", ref)).refreshed is True

        assert seen == ["tile:overview__sales"]

    @pytest.mark.asyncio
    async def test_a_denylisted_action_is_refused_and_leaves_the_body_alone(
        self, home, monkeypatch
    ):
        def refuse(provider_name, action_config, ctx=None, session_key=""):
            from personalclaw.guardrails.denylist import DenyDecision

            return DenyDecision(
                blocked=True, verdict="block", reason="matched a deny glob", matched="test"
            )

        monkeypatch.setattr("personalclaw.guardrails.denylist.enforce_action", refuse)
        ref = _live_tile()
        before = _body()

        result = await tile_refresh.refresh_tile("overview", ref)

        assert result.refreshed is False
        assert "refused by the action denylist" in result.nodes[0].error
        assert _body() == before

    @pytest.mark.asyncio
    async def test_a_missing_skeleton_is_recorded_rather_than_silently_skipped(self, home):
        ref = _live_tile()
        views_store.set_tile_refresh(
            "overview", ref, {"mode": "ttl", "ttl_secs": 60, "skeleton": "gone", "data": []}
        )
        result = await tile_refresh.refresh_tile("overview", ref)

        assert result.refreshed is False
        assert result.reason == "skeleton_missing"
        assert tile_refresh.last_row("overview", ref)["ok"] is False


# ── the binding round-trips through the store ────────────────────────────────


class TestTheTileBindingRoundTrips:
    def test_a_binding_survives_a_write_and_reload(self, home):
        artifact_registry.get_provider().create(
            name="S", content="x", kind="widget", slug="tile-skeleton"
        )
        views_store.add_tile("overview", "artifact:sales")
        views_store.set_tile_refresh(
            "overview",
            "artifact:sales",
            {"mode": "ttl", "ttl_secs": 30, "skeleton": "tile-skeleton", "data": [HEALTH_NODE]},
        )
        tile = views_store.find_tile("overview", "artifact:sales")
        assert tile is not None
        assert tile.refresh.mode == "ttl"
        assert tile.refresh.ttl_secs == 30
        assert tile.refresh.skeleton == "tile-skeleton"
        assert [n.id for n in tile.refresh.data] == ["health"]

    def test_an_unrecognized_mode_fails_open_to_manual(self, home):
        views_store.add_tile("overview", "artifact:sales")
        views_store.set_tile_refresh("overview", "artifact:sales", {"mode": "view"})
        tile = views_store.find_tile("overview", "artifact:sales")
        assert tile is not None
        assert tile.refresh.mode == "manual", "a declared-but-unimplemented mode must not fire"

    def test_a_tile_still_carries_no_coordinates(self, home):
        views_store.add_tile("overview", "artifact:sales")
        tile = views_store.find_tile("overview", "artifact:sales")
        assert tile is not None
        for banned in ("x", "y", "w", "h", "col", "row"):
            assert not hasattr(tile, banned)
