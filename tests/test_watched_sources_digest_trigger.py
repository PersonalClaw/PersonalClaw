"""WS-7's missing half: the morning digest's CALLER (WATCHED-SOURCES §6.2).

`tests/test_watched_sources_digest.py` proves `run_morning_digest` works when something calls
it. WS-7's execution log records that nothing did: *"the digest is invocable and fully tested,
but nothing in the shipped product calls it yet"*. Measured on `main` at `0169f8d4`: `git grep
run_morning_digest -- src/` returned TWO hits, both inside `source_digest.py` (the definition
and a docstring mention). Zero callers.

So this file asserts REACHABILITY, not existence — a registered-but-never-fired trigger is the
same defect one level up:

* the reconciler puts an ARMED `clock` row in the unified `TriggerStore` the clock loop reads;
* the provider name in THAT ROW resolves through the real action-provider registry and passes
  the scheduler's own `ALLOWED_HOOK_PROVIDERS` validation, so the fire is not refused;
* dispatching from the stored row exactly as `gateway._fire_store_trigger` does actually
  produces ONE knowledge item and ONE notification through the shipped
  `notification_allowed()` gate;
* `mute_all` still suppresses it (the gate is not re-implemented or bypassed);
* a SECOND fire posts nothing, which is what `digest_cursor.json` is for;
* and `_init_cron` — the boot path — really contains the call.

No frozen clock anywhere. WS-7 recorded a measured false green from one: `_due_delay` compares
against the store's wall-clock `last_poll_at`, so a clock pinned at `t=1_000_000` leaves every
source permanently "not due" and every later `tick()` a silent no-op that reads as success. The
ingest here does ONE tick against a source that has never been polled (so it is due on the real
clock, with no waiting), and the double-fire test re-dispatches the DIGEST rather than re-polling
the source — so no due-check is involved and no sleep is needed.
"""

import json

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.source_digest_provider import (
    SOURCE_DIGEST_JOB_NAME,
    SOURCE_DIGEST_SCHEDULE,
    reconcile_source_digest_cron,
)
from personalclaw.knowledge import source_digest as sd
from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.source_streams import SourceEventSpool
from personalclaw.knowledge_providers.base import (
    KnowledgeItem,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Redirect the home via ``PERSONALCLAW_HOME`` AND repair the import-bound copy.

    Verbatim the remedy `test_watched_sources_digest.py` records as MEASURED: a
    `monkeypatch.setattr` on `config.loader.config_dir` that is live during a consumer's FIRST
    import is baked in permanently (`providers/entity_routes.py:22` does `from
    personalclaw.config.loader import config_dir`, and the undo restores only the loader
    module's attribute). Under xdist that made a `mute_all` test read the PREVIOUS test's home
    and read as a security-control bypass. So the env var is the lever — `config_dir()` reads it
    per call and caches nothing — `entity_routes.config_dir` is re-pointed at the REAL live
    function to undo any bake-in a sibling suite performed, and the redirect is ASSERTED.
    """
    from personalclaw.config.loader import config_dir as live_config_dir
    from personalclaw.providers import entity_routes

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr(entity_routes, "config_dir", live_config_dir)
    assert entity_routes._entity_settings_path("notifications").parent.parent == home
    return home


@pytest.fixture()
def trigger_store(_isolated_home):
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=_isolated_home)


# ── the ingest: the REAL poll path, so the spool holds real records ──────────────


class _FixtureSourceProvider(KnowledgeSourceProvider):
    def __init__(self, items):
        self._items = list(items)

    @property
    def name(self) -> str:
        return "watched-fixture"

    @property
    def display_name(self) -> str:
        return "Watched Fixture"

    async def list_sources(self) -> list[KnowledgeSource]:
        return []

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        return []

    async def get_item(self, item_id: str):
        return None

    async def poll(self, source_id: str, cursor: str = "") -> SourcePollResult:
        items, self._items = self._items, []
        return SourcePollResult(items=items, cursor="c1")


class _FakeQueue:
    def enqueue(self, item_id: str) -> None:
        pass

    def recover_pending(self) -> int:
        return 0


class _EmptyQueryStore:
    def list_queries(self):
        return []


def _cfg():
    from personalclaw.config.loader import SourcesConfig

    return SourcesConfig(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
        daily_request_budget=288,
    )


def _live_store():
    """THE store the provider will open — through `knowledge_db_path`, never a composed path."""
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


async def _ingest(items):
    """One real `SourceEngine.tick()` into the DEFAULT spool the digest reads.

    Deliberately the default `SourceEventSpool()` and the default cursor file, because the
    provider calls `run_morning_digest` with neither — passing explicit paths here would test a
    seam the shipped caller never uses.
    """
    store = _live_store()
    store.create_source(name="Hacker News", provider="watched-fixture", kind="feed")
    engine = SourceEngine(
        store,
        _FakeQueue(),
        providers_lister=lambda: [_FixtureSourceProvider(items)],
        config_loader=_cfg,
        event_spool=SourceEventSpool(),
        query_store=_EmptyQueryStore(),
    )
    await engine.tick()
    return store


def _state(tmp_path):
    """A REAL DashboardState, so `notify` runs the shipped `notification_allowed()` gate."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    sessions = MagicMock(count=0)
    sessions.remove = AsyncMock()
    sessions.get_pid = MagicMock(return_value=None)
    return DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "conv"),
    )


