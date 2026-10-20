"""A backup and a sync carry every file a store holds, and name any file they cannot carry.

🔴 A folder store was exported by reading its ``*.json`` files and nothing else, so a backup's
export and a sync carried none of its other files, and said nothing: saved prompts are YAML, an
agent folder's prompt assets are Markdown, a voice profile's reference clip is audio, and prompt
snippets, a folder of YAML declared as a tree, were not read at all. A saved prompt existed on one
machine only, and the export that reads as a backup did not hold it. A one-file store that is not
JSON — the workspace pointer is a bare path — was dropped the same way.

Every file of a folder store is now a row: a JSON file as its data, any other as its text or, when
it is not text, its bytes (``shards.read_entity_dir``). A one-file store restored whole is carried
whatever it holds (``shards.read_json_file``). A file the export cannot carry — JSON that does not
parse, one too large for a row — is named with why, in the export's result, the backup job's report
and the sync's report.

The rail: every folder store and every one-file store the inventory declares, given a file of each
shape, is exported whole, and a sync brings each file to the other machine as it was. A store the
exporter cannot read fails here, by name.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.durability import reconcile, shards
from personalclaw.durability.cursor import CONSUMED
from personalclaw.durability.shards import export_shards, import_shards
from tests.test_durability_sync_cycle import SharedStore

FOLDER_STORES = sorted(e.id for e in inv.export_entries() if e.kind == inv.KIND_JSON_ENTITY_DIR)
FILE_STORES = sorted(e.id for e in inv.export_entries() if e.kind == inv.KIND_JSON_FILE)

#: A file of each shape a folder store holds, by its path in the folder.
SHAPES: dict[str, bytes] = {
    "planted.json": json.dumps({"id": "planted", "n": 1}).encode("utf-8"),
    "planted.yaml": b"name: planted\nkind: user\ncontent: |\n  Review {{language}} code.\n",
    "notes/planted.md": "# Planted\n\nCafé, naïve — text.\n".encode("utf-8"),
    "planted.wav": b"RIFF\x00\x00\xff\xfe audio \x80\x81",
}


def _plant(home: Path, entry: inv.StateEntry) -> None:
    for rel, raw in SHAPES.items():
        path = home / entry.path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)


def _file_of(row: dict) -> str:
    """The file a shard row stands for, by its path in the store's folder: a JSON file's row is
    named without ``.json`` and holds its ``data``; any other file's holds ``text`` or ``base64``.
    """
    rid = str(row.get("id", ""))
    return rid if ("text" in row or "base64" in row) else f"{rid}.json"


def _bytes_of(row: dict) -> bytes:
    if "text" in row:
        return str(row["text"]).encode("utf-8")
    if "base64" in row:
        return base64.b64decode(str(row["base64"]))
    return json.dumps(row.get("data")).encode("utf-8")


def _files_of(rows: list[dict]) -> set[str]:
    return {_file_of(r) for r in rows if not r.get("deleted_at")}


def test_the_rail_covers_the_stores_it_names():
    assert "prompts" in FOLDER_STORES and "prompt_snippets" in FOLDER_STORES
    assert len(FOLDER_STORES) >= 10 and len(FILE_STORES) >= 20


def test_every_store_is_of_a_kind_the_exporter_reads():
    assert {e.kind for e in inv.INVENTORY} == {
        inv.KIND_SQLITE,
        inv.KIND_JSON_ENTITY_DIR,
        inv.KIND_JSON_FILE,
        inv.KIND_JSONL_APPEND,
        inv.KIND_TREE,
    }


@pytest.mark.parametrize("entry_id", FOLDER_STORES)
def test_a_backup_carries_every_file_of_a_folder_store(tmp_path, entry_id):
    entry = inv.by_id(entry_id)
    home = tmp_path / "home"
    _plant(home, entry)
    result = export_shards(home, tmp_path / "backup")
    rows = import_shards(tmp_path / "backup").rows.get(entry.id, [])
    missing = sorted(set(SHAPES) - _files_of(rows))
    assert not missing, f"a backup of {entry.path} left out {missing}"
    assert result.left_out == {}


@pytest.mark.parametrize("entry_id", FOLDER_STORES)
def test_a_sync_brings_every_file_of_a_folder_store_to_the_other_machine(tmp_path, entry_id):
    entry = inv.by_id(entry_id)
    a, b = tmp_path / "A", tmp_path / "B"
    _plant(a, entry)
    export_shards(a, tmp_path / "sync", for_sync=True)
    rows = import_shards(tmp_path / "sync").rows.get(entry.id, [])
    result = reconcile.reconcile_entry(b, entry, rows)
    assert result.verdict == CONSUMED, result.detail
    for rel, raw in SHAPES.items():
        there = b / entry.path / rel
        assert there.is_file(), f"{entry.path}/{rel} did not reach the other machine"
        if rel.endswith(".json"):
            assert json.loads(there.read_bytes()) == json.loads(raw)
        else:
            assert there.read_bytes() == raw, f"{entry.path}/{rel} arrived changed"


@pytest.mark.parametrize("entry_id", FILE_STORES)
def test_a_one_file_store_is_carried_or_named(tmp_path, entry_id):
    entry = inv.by_id(entry_id)
    home = tmp_path / "home"
    path = home / entry.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"/Users/someone/work\n")
    result = export_shards(home, tmp_path / "backup")
    rows = import_shards(tmp_path / "backup").rows.get(entry.id, [])
    if entry.merge == inv.MERGE_REPLACE_ONLY:
        # Restored whole and never merged, so it is carried whatever it holds.
        assert [_bytes_of(r) for r in rows] == [b"/Users/someone/work\n"]
        assert result.left_out == {}
    else:
        # A sync would take it in as an edit over the other machine's store: named instead.
        assert rows == []
        assert result.left_out == {entry.path: "not valid JSON"}


# ── the stores' own writers ───────────────────────────────────────────────────


def _as(monkeypatch, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))


def test_a_saved_prompt_and_snippet_reach_the_other_machine(tmp_path, monkeypatch):
    from personalclaw.durability.sync_cycle import run_sync_cycle
    from personalclaw.prompt_providers.base import PromptSnippet, PromptTemplate
    from personalclaw.prompt_providers.native_provider import NativePromptProvider

    store = SharedStore()
    a, b = tmp_path / "A", tmp_path / "B"
    _as(monkeypatch, a)
    NativePromptProvider().create_prompt(
        PromptTemplate(name="code-review", content="Review the {{language}} diff.")
    )
    NativePromptProvider().create_snippet(
        PromptSnippet(name="house-style", content="Answer in short sentences.")
    )
    assert run_sync_cycle(store, a, self_id="A", now="t1").ok
    _as(monkeypatch, b)
    report = run_sync_cycle(store, b, self_id="B", now="t2")
    assert report.ok, report.error
    prompt = NativePromptProvider().get_prompt("code-review")
    snippet = NativePromptProvider().get_snippet("house-style")
    assert prompt is not None and prompt.content == "Review the {{language}} diff."
    assert snippet is not None and snippet.content == "Answer in short sentences."
    assert (b / "prompts" / "code-review.yaml").read_bytes() == (
        a / "prompts" / "code-review.yaml"
    ).read_bytes()


def test_a_saved_prompt_is_in_the_backup_export(tmp_path, monkeypatch):
    from personalclaw.durability import service
    from personalclaw.prompt_providers.base import PromptTemplate
    from personalclaw.prompt_providers.native_provider import NativePromptProvider

    home = tmp_path / "home"
    _as(monkeypatch, home)
    NativePromptProvider().create_prompt(PromptTemplate(name="standup", content="What moved?"))
    result = service.run_incremental_export()
    assert result.ok, result.detail
    rows = import_shards(shards.default_shard_dir(home)).rows.get("prompts", [])
    assert "standup.yaml" in _files_of(rows)


# ── what cannot be carried is said ────────────────────────────────────────────


def test_a_file_the_export_cannot_carry_is_named_with_why(tmp_path, monkeypatch):
    from personalclaw.durability import service

    home = tmp_path / "home"
    _as(monkeypatch, home)
    (home / "tasks").mkdir(parents=True)
    (home / "tasks" / "broken.json").write_text('{"id": "broken", ', encoding="utf-8")
    (home / "tasks" / "ok.json").write_text('{"id": "ok"}', encoding="utf-8")
    monkeypatch.setattr(shards, "LARGEST_FILE_BYTES", 64)
    (home / "prompts").mkdir()
    (home / "prompts" / "huge.yaml").write_bytes(b"x" * 65)

    result = export_shards(home, tmp_path / "out")
    assert result.left_out == {
        "tasks/broken.json": "not valid JSON",
        "prompts/huge.yaml": "larger than 64 bytes",
    }
    rows = import_shards(tmp_path / "out").rows
    assert _files_of(rows["tasks"]) == {"ok.json"}, "the rest of the store is still carried"

    job = service.run_incremental_export()
    assert not job.ok
    assert "2 files could not be exported" in job.detail
    assert "tasks/broken.json (not valid JSON)" in job.detail


def test_the_sync_report_names_a_file_it_could_not_carry(tmp_path):
    from personalclaw.durability.sync_cycle import run_sync_cycle

    home = tmp_path / "home"
    (home / "tasks").mkdir(parents=True)
    (home / "tasks" / "broken.json").write_text("{", encoding="utf-8")
    report = run_sync_cycle(SharedStore(), home, self_id="A", now="t1")
    assert report.ok, report.error
    assert report.left_out == {"tasks/broken.json": "not valid JSON"}
    assert "1 file could not be synced: tasks/broken.json (not valid JSON)" in report.detail
