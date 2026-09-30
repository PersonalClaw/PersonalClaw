"""The Doctor offers to prune a binding whose model is gone, and pruning it removes the model.

Measured on a live install: the small model vanished from the instance that served it (it no
longer listed it), and Doctor › local-models read "1 phantom binding · No automatic fix — …
Bind that use case to a model that exists in Settings → Models". The Background chain kept the
model until it was removed by hand. The Doctor's own "prune bindings" fix existed, but no check
offered it, and it pruned only bindings to REMOVED providers — never this one.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.local_models import registry as local_registry
from personalclaw.local_models.provider import LocalModel, LocalModelProvider
from personalclaw.providers.use_cases import active_models_path, load_active_models
from personalclaw.resilience.doctor import DoctorContext, _probe_local_models
from personalclaw.resilience.fixes import apply_fix, get_fix

#: The fix's stable id, as a probe names it.
PRUNE_BINDINGS_FIX = "model-providers.prune-bindings"


class _Local(LocalModelProvider):
    def __init__(self, name: str, *, up: bool, models: list[str]) -> None:
        self._name, self.up, self.models = name, up, models

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name

    async def is_available(self) -> bool:
        return self.up

    async def list_models(self):
        return [LocalModel(name=m, downloaded=True, capabilities=["chat"]) for m in self.models]

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


@pytest.fixture()
def instances(monkeypatch):
    """Three local instances, as a Background chain can use them. ``ollama-small``
    advertises ``qwen3:1.7b`` until a test takes it away."""
    monkeypatch.setattr(local_registry, "_providers", {})
    monkeypatch.setattr(local_registry, "_capabilities", {})
    made = {
        "ollama": _Local("ollama", up=True, models=["gemma4:12b"]),
        "ollama-bg": _Local("ollama-bg", up=True, models=["gemma4:12b"]),
        "ollama-small": _Local("ollama-small", up=True, models=["qwen3:1.7b"]),
    }
    for name, prov in made.items():
        local_registry.register_provider(prov, ["chat"], name=name)
    return made


CHAIN = ["ollama-bg:gemma4:12b", "ollama:gemma4:12b", "ollama-small:qwen3:1.7b"]


def _store(active: dict) -> None:
    path = active_models_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(active), encoding="utf-8")


def _stored() -> dict:
    return json.loads(active_models_path().read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_the_check_offers_the_prune_for_a_model_its_instance_no_longer_lists(instances):
    _store({"background": CHAIN})
    instances["ollama-small"].models = ["fake-llm:1b"]

    res = await _probe_local_models(DoctorContext())

    assert res.ok is False
    assert res.fix_id == PRUNE_BINDINGS_FIX, "the check names a binding it offers no way to prune"
    assert res.evidence["phantom_bindings"] == ["ollama-small:qwen3:1.7b"]
    assert "ollama-small:qwen3:1.7b" in res.detail


def test_the_prune_says_what_it_unbinds_then_the_chain_no_longer_lists_the_model(instances):
    _store({"background": CHAIN})
    instances["ollama-small"].models = ["fake-llm:1b"]
    fix = get_fix(PRUNE_BINDINGS_FIX)
    assert fix is not None

    assert fix.dry_preview() == (
        "Would unbind from Background: qwen3:1.7b (ollama-small no longer lists it)."
    )
    assert _stored()["background"] == CHAIN, "a preview changed the bindings"

    result = apply_fix(PRUNE_BINDINGS_FIX)

    assert result == {
        "ok": True,
        "fix_id": PRUNE_BINDINGS_FIX,
        "result": "Unbound qwen3:1.7b from Background.",
    }
    assert _stored()["background"] == ["ollama-bg:gemma4:12b", "ollama:gemma4:12b"]
    assert fix.dry_preview() == "No binding names a model that is gone."


@pytest.mark.asyncio
async def test_after_the_prune_the_check_passes(instances):
    _store({"background": CHAIN})
    instances["ollama-small"].models = ["fake-llm:1b"]
    # Off the loop, as `POST /api/doctor/fix/{id}` runs it.
    result = await asyncio.to_thread(apply_fix, PRUNE_BINDINGS_FIX)
    assert result["ok"] is True, result

    res = await _probe_local_models(DoctorContext())

    assert res.ok is True and res.fix_id is None


def test_an_instance_that_is_down_keeps_its_bindings(instances):
    # Its empty answer is an outage, not a list of models that are gone: nothing is pruned.
    _store({"background": CHAIN})
    instances["ollama-small"].up = False

    assert get_fix(PRUNE_BINDINGS_FIX).dry_preview() == "No binding names a model that is gone."
    apply_fix(PRUNE_BINDINGS_FIX)
    assert _stored()["background"] == CHAIN


def test_a_binding_to_a_removed_provider_is_pruned_too(instances):
    _store({"background": ["gone-provider:some-model", *CHAIN]})
    assert "gone-provider:some-model" not in load_active_models()["background"]

    assert get_fix(PRUNE_BINDINGS_FIX).dry_preview() == (
        "Would unbind from Background: some-model (gone-provider was removed)."
    )
    apply_fix(PRUNE_BINDINGS_FIX)
    assert _stored()["background"] == CHAIN


def test_the_preview_says_what_a_use_case_left_with_no_model_falls_back_to(instances):
    _store({"background": ["ollama-small:qwen3:1.7b"], "embedding": ["ollama-small:qwen3:1.7b"]})
    instances["ollama-small"].models = []

    preview = get_fix(PRUNE_BINDINGS_FIX).dry_preview()

    assert (
        "from Background: qwen3:1.7b (ollama-small no longer lists it) — Background then "
        "uses your Chat models" in preview
    )
    assert (
        "from Embedding: qwen3:1.7b (ollama-small no longer lists it) — Embedding then has "
        "no model until you choose one" in preview
    )
