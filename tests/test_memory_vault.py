"""mem-fs-mirror: the memory → markdown vault projection.

Covers the load-bearing behaviors: safe (fence-proof) frontmatter, wikilink
derivation from real record fields, idempotent + incremental sync, and pruning
of records that vanish. The vault is a pure projection — a full rebuild must
reproduce the same bytes.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.memory import MemoryStore
from personalclaw.memory_record import MemoryKind, MemoryRecord, MemoryScope
from personalclaw.memory_service import MemoryService
from personalclaw.memory_vault import (
    MemoryVault,
    render_record,
    _slug,
    _yaml_scalar,
)
from personalclaw.vector_memory import VectorMemoryStore


@pytest.fixture
def service(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    store = MemoryStore(workspace=ws)
    store.init()
    vs = VectorMemoryStore(db_path=tmp_path / "mem.db", embedding_dim=3)
    vs.init()
    vs.embed_fn = lambda t: [1.0, 0.0, 0.0]
    store.vector_store = vs
    return MemoryService.over_vector_store(vs)


@pytest.fixture
def vault(service, tmp_path):
    return MemoryVault(service, tmp_path / "vault")


# ── frontmatter is fence-proof (untrusted memory text) ───────────────────────

def test_yaml_scalar_escapes_fence_breakers():
    # A value that tries to break the fence / forge keys is JSON-escaped inline.
    evil = "line1\n---\nkind: injected\nmalice: true"
    out = _yaml_scalar(evil)
    assert "\n" not in out  # single-line — cannot introduce new YAML keys
    assert out.startswith('"') and out.endswith('"')
    # And it round-trips as the original string via JSON.
    assert json.loads(out) == evil


def test_scalar_types_roundtrip():
    assert _yaml_scalar(True) == "true"
    assert _yaml_scalar(5) == "5"
    assert _yaml_scalar(0.5) == "0.5"


def test_slug_is_stable_and_safe():
    assert _slug("pref.editor") == "pref.editor"
    a = _slug("weird/id:with spaces")
    b = _slug("weird/id:with spaces")
    assert a == b  # deterministic
    assert "/" not in a and ":" not in a and " " not in a


# ── rendering derives links from real fields ─────────────────────────────────

def test_render_episodic_links_session_and_tags():
    rec = MemoryRecord(
        id="ep1", kind=MemoryKind.EPISODIC, text="deployed the migration",
        conversation_id="sess-42", tags=["deploy", "infra"],
    )
    note = render_record(rec)
    assert note.relpath == "episodic/ep1.md"
    assert "[[session-sess-42]]" in note.content
    assert "[[tag-deploy]]" in note.content and "[[tag-infra]]" in note.content
    # links set carries the wikilink targets for graph wiring
    assert {"session-sess-42", "tag-deploy", "tag-infra"} <= note.links


def test_render_supersession_link():
    rec = MemoryRecord(id="fact.old", kind=MemoryKind.SEMANTIC, value="old",
                       superseded_by="fact.new")
    note = render_record(rec)
    assert "**Superseded by:** [[fact.new]]" in note.content


def test_render_frontmatter_has_axes():
    rec = MemoryRecord(id="pref.x", kind=MemoryKind.PREFERENCE, value="vim",
                       scope=MemoryScope.GLOBAL, confidence=0.9, category="pref")
    note = render_record(rec)
    assert note.content.startswith("---\n")
    assert 'id: "pref.x"' in note.content
    assert "kind: \"preference\"" in note.content
    assert "confidence: 0.9" in note.content


# ── sync is idempotent + incremental + prunes ────────────────────────────────

def test_sync_writes_then_noop(vault, service):
    service.set_semantic("pref.editor", "vim", 0.9, "user_explicit")
    service.write_episodic("shipped v2", conversation_id="s1", tags=["ship"])
    first = vault.sync()
    assert first["records"] >= 2
    assert first["written"] >= 3  # 2 records + index + tag hub
    # A second sync with no changes writes nothing.
    second = vault.sync()
    assert second["written"] == 0
    assert second["pruned"] == 0
    # Files actually exist on disk.
    assert (vault.path / "facts" / "pref.editor.md").is_file()
    assert (vault.path / "MEMORY.md").is_file()
    assert (vault.path / "tags" / f"{_slug('tag-ship')}.md").is_file()


def test_sync_prunes_deleted_record(vault, service):
    service.set_semantic("pref.tmp", "gone-soon", 0.9, "user_explicit")
    vault.sync()
    assert (vault.path / "facts" / "pref.tmp.md").is_file()
    # Delete the record; the next sync prunes its file.
    service.delete_semantic("pref.tmp", source="user_explicit")
    summary = vault.sync()
    assert summary["pruned"] >= 1
    assert not (vault.path / "facts" / "pref.tmp.md").is_file()


def test_full_rebuild_reproduces_bytes(vault, service, tmp_path):
    service.set_semantic("pref.editor", "vim", 0.9, "user_explicit")
    vault.sync()
    original = (vault.path / "facts" / "pref.editor.md").read_text(encoding="utf-8")
    # Blow away the manifest → full rebuild → identical content (pure projection).
    (vault.path / ".vault-manifest.json").unlink()
    vault.sync()
    rebuilt = (vault.path / "facts" / "pref.editor.md").read_text(encoding="utf-8")
    assert rebuilt == original


def test_disabled_config_yields_no_vault(monkeypatch, service):
    from personalclaw import memory_vault

    # vault_dir_from_config returns None when disabled → mirror is a no-op.
    monkeypatch.setattr(memory_vault, "vault_dir_from_config", lambda: None)
    assert memory_vault.vault_for(service) is None
    # And the guarded post-consolidation entry never raises.
    memory_vault.mirror_after_consolidation(service)
