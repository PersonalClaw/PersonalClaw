"""The tool-description index: kept across restarts, filled in groups, one model at a time.

`tool_vectors.ToolVectors` is what every runtime's tool retrieval reads its vectors from. These
pin the promises its callers rely on: a restart embeds nothing unchanged, only a changed
description is embedded again, what is missing goes to the embedding server in groups, vectors of
two models never mix, and a file that cannot be read, or a server that is down, costs a ranking by
words rather than a failure.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.agents.native import tool_vectors as tv
from personalclaw.embedding_providers import registry as embedding_registry


class _Server:
    """An embedding server: the groups it was asked for, and whether it answers."""

    def __init__(self, model: str = "local:embed-a") -> None:
        self.model = model
        self.groups: list[list[str]] = []
        self.down = False

    def one(self, text: str) -> list[float] | None:
        return None if self.down else self._vector(text)

    def many(self, texts: list[str]) -> list[list[float] | None]:
        self.groups.append(list(texts))
        if self.down:
            raise ConnectionError("embedding server unreachable")
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        return [float(len(text)), float(sum(map(ord, text)) % 97), 1.0]

    def embedder(self) -> tv.Embedder:
        return tv.Embedder(model=self.model, one=self.one, many=self.many)

    def texts(self) -> list[str]:
        return [t for group in self.groups for t in group]


def _catalog(n: int, *, prefix: str = "tool") -> list[str]:
    return [tv.tool_text(f"{prefix}_{i}", f"does thing {i}") for i in range(n)]


def _fill(index: tv.ToolVectors, path, server: _Server, texts: list[str]) -> None:
    index.want(path, server.embedder(), texts)
    assert index.drain(timeout=10), "the background fill never finished"


def test_a_restart_embeds_nothing_it_embedded_before(tmp_path):
    path = tmp_path / tv.TOOL_VECTORS_FILE
    server = _Server()
    texts = _catalog(40)
    _fill(tv.ToolVectors(), path, server, texts)
    assert sorted(server.texts()) == sorted(texts)

    restarted = tv.ToolVectors()
    known = restarted.vectors(path, server.model, texts)
    assert set(known) == set(texts)
    restarted.want(path, server.embedder(), texts)
    assert restarted.drain(timeout=10)
    assert len(server.texts()) == len(texts), "the restart embedded the catalog again"


def test_only_a_changed_description_is_embedded_again(tmp_path):
    path = tmp_path / tv.TOOL_VECTORS_FILE
    server = _Server()
    texts = _catalog(10)
    _fill(tv.ToolVectors(), path, server, texts)
    changed = texts[:9] + [tv.tool_text("tool_9", "does something else now")]
    index = tv.ToolVectors()
    _fill(index, path, server, changed)
    assert server.groups[-1] == [changed[-1]]
    assert set(index.vectors(path, server.model, changed)) == set(changed)


def test_what_is_missing_is_embedded_in_groups(tmp_path):
    server = _Server()
    _fill(tv.ToolVectors(), tmp_path / tv.TOOL_VECTORS_FILE, server, _catalog(70))
    assert [len(g) for g in server.groups] == [32, 32, 6]


def test_a_text_asked_for_while_it_is_being_embedded_is_embedded_once(tmp_path):
    path = tmp_path / tv.TOOL_VECTORS_FILE
    server = _Server()
    index = tv.ToolVectors()
    texts = _catalog(20)
    index.want(path, server.embedder(), texts)
    index.want(path, server.embedder(), texts)
    assert index.drain(timeout=10)
    assert sorted(server.texts()) == sorted(texts)


def test_a_new_embedding_model_embeds_again_and_never_mixes_vectors(tmp_path):
    path = tmp_path / tv.TOOL_VECTORS_FILE
    first, second = _Server("local:embed-a"), _Server("local:embed-b")
    texts = _catalog(5)
    index = tv.ToolVectors()
    _fill(index, path, first, texts)
    assert index.vectors(path, second.model, texts) == {}
    _fill(index, path, second, texts)
    assert sorted(second.texts()) == sorted(texts)
    assert json.loads(path.read_text())["model"] == second.model
    assert tv.ToolVectors().vectors(path, first.model, texts) == {}


def test_an_unreadable_file_reads_as_empty_and_is_written_again(tmp_path):
    path = tmp_path / tv.TOOL_VECTORS_FILE
    path.write_text("{not json", encoding="utf-8")
    server = _Server()
    texts = _catalog(3)
    index = tv.ToolVectors()
    assert index.vectors(path, server.model, texts) == {}
    _fill(index, path, server, texts)
    assert set(tv.ToolVectors().vectors(path, server.model, texts)) == set(texts)


def test_a_text_that_could_not_be_embedded_is_not_asked_for_again_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.knowledge.embed_batch.time.sleep", lambda _s: None)
    path = tmp_path / tv.TOOL_VECTORS_FILE
    server = _Server()
    server.down = True
    texts = _catalog(2)
    index = tv.ToolVectors()
    _fill(index, path, server, texts)
    assert server.groups, "the server was never asked"
    asked = len(server.groups)
    index.want(path, server.embedder(), texts)
    assert index.drain(timeout=10)
    assert len(server.groups) == asked, "a server that is down was asked again at once"
    assert index.vectors(path, server.model, texts) == {}


def test_the_file_keeps_only_the_newest_vectors(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "MAX_VECTORS", 8)
    path = tmp_path / tv.TOOL_VECTORS_FILE
    server = _Server()
    old, new = _catalog(6, prefix="old"), _catalog(6, prefix="new")
    index = tv.ToolVectors()
    _fill(index, path, server, old)
    _fill(index, path, server, new)
    kept = tv.ToolVectors().vectors(path, server.model, old + new)
    assert set(new) <= set(kept) and len(kept) == 8


# ── the batch accessor for a configured model provider ──


class _Provider:
    """A configured model provider whose ``embed`` takes a list, as Ollama's does."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail = False

    async def start(self) -> None:
        return None

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        self.calls.append(list(inputs))
        if self.fail:
            raise RuntimeError("413 payload too large")
        return [[float(len(t))] for t in inputs]


@pytest.fixture
def configured_provider(monkeypatch):
    import personalclaw.llm.registry as llm_registry

    provider = _Provider()

    class _Registry:
        def build(self, name: str, **_kw: object) -> _Provider:
            return provider

    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: _Registry())
    monkeypatch.setattr(embedding_registry, "_ensure_scanned", lambda: None)
    monkeypatch.setattr(embedding_registry, "_providers", {})
    return provider


def test_a_configured_provider_embeds_a_group_in_one_request(configured_provider):
    many = embedding_registry.embed_many_fn_for("local-embed", "embed-model")
    assert many is not None
    assert many(["a", "bb", "ccc"]) == [[1.0], [2.0], [3.0]]
    assert configured_provider.calls == [["a", "bb", "ccc"]]


def test_a_group_the_provider_refuses_raises_so_it_can_be_split(configured_provider):
    """`embed_batch.embed_texts` retries and splits a group on the exception; a swallowed one
    would read as a group embedded with no vectors."""
    configured_provider.fail = True
    many = embedding_registry.embed_many_fn_for("local-embed", "embed-model")
    assert many is not None
    with pytest.raises(RuntimeError, match="payload too large"):
        many(["a", "b"])
