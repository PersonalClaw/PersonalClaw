"""Settings B10 — the learned-fact confidence has ONE setting, and it is the one the gate reads.

Settings → Providers → Vector Memory offered a "Confidence Threshold" (0–1). It saved into the
app's provider config and nothing read it: `memory_providers.registry.create_default_provider`
discards its `config`. The gate it described reads `memory.semantic_confidence_threshold` — the
confidence a LEARNED fact needs before `VectorMemoryStore.validate_semantic` keeps it — and no
control wrote that. (It is a gate on what memory keeps, not a recall filter: recall never reads
it, and a fact the user states is always kept.)

So the app field is gone and the core setting has the control: Settings → Memory → "Learned-fact
confidence", written through the `_EDITABLE_CONFIG` PATCH, read back by the Memory settings GET,
and read by the store on every write — a change takes effect on the next learned fact, where the
constructor pin the two servers used to pass meant a restart.

The same inert shape shipped in three more core apps, which the family rail at the bottom found:
native-skills (`max_triggered`, `auto_create_from_sessions` — the gateway reads `skills.*`),
native-tasks (`storage_dir`) and personalclaw-tools (`sandbox_mode`, and `require_approval`: an
approval switch that gated nothing). Their settings blocks are gone too.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import personalclaw
from personalclaw.vector_memory import SemanticRejectCode, VectorMemoryStore

_NATIVE = Path(personalclaw.__file__).resolve().parent / "apps" / "native"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    from personalclaw.config.loader import config_path

    assert str(config_path()).startswith(str(tmp_path)), "the redirect did not reach the loader"
    return tmp_path


def _set_threshold(home: Path, value: float) -> None:
    (home / "config.json").write_text(
        json.dumps({"memory": {"semantic_confidence_threshold": value}}), encoding="utf-8"
    )


def _async(value):
    async def _coro():
        return value

    return _coro


def _patch(path: str, value) -> "object":
    from personalclaw.dashboard.handlers import core

    req = MagicMock()
    req.app = {"state": MagicMock()}
    req.headers = {}
    req.get = lambda k, d=None: {"user": "owner"}.get(k, d)
    req.json = _async({"path": path, "value": value})
    return asyncio.run(core.api_personalclaw_config_patch(req))


# ── the gate follows the setting ─────────────────────────────────────────────────────────


def test_the_learned_fact_gate_follows_the_setting_without_a_restart(home):
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()

    _set_threshold(home, 0.3)
    assert (
        store.set_semantic("pref.editor", "helix", 0.5, "consolidation:week-1") is None
    ), "a 0.5 fact is above a 0.3 setting and must be kept"

    _set_threshold(home, 0.9)  # the same store, no restart
    refused = store.set_semantic("pref.shell", "fish", 0.85, "consolidation:week-2")
    assert refused is not None and refused[0] == SemanticRejectCode.CONFIDENCE, refused

    # What the user states is never gated, at any setting.
    assert store.set_semantic("pref.os", "macos", 0.1, "user_explicit") is None


def test_an_unreadable_config_keeps_the_gate_on(home):
    """Fail-safe: a broken config.json must not turn the gate off (it falls back to 0.8)."""
    (home / "config.json").write_text("{ not json", encoding="utf-8")
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    refused = store.set_semantic("pref.shell", "fish", 0.5, "consolidation:week-2")
    assert refused is not None and refused[0] == SemanticRejectCode.CONFIDENCE


# ── the control writes the leaf the gate reads ───────────────────────────────────────────


def test_the_settings_control_writes_the_leaf_the_gate_reads(home):
    resp = _patch("memory.semantic_confidence_threshold", 0.6)
    assert resp.status == 200, resp.text
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert on_disk["memory"]["semantic_confidence_threshold"] == 0.6

    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    assert store.confidence_threshold == 0.6
    assert store.set_semantic("pref.editor", "helix", 0.65, "consolidation:week-1") is None
    refused = store.set_semantic("pref.shell", "fish", 0.55, "consolidation:week-2")
    assert refused is not None and refused[0] == SemanticRejectCode.CONFIDENCE


@pytest.mark.parametrize("value", [1.5, -0.1, "high", True])
def test_a_threshold_outside_zero_to_one_is_refused(home, value):
    resp = _patch("memory.semantic_confidence_threshold", value)
    assert resp.status == 400, (value, resp.text)
    assert json.loads((home / "config.json").read_text(encoding="utf-8")) == {}


def test_a_hand_edited_value_is_clamped_on_load(home):
    """The PATCH refuses out-of-range values; a hand edit is clamped, so the gate cannot be set
    to a bound that keeps nothing (above 1) or everything (below 0)."""
    from personalclaw.config.loader import AppConfig

    _set_threshold(home, 7)
    assert AppConfig.load().memory.semantic_confidence_threshold == 1.0
    _set_threshold(home, -3)
    assert AppConfig.load().memory.semantic_confidence_threshold == 0.0


def test_the_memory_settings_read_carries_the_value_the_control_renders(home):
    from personalclaw.dashboard.handlers import memory as memory_handlers

    _set_threshold(home, 0.7)
    req = MagicMock()
    req.method = "GET"
    req.app = {"state": MagicMock()}
    resp = asyncio.run(memory_handlers.api_memory_settings(req))
    assert resp.status == 200, resp.text
    assert json.loads(resp.text)["semantic_confidence_threshold"] == 0.7


def test_the_vector_memory_app_no_longer_offers_the_setting():
    manifest = json.loads((_NATIVE / "native-vector-memory" / "app.json").read_text("utf-8"))
    assert "settingsSchema" not in manifest["provider"], manifest["provider"]


# ── the family: a core app's provider setting must reach a reader ────────────────────────


def _factory(manifest_dir: Path, implementation: str) -> ast.FunctionDef | None:
    """The factory function a manifest's ``implementation`` names, parsed — never imported."""
    module, _, name = implementation.partition(":")
    candidates = [manifest_dir / f"{module.rsplit('.', 1)[-1]}.py"]
    if module.startswith("personalclaw."):
        rel = Path(*module.split("."))
        root = Path(personalclaw.__file__).resolve().parent.parent
        candidates = [root / rel.with_suffix(".py"), root / rel / "__init__.py"]
    for path in candidates:
        if path.is_file():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                    return node  # type: ignore[return-value]
    return None


