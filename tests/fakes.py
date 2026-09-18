"""In-tree fakes for core seam tests.

Core tests must not import from app bundles (apps/ is a SIBLING workspace dir —
a standalone clone of this package doesn't have it). Tests that need a concrete
model-provider TYPE on the registry (resolution, catalog, override threading)
register this fake instead of importing the ollama app's module.
"""

from __future__ import annotations

from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

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