def _wire_services(monkeypatch, state):
    from personalclaw.action_providers import services as svc

    def _spawn(coro):
        return None

    monkeypatch.setattr(svc, "_services", svc.ActionServices(state=state, spawn_background=_spawn))


def _patch_llm(monkeypatch, calls, reply="Two releases shipped."):
    """Record the background one-shot. The digest falls back to `UNSYNTHESISED_BODY` on a falsy
    completion, so a patch that never fired would still write an item — every test that asserts
    an item therefore asserts this recorder was CALLED."""
    from personalclaw import llm_helpers

    async def _fn(prompt, use_case="", **kwargs):
        calls.append({"prompt": prompt, "use_case": use_case})
        return reply

    monkeypatch.setattr(llm_helpers, "one_shot_completion", _fn)


async def _fire_from_the_store(trigger_store):
    """Dispatch the STORED row the way `gateway._fire_store_trigger` does.

    The provider name and config are read out of the row the reconciler wrote — never a literal
    in this test. That is the whole point: if the reconciler stored a name the registry does not
    know, this returns None and the test reds, which is the "registered but never fires" defect.
    """
    from personalclaw.action_providers import get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    row = trigger_store.get(SOURCE_DIGEST_JOB_NAME)
    assert row is not None, "nothing to fire: the reconciler stored no row"
    workflow = row.trigger.workflow or {}
    inline = workflow.get("inline") if isinstance(workflow.get("inline"), dict) else None
    provider_name = str((inline or workflow).get("provider") or "")
    config = (inline or workflow).get("config") or {}

    _ensure_default_providers_registered()
    provider = get_action_provider(provider_name)
    assert provider is not None, f"the stored trigger names an unknown provider {provider_name!r}"
    return await provider.execute(config, ActionContext(event="trigger.fired"))


# ── 1. the row exists, is ARMED, and is single-flight ───────────────────────────


def test_the_bundled_trigger_is_registered_and_ARMED(trigger_store):
    # VACUITY: nothing is there before the reconciler runs, so the assertions below are about
    # what IT wrote and not about a row some other boot step happened to leave.
    assert trigger_store.get(SOURCE_DIGEST_JOB_NAME) is None

    reconcile_source_digest_cron(trigger_store)

    row = trigger_store.get(SOURCE_DIGEST_JOB_NAME)
    assert row is not None
    trigger = row.trigger
    assert trigger.kind == "clock"
    assert trigger.enabled is True
    assert trigger.spec["expr"] == SOURCE_DIGEST_SCHEDULE == "0 7 * * *"
    inline = (trigger.workflow or {}).get("inline") or {}
    assert inline.get("provider") == "source-digest"
    # EMPTY config: no prompt text on this path for a template author to edit, which is the
    # security reasoning WS-7 chose a callable over a bundled template for.
    assert inline.get("config") == {}
    # The digest's OUTPUT is a notification; a cron-result toast about it would be a
    # notification about your notification.
    assert trigger.delivery == "none"
    # 🔴 ARMED is the difference between a registered digest and one that runs.
    assert trigger.next_fire_at
    assert trigger.next_fire_at.endswith("07:00:00+00:00")
    # SINGLE-FLIGHT: this is the half of the one-notification-per-run guarantee the cursor does
    # NOT provide. `overlap: skip` makes the claim lock in `triggers/firepath.py` refuse a
    # concurrent fire; `parallel` here would let two runs read the same cursor.
    assert trigger.overlap == "skip"
    assert row.ok, row.errors