def _reads_its_config(fn: ast.FunctionDef) -> bool:
    params = {a.arg for a in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]}
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return bool(params & used)


def _audit(native_root: Path) -> tuple[list[str], list[str], list[str]]:
    """``(read, discarded, unresolved)`` — apps whose declared settings reach their factory,
    apps whose factory ignores them, and apps whose factory could not be found."""
    read: list[str] = []
    discarded: list[str] = []
    unresolved: list[str] = []
    for manifest_path in sorted(native_root.glob("*/app.json")):
        provider = json.loads(manifest_path.read_text(encoding="utf-8")).get("provider") or {}
        properties = (provider.get("settingsSchema") or {}).get("properties") or {}
        # An ACTION provider's settingsSchema is not factory config: it is the per-fire action
        # config a trigger stores and hands to `execute(config, …)` on every fire.
        if not properties or provider.get("type") == "action":
            continue
        name = manifest_path.parent.name
        fn = _factory(manifest_path.parent, str(provider.get("implementation") or ""))
        if fn is None:
            unresolved.append(name)
        elif _reads_its_config(fn):
            read.append(name)
        else:
            discarded.append(f"{name}: {sorted(properties)}")
    return read, discarded, unresolved


def test_no_core_app_declares_a_setting_its_factory_discards():
    """The registry hands an app's saved provider settings to its factory and to nothing else
    (`providers/registry.py`: `factory(config)`), so a factory that never reads its argument
    turns every declared field into a control that saves and does nothing."""
    read, discarded, unresolved = _audit(_NATIVE)
    assert (
        not unresolved
    ), f"factories this census could not find, so it measured nothing: {unresolved}"
    # VACUITY FLOOR: the providers whose settings ARE read must be seen as reading them.
    assert {"bundled-chat", "ollama-models"} <= set(read), read
    assert not discarded, (
        f"these core apps declare settings their factory never reads: {discarded}. Wire the "
        "field to its reader or delete it — a saved value nothing reads is a promise the code "
        "ignores."
    )


def test_the_family_rail_catches_a_factory_that_discards_its_settings(tmp_path):
    """The rail's own positive control, on a planted app: the shape the four real ones had."""
    app = tmp_path / "planted-app"
    app.mkdir()
    (app / "app.json").write_text(
        json.dumps(
            {
                "name": "planted-app",
                "provider": {
                    "type": "memory",
                    "implementation": "provider:create_provider",
                    "settingsSchema": {"properties": {"threshold": {"type": "number"}}},
                },
            }
        ),
        encoding="utf-8",
    )
    (app / "provider.py").write_text(
        "def create_provider(config=None):\n    return object()\n", encoding="utf-8"
    )
    read, discarded, unresolved = _audit(tmp_path)
    assert discarded == ["planted-app: ['threshold']"] and not read and not unresolved
