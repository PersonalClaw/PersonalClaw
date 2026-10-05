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

And to the SECOND OPINION on an attention row: a test raised with no event loop waits for the
verdict's whole hand-over with ``wait_for_verdicts``, not for the verdict word to appear.

And to a BOUNDED READ: a test that times out a read on a worker holds the worker with
``held_workers`` and lets it go before the test ends, since nothing can stop it.

And to a RUNNING GATEWAY a command talks to: a test serves ``gateway_stand_in`` on a loopback port
of its own, which answers ``/api/healthz`` as the gateway of the home it is given and records
every request it is sent, rather than patching ``urllib``. Commands reach their gateway through
``home_gateway``, whose transport takes no proxy and no patched ``urlopen``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import inspect
import json
import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from unittest import mock
from urllib.parse import unquote

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

    Lives here, shared, rather than being re-stubbed per test file. An ingest
    that wrote no vector and no chunk persists ``processing_status='unsearchable'`` instead
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

    async def update_task(
        self, task_id: str, *, base_revision: str | None = None, **fields: object
    ) -> Task | None:
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


def wait_for_verdicts(monkeypatch) -> Callable[[Any, str], Any]:
    """A wait for the second opinion on an attention row to be handed over, whole.

    With no event loop the verify worker runs ``inbox.apply_verdict`` itself: it writes the
    verdict on the row, then saves the row and files it or fires its notification. A wait that
    returned the moment the verdict appeared read the row, its status and the notification count
    before the rest had run. So this one waits for ``apply_verdict`` to return, and gives back
    ``wait(store, item_id)``, which returns the row.
    """
    from personalclaw import inbox

    handed_over: set[str] = set()
    done = threading.Condition()
    real = inbox.apply_verdict

    def apply_verdict(state: Any, store: Any, item: Any, verdict: str) -> None:
        try:
            real(state, store, item, verdict)
        finally:
            with done:
                handed_over.add(item.id)
                done.notify_all()

    monkeypatch.setattr(inbox, "apply_verdict", apply_verdict)

    def wait(store: Any, item_id: str) -> Any:
        with done:
            if not done.wait_for(lambda: item_id in handed_over, timeout=5.0):
                raise AssertionError("the verdict never reached the row")
        return store.items[item_id]

    return wait


@dataclass
class HeldWorkers:
    """The workers a bounded read left running, and the gate they wait at (``held_workers``)."""

    #: Every pool made inside the block, in the order they were made.
    pools: list[concurrent.futures.ThreadPoolExecutor] = field(default_factory=list)
    _released: threading.Event = field(default_factory=threading.Event)

    def hold(self, timeout: float = 30.0) -> None:
        """Keep the calling worker here until the block ends: the stand-in for a slow read."""
        self._released.wait(timeout)

    def let_go(self) -> None:
        """Release every held worker and wait until each pool's threads have finished."""
        self._released.set()
        for pool in self.pools:
            pool.shutdown(wait=True)


