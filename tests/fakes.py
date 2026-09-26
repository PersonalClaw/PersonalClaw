"""In-tree fakes for core seam tests.

Core tests must not import from app bundles (apps/ is a SIBLING workspace dir —
a standalone clone of this package doesn't have it). Tests that need a concrete
model-provider TYPE on the registry (resolution, catalog, override threading)
register this fake instead of importing the ollama app's module.

The same rule applies to the TASK provider seam: a test that wants to prove what the
aggregator does with a provider registers ``FakeTaskProvider`` rather than patching the
aggregator, which is where the filters, the sort and the paging it means to check live.

And to the EVENT BUS: a test that wants an event to fire a trigger drives it through the gateway's
real router with ``with_event_router`` rather than calling a matcher or a dispatch by hand, so the
event takes the production path — match, the gate walk, the one store dispatch.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.tasks.models import Task
from personalclaw.tasks.provider import TaskProvider

FAKE_MODEL_TYPE = "fake-model"

FAKE_MODEL_CAPABILITY = ProviderCapability(
    type=FAKE_MODEL_TYPE,
    capabilities=frozenset(
        {
            Capability.CHAT,
            Capability.CODE_TOOLS,
            Capability.STREAMING,
            Capability.VISION,
            Capability.EMBEDDING,
        }
    ),
    supports_streaming=True,
    supports_tools=True,
    supports_embeddings=True,
    supports_vision=True,
    max_context_tokens=0,
    notes="test fake — a concrete model-provider type for core seam tests",
)


class FakeModelProvider:
    """Minimal stand-in built by the fake factory. Records the resolved model so
    tests can assert the ``model`` build-kwarg override semantics every real
    factory honors (model kwarg wins over entry.model)."""

    supports_tools = True

    def __init__(self, entry: ProviderEntry, model: str = "") -> None:
        self._entry = entry
        self._model = model or entry.model


def _factory(entry: ProviderEntry, model: str = "", **_kw: object) -> FakeModelProvider:
    return FakeModelProvider(entry, model=model)


def ensure_fake_model_type(registry: ProviderRegistry) -> None:
    """Idempotently register the fake model-provider type on ``registry``.

    register_type raises on duplicates (by design), and the default registry is
    a process singleton shared across test files — so check before registering.
    """
    if FAKE_MODEL_TYPE not in registry._factories:  # noqa: SLF001 (test helper)
        registry.register_type(FAKE_MODEL_CAPABILITY, _factory)


class BoundEmbedder:
    """An embedding provider that is bound and actually yields a vector.

    Lives here, shared, rather than being re-stubbed per test file. RET-2 made an ingest
    that wrote no vector and no chunk persist ``processing_status='unsearchable'`` instead
    of ``done`` (``knowledge.searchability.verdict_for_ingest``), so every runner test
    whose subject is something ELSE — the graph, slicing, URL routing, the ingest queue, an
    event emit site — has to say out loud that its embedding provider IS bound, or it
    asserts the terminal status of a condition it never meant to create. Four hand-rolled
    copies of this stub would be four chances to drift from what that verdict counts as a
    bound embedder.
    """

    def is_available(self) -> bool:
        return True

    def embed_for_item(self, title: str, summary: str, content: str | None = None) -> list[float]:
        return [0.5, 0.25, 0.125, 0.0625]


# ── task providers ───────────────────────────────────────────────────────────────────────────
#
# The aggregation seam (`tasks.registry.collect_tasks`) has to page a provider to
# completeness, so a test that wants to prove it did needs a provider that actually SERVES
# windows. Patching the aggregator instead hides the thing under test: the `mine` lens, the
# filters and the sort all live inside it.


class FakeTaskProvider(TaskProvider):
    """A paging task provider over rows handed in at construction.

    Mirrors ``NativeTaskProvider.list_tasks``: it applies the status/assignee/project
    filters, reports the PRE-slice count as ``total``, and honours ``limit``/``offset``.
    Every request is recorded in :attr:`requests`, so a test can assert the aggregator paged
    rather than inferring it from the row count.
    """

    def __init__(
        self,
        tasks: list[Task] | None = None,
        name: str = "fake",
        *,
        ignore_offset: bool = False,
        total_override: int | None = None,
        max_page: int | None = None,
    ) -> None:
        self._tasks = list(tasks or [])
        self._name = name
        #: Models a provider that silently ignores `offset` — the case the aggregator's
        #: per-provider bound exists to terminate.
        self._ignore_offset = ignore_offset
        #: Models a provider whose reported `total` disagrees with what it will serve.
        self._total_override = total_override
        #: Models a remote provider that clamps `limit` to its own maximum page size, so a full
        #: request comes back SHORT while more rows exist.
        self._max_page = max_page
        self.requests: list[tuple[int, int]] = []

    @property
    def name(self) -> str:
        return self._name

    async def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
        project: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        self.requests.append((limit, offset))
        rows = self._tasks
        if status:
            rows = [t for t in rows if t.status.value == status]
        if assignee:
            rows = [t for t in rows if t.assignee == assignee]
        if project:
            rows = [t for t in rows if t.project == project]
        total = self._total_override if self._total_override is not None else len(rows)
        start = 0 if self._ignore_offset else offset
        served = min(limit, self._max_page) if self._max_page else limit
        return rows[start : start + served], total

    async def get_task(self, task_id: str) -> Task | None:
        return next((t for t in self._tasks if t.id == task_id), None)

    async def create_task(self, **fields: object) -> Task:
        raise NotImplementedError("read-only fake")

    async def update_task(self, task_id: str, **fields: object) -> Task | None:
        raise NotImplementedError("read-only fake")

    async def delete_task(self, task_id: str) -> bool:
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.id != task_id]
        return len(self._tasks) < before


def only_task_provider(monkeypatch, provider: TaskProvider) -> None:
    """Make ``provider`` the ONLY registered task provider for one test.

    Replaces the registry's provider dict wholesale (monkeypatch restores it), so the
    native provider cannot contribute rows from whatever home the test is pointed at.
    """
    from personalclaw.tasks import registry as task_registry

    monkeypatch.setattr(task_registry, "_providers", {provider.name: provider})
    monkeypatch.setattr(task_registry, "_ensure_native", lambda: None)


# ── the gateway half of the event bus ──


class QuietDashboardState:
    """A dashboard state that accepts every notification and refresh — for a test asserting
    elsewhere."""

    def notify(self, *args: object, **kwargs: object) -> bool:
        return True

    def push_refresh(self, *kinds: str) -> None:
        return None


async def with_event_router(act: Callable[[], Any], *, dashboard_state: Any = None) -> None:
    """Run ``act()`` with the gateway's real event router attached, then wait out every fire.

    A bare ``GatewayOrchestrator`` (no subsystems) whose router dispatches through the real
    ``_fire_store_trigger``, attached and detached exactly as the gateway's boot and shutdown do.
    ``act`` may return an awaitable (an engine's ``tick()``); it is awaited before the router
    settles, so every fire the act caused has finished — and recorded its run — when this returns.
    """
    from personalclaw.gateway import GatewayOrchestrator

    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = dashboard_state if dashboard_state is not None else QuietDashboardState()
    orch._start_event_triggers()
    try:
        result = act()
        if inspect.isawaitable(result):
            await result
        await orch._event_router.settle()
    finally:
        orch._stop_event_triggers()


def fire_through_the_gateway(act: Callable[[], Any], *, dashboard_state: Any = None) -> None:
    """``with_event_router`` for a synchronous test: its own event loop, run to completion."""
    asyncio.run(with_event_router(act, dashboard_state=dashboard_state))


@dataclass(frozen=True)
class EventFire:
    """What one real event fire did, read off the run row it left in the ledger."""

    ran: bool
    status: str
    reason: str


def fire_memory_event_trigger(
    provider_name: str,
    *,
    config: dict | None = None,
    trigger_id: str = "t-acme",
    key: str = "project.acme.status",
    value: str = "green",
    dashboard_state: Any = None,
) -> EventFire:
    """Fire a stored memory `event` trigger whose action is ``provider_name``, the real way.

    The trigger is written once (with its capability set frozen, as every real writer freezes
    it) and a memory write is put on the bus; the gateway's router matches it, the gate walk
    admits it, and the one store dispatch — screen, fence, denylist, rung ladder, the run
    record — decides. The caller's home isolation decides where all of that lands.
    """
    import time

    from personalclaw.config.loader import config_dir
    from personalclaw.event_triggers import MEMORY_UPDATE, SOURCE_MEMORY, emit_event, event_spec
    from personalclaw.schedule_history import ScheduleRunStore
    from personalclaw.triggers import screen
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    home = config_dir()
    store = TriggerStore(base_dir=home)
    if store.get(trigger_id) is None:
        trigger = Trigger(
            id=trigger_id,
            name=trigger_id,
            kind="event",
            enabled=True,
            spec=event_spec(MEMORY_UPDATE),
            workflow={"inline": {"provider": provider_name, "config": dict(config or {})}},
        )
        trigger.capabilities = screen.capabilities_for_action(trigger)
        store.upsert(trigger)

    async def _fire() -> dict:
        ledger = ScheduleRunStore(home)
        _before, recorded = await ledger.list_for_job(trigger_id, 0, 1)
        await with_event_router(
            lambda: emit_event(
                source=SOURCE_MEMORY, event_type="update", key=key, value=value, now=time.time()
            ),
            dashboard_state=dashboard_state,
        )
        runs, total = await ledger.list_for_job(trigger_id, 0, 1)
        # Only a row THIS fire wrote answers for it. A refusal that records nothing (the incident
        # gate's `refused` is not a suppression row) must not borrow the previous fire's row.
        return runs[0] if total > recorded else {}

    row = asyncio.run(_fire())
    status = str(row.get("status") or "")
    return EventFire(ran=status == "success", status=status, reason=str(row.get("error") or ""))