def test_reconciling_twice_does_not_duplicate_or_reset(trigger_store):
    """Creation-only, like the monthly recap: "morning" is the feature, not a setting, so a
    schedule the user edited by hand must survive every restart."""
    reconcile_source_digest_cron(trigger_store)
    before = len(trigger_store.load())

    edited = trigger_store.get(SOURCE_DIGEST_JOB_NAME).trigger
    edited.spec = {**edited.spec, "expr": "15 6 * * 1-5"}
    trigger_store.upsert(edited)

    reconcile_source_digest_cron(trigger_store)
    assert len(trigger_store.load()) == before
    assert trigger_store.get(SOURCE_DIGEST_JOB_NAME).trigger.spec["expr"] == "15 6 * * 1-5"


# ── 2. the stored name is DISPATCHABLE, not merely stored ───────────────────────


def test_the_stored_provider_name_is_dispatchable_and_governed(trigger_store):
    """A row the scheduler would refuse is the same silence as no row at all."""
    from personalclaw.action_providers import get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.guardrails.rungs import action_type_for_provider, ensure_core_action_types
    from personalclaw.triggers.screen import provider_is_read_only
    from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

    reconcile_source_digest_cron(trigger_store)
    inline = (trigger_store.get(SOURCE_DIGEST_JOB_NAME).trigger.workflow or {})["inline"]
    name = inline["provider"]

    _ensure_default_providers_registered()
    assert get_action_provider(name) is not None
    # The scheduler validates a system trigger exactly like a user-authored hook.
    assert name in ALLOWED_HOOK_PROVIDERS
    # Write-capable: it writes a knowledge item and notifies, unattended, on a cron — so the
    # decision-7 fence needs the frozen grant rather than the auto-fire default.
    assert provider_is_read_only(name) is False
    # And it is DECLARED in the guardrail table, so the dispatch seams can tell it from an
    # ungoverned action.
    ensure_core_action_types()
    spec = action_type_for_provider(name)
    assert spec is not None and spec.key == "action.knowledge_write"

    # VACUITY: the same three lookups for a name nothing registers all come back negative, so
    # the assertions above are not tautologies over permissive containers.
    assert get_action_provider("source-digest-that-does-not-exist") is None
    assert "source-digest-that-does-not-exist" not in ALLOWED_HOOK_PROVIDERS
    assert action_type_for_provider("source-digest-that-does-not-exist") is None


# ── 3. THE REACHABILITY TEST: the digest runs THROUGH the stored row ────────────


@pytest.mark.asyncio
async def test_the_digest_RUNS_through_the_stored_trigger(trigger_store, tmp_path, monkeypatch):
    """ONE item + ONE notification, dispatched from the row the reconciler wrote (SC#10)."""
    store = await _ingest(
        [
            SourceItem(guid="g1", title="Release 2.0", content="stable"),
            SourceItem(guid="g2", title="Release 2.1", content="also stable"),
            SourceItem(guid="g3", title="Release 2.2", content="more"),
        ]
    )
    state = _state(tmp_path)
    _wire_services(monkeypatch, state)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls)

    reconcile_source_digest_cron(trigger_store)
    result = await _fire_from_the_store(trigger_store)

    assert result.success is True, result.error
    assert "created" in result.stdout and "from 3 items" in result.stdout

    # THREE items in, ONE digest item out — through the real store the provider opened itself.
    rows = store.db.execute(
        "SELECT id FROM items WHERE provider = ?", (sd.DIGEST_PROVIDER,)
    ).fetchall()
    assert len(rows) == 1, "three items in must produce exactly ONE digest item"
    # ONE notification, through the real gate.
    assert len(state._notification_log) == 1
    # The background one-shot really fired on the reasoning axis. Without this the fallback body
    # would have produced an item anyway and the counts above would pass vacuously.
    assert len(calls) == 1
    assert calls[0]["use_case"] == "background"
    # Isolation: the cursor the run advanced landed under tmp_path, not the real home.
    cursor = sd.cursor_path()
    assert cursor.is_relative_to(tmp_path)
    assert sd.read_cursor() > 0