@contextmanager
def held_workers() -> Iterator[HeldWorkers]:
    """Hold the workers a bounded read leaves behind, and let them go before the block ends.

    A timeout bounds the CALLER of a read on a worker, not the read: the worker cannot be stopped
    and finishes later into a result nobody reads. In a test, later is after the test ends and its
    home isolation is undone, so a worker that went on to resolve the home reached the real one,
    during some other test. Every worker pool made inside the block is recorded (each is a
    ``memory_writes.ScopeCarryingExecutor``, which ``test_model_reach_census`` holds the tree to); a
    stub for the slow read calls ``held.hold()``; the block's end releases the stubs and joins
    every recorded pool, so each worker finishes inside the test.
    """
    from personalclaw import memory_writes

    held = HeldWorkers()
    real = memory_writes.ScopeCarryingExecutor

    class _Recorded(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            held.pools.append(self)

    with mock.patch.object(memory_writes, "ScopeCarryingExecutor", _Recorded):
        try:
            yield held
        finally:
            held.let_go()


#: The headers a credential travels in: the home's local secret, the internal credential that is
#: the same secret, and a token minted with it.
CREDENTIAL_HEADERS = ("x-local-secret", "x-internal-secret", "authorization")


def home_fingerprint(home: Path | str) -> str:
    """The ``home_id`` a gateway of *home* answers ``/api/healthz`` with, by the recipe its
    docstring gives, written out here apart from the code that computes it, so a change to either
    side is a failure rather than a silent agreement."""
    return hashlib.sha256(str(Path(home).expanduser().resolve()).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class SeenRequest:
    """One request a stand-in gateway was sent."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def credentials(self) -> list[str]:
        """The credential headers it carried, by lower-case name."""
        return [name for name in CREDENTIAL_HEADERS if name in self.headers]


#: A route's answer: an aiohttp handler (a real one of the gateway's included), or a fixed
#: ``(status, JSON body)``.
Route = Callable[[Any], Any] | tuple[int, Any]


@dataclass
class GatewayStandIn:
    """A gateway on a loopback port of its own, served from a thread of its own (so a command can
    call it from the test's thread, and an async test's loop stays free). See
    :func:`gateway_stand_in`."""

    home: Path
    port: int
    requests: list[SeenRequest]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def credentials_sent(self) -> list[str]:
        """Every credential header a request it was sent carried, in order."""
        return [name for seen in self.requests for name in seen.credentials()]

    def asked(self, path: str) -> list[SeenRequest]:
        """The requests it was sent for *path* (its query left off)."""
        return [seen for seen in self.requests if seen.path.split("?", 1)[0] == path]

    def record(self, home: Path | None = None) -> None:
        """Write the runtime record a gateway keeps in its home once it listens, naming this
        port and a live pid (this test's), into *home* (its own, when none is named)."""
        target = home if home is not None else self.home
        target.mkdir(parents=True, exist_ok=True)
        (target / "gateway.runtime.json").write_text(
            json.dumps({"port": self.port, "pid": os.getpid()}), encoding="utf-8"
        )


@contextmanager
def gateway_stand_in(
    home: Path | str,
    *,
    routes: Mapping[tuple[str, str], Route] | None = None,
    local_secret: str = "",
) -> Iterator[GatewayStandIn]:
    """A gateway of *home*: ``GET /api/healthz`` answers as that home's gateway does (``status``,
    ``pid``, and ``home_id``, its fingerprint), and every other request is answered from *routes*,
    by ``(method, path)``, else 404. *local_secret* is the secret the gateway's own handlers check
    (``app["local_secret"]``), for a route that is one of them. Every request is recorded, its
    headers by lower-case name, before it is answered.
    """
    from aiohttp import web

    import personalclaw

    home = Path(home)
    seen: list[SeenRequest] = []
    table: dict[tuple[str, str], Route] = {
        ("GET", "/api/healthz"): (
            200,
            {
                "status": "ok",
                "version": personalclaw.__version__,
                "pid": os.getpid(),
                "home_id": home_fingerprint(home),
                "root_ok": True,
            },
        ),
        **(routes or {}),
    }

    async def answer(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        # The path as the router reads it, percent-escapes decoded, and its query as it was sent.
        path, _, query = request.raw_path.partition("?")
        path = unquote(path)
        seen.append(
            SeenRequest(
                method=request.method,
                path=f"{path}?{query}" if query else path,
                headers={k.lower(): v for k, v in request.headers.items()},
                body=body,
            )
        )
        route = table.get((request.method, path))
        if route is None:
            return web.json_response({"error": "not found"}, status=404)
        if isinstance(route, tuple):
            status, payload = route
            return web.json_response(payload, status=status)
        return await route(request)

    app = web.Application()
    app["local_secret"] = local_secret
    app.router.add_route("*", "/{tail:.*}", answer)
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(app)
    bound: dict[str, int] = {}
    ready = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        bound["port"] = runner.addresses[0][1]
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=serve, name="gateway-stand-in", daemon=True)
    thread.start()
    assert ready.wait(10), "the stand-in gateway did not start"
    try:
        yield GatewayStandIn(home=home, port=bound["port"], requests=seen)
    finally:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)
        loop.close()


def released_port() -> int:
    """A loopback port nothing listens on: bound, then let go, so the test holds it (the port
    guard lets a test ask a port it held) and a connection to it is refused."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
