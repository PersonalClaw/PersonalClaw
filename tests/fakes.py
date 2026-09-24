"""In-tree fakes for core seam tests.

Core tests must not import from app bundles (apps/ is a SIBLING workspace dir —
a standalone clone of this package doesn't have it). Tests that need a concrete
model-provider TYPE on the registry (resolution, catalog, override threading)
register this fake instead of importing the ollama app's module.

The same rule applies to the TASK provider seam: a test that wants to prove what the
aggregator does with a provider registers ``FakeTaskProvider`` rather than patching the
aggregator, which is where the filters, the sort and the paging it means to check live.
"""

from __future__ import annotations

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
