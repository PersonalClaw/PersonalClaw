"""The learned-fact confidence has ONE setting, and it is the one the gate reads.

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
from collections.abc import Iterator
from pathlib import Path
from typing import Any
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


def _leaves(node: Any) -> Iterator[Any]:
    """Every scalar in a JSON document, however deep."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _leaves(value)
    elif isinstance(node, list):
        for value in node:
            yield from _leaves(value)
    else:
        yield node


def _mcp_card_fields_its_store_drops(properties: dict[str, Any]) -> list[str]:
    """The MCP Tool Servers card's fields whose value never reaches the server entry it saves.

    That card's settings are not factory config. Its instance routes save them through
    `providers/mcp_instances` into `mcp.json`, the file the MCP client starts servers from, and
    the tool handler builds its one provider over every configured server without them. So each
    field is saved alone through that store, set to a value its schema allows other than the
    default, and the value must be in the entry that lands in the file.
    """
    from personalclaw.config import loader as config_loader
    from personalclaw.providers import mcp_instances

    dropped: list[str] = []
    for field, schema in sorted(properties.items()):
        others = [v for v in schema.get("enum") or [] if v != schema.get("default")]
        # Saving starts nothing, and a value no command, path or host resolves keeps it so.
        value = others[0] if others else f"/nonexistent/rail-{field}"
        mcp_instances.create_instance(f"rail-{field}", {field: value})
        saved = json.loads((config_loader.config_dir() / "mcp.json").read_text(encoding="utf-8"))
        if value not in list(_leaves(saved["mcpServers"][f"rail-{field}"])):
            dropped.append(field)
    return dropped


def _audit(native_root: Path) -> tuple[list[str], list[str], list[str]]:
    """``(read, discarded, unresolved)`` — apps whose declared settings reach their reader,
    apps whose reader ignores them, and apps whose reader could not be found."""
    from personalclaw.providers.mcp_instances import MCP_TOOLS_EXTENSION

    read: list[str] = []
    discarded: list[str] = []
    unresolved: list[str] = []
    for manifest_path in sorted(native_root.glob("*/app.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        provider = manifest.get("provider") or {}
        properties = (provider.get("settingsSchema") or {}).get("properties") or {}
        # An ACTION provider's settingsSchema is not factory config: it is the per-fire action
        # config a trigger stores and hands to `execute(config, …)` on every fire.
        if not properties or provider.get("type") == "action":
            continue
        name = manifest_path.parent.name
        # Keyed on the name the instance routes and the tool handler route this card by.
        if manifest.get("name") == MCP_TOOLS_EXTENSION:
            dropped = _mcp_card_fields_its_store_drops(properties)
            if dropped:
                discarded.append(f"{name}: {dropped}")
            else:
                read.append(name)
            continue
        fn = _factory(manifest_path.parent, str(provider.get("implementation") or ""))
        if fn is None:
            unresolved.append(name)
        elif _reads_its_config(fn):
            read.append(name)
        else:
            discarded.append(f"{name}: {sorted(properties)}")
    return read, discarded, unresolved


def test_no_core_app_declares_a_setting_its_factory_discards():
    """The registry hands an app's saved provider settings to its factory
    (`providers/registry.py`: `factory(config)`), so a factory that never reads its argument
    turns every declared field into a control that saves and does nothing. One card saves
    somewhere else: MCP Tool Servers, whose routes write its fields into `mcp.json`
    (`providers/mcp_instances`). That store is its reader, and the rail measures it field by
    field."""
    read, discarded, unresolved = _audit(_NATIVE)
    assert (
        not unresolved
    ), f"factories this census could not find, so it measured nothing: {unresolved}"
    assert not discarded, (
        f"these core apps declare settings that never reach a reader: {discarded}. Wire the "
        "field to its reader or delete it — a saved value nothing reads is a promise the code "
        "ignores."
    )
    # VACUITY FLOOR: the providers whose settings ARE read must be seen as reading them, the
    # MCP card's fields included, each found in the entry it saved.
    assert {"bundled-chat", "ollama-models", "mcp-tools"} <= set(read), read


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


def test_the_family_rail_catches_an_mcp_card_field_its_store_drops(tmp_path):
    """The MCP card's positive control: its real fields plus a planted one the store never
    writes. The rail passes the real ones and names only the planted one, so following the card
    to `mcp.json` measures each field rather than passing the card."""
    real = json.loads((_NATIVE / "mcp-tools" / "app.json").read_text(encoding="utf-8"))
    schema = real["provider"]["settingsSchema"]
    planted = {**schema, "properties": {**schema["properties"], "cwd": {"type": "string"}}}
    app = tmp_path / "mcp-tools"
    app.mkdir()
    (app / "app.json").write_text(
        json.dumps({**real, "provider": {**real["provider"], "settingsSchema": planted}}),
        encoding="utf-8",
    )
    read, discarded, unresolved = _audit(tmp_path)
    assert discarded == ["mcp-tools: ['cwd']"] and not read and not unresolved