@pytest.mark.asyncio
async def test_mute_all_still_suppresses_through_the_trigger(trigger_store, tmp_path, monkeypatch):
    """🔴 VACUITY GUARD for the clause above, and the proof the gate is not bypassed.

    A caller that pushed its own notification instead of going through
    `DashboardState.notify` → `notification_allowed()` would deliver here.
    """
    from personalclaw import notification_kinds
    from personalclaw.providers import entity_routes

    settings = tmp_path / "home" / "entity_settings"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "notifications.json").write_text(json.dumps({"mute_all": True}))
    # PRECONDITION, asserted not assumed: an isolation leak must fail HERE rather than
    # masquerading as a security-control bypass (WS-7 measured exactly that confusion).
    assert entity_routes.notification_allowed(notification_kinds.INFO) is False

    store = await _ingest([SourceItem(guid="g1", title="Release 2.0", content="stable")])
    state = _state(tmp_path)
    _wire_services(monkeypatch, state)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls)

    reconcile_source_digest_cron(trigger_store)
    result = await _fire_from_the_store(trigger_store)

    assert result.success is True, result.error
    # The digest ITEM is still written — the library is not a notification — but nothing
    # delivered.
    rows = store.db.execute(
        "SELECT id FROM items WHERE provider = ?", (sd.DIGEST_PROVIDER,)
    ).fetchall()
    assert len(rows) == 1
    assert state._notification_log == []
    assert len(calls) == 1


# ── 4. a second fire posts NOTHING (the double-post guarantee) ──────────────────


@pytest.mark.asyncio
async def test_a_second_fire_posts_nothing(trigger_store, tmp_path, monkeypatch):
    """A retry, a restart, or a hand-run right after the cron must not double-post.

    The guard is `<home>/sources/digest_cursor.json`, which advances past the window only after
    the item is durable — so the second fire reads an EMPTY window. Concurrent fires are refused
    one level up by the overlap claim lock (asserted as `overlap == "skip"` above).
    """
    store = await _ingest([SourceItem(guid="g1", title="Release 2.0", content="stable")])
    state = _state(tmp_path)
    _wire_services(monkeypatch, state)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls)
    reconcile_source_digest_cron(trigger_store)

    first = await _fire_from_the_store(trigger_store)
    second = await _fire_from_the_store(trigger_store)

    assert first.success is True and "created" in first.stdout
    # An empty window is a SUCCESS with nothing to show, not a red cron.
    assert second.success is True
    assert second.stdout == "source digest: no new items"

    rows = store.db.execute(
        "SELECT id FROM items WHERE provider = ?", (sd.DIGEST_PROVIDER,)
    ).fetchall()
    assert len(rows) == 1, "the second fire must not mint a second digest item"
    assert len(state._notification_log) == 1, "the second fire must not notify again"
    # And it spent no model call, which is why leaving the trigger enabled on a quiet home costs
    # nothing.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_an_unwired_state_refuses_rather_than_bypassing_the_gate(
    trigger_store, tmp_path, monkeypatch
):
    """No `DashboardState` means no gate. Running anyway would notify past `mute_all`."""
    from personalclaw.action_providers import services as svc

    await _ingest([SourceItem(guid="g1", title="Release 2.0", content="stable")])
    monkeypatch.setattr(svc, "_services", None)
    reconcile_source_digest_cron(trigger_store)

    result = await _fire_from_the_store(trigger_store)
    assert result.success is False
    assert "no dashboard state" in result.error


# ── 5. the CALL SITE: boot really calls the reconciler ──────────────────────────


def test_gateway_boot_calls_the_reconciler():
    """🔴 THE ATOM'S UNMET CLAUSE, asserted directly. Anchored on `_init_cron`'s source with
    comment lines stripped — a text scanner that read comments would pass on the explanatory
    block beside the call. This is the house pattern (`test_gateway.py::TestInitCron`), used
    because a MagicMock orchestrator answers any attribute and would pass vacuously.
    """
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._init_cron)
    code = "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#"))

    assert "reconcile_source_digest_cron(_trigger_store)" in code
    # Inside the `--no-crons` else-branch with every other unattended writer: a harness run must
    # not emit a digest.
    guard = code.index("if self._no_crons:")
    assert code.index("reconcile_source_digest_cron(_trigger_store)") > guard

    # VACUITY, two ways. (i) The scan can find a call that IS there — so a passing assertion
    # above is not the scan silently matching nothing. (ii) A name nothing calls is absent — so
    # it is not matching everything.
    assert "reconcile_usage_recap_cron(_trigger_store)" in code
    assert "reconcile_source_digest_cron_typo(_trigger_store)" not in code
