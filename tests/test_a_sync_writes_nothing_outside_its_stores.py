"""A sync writes nothing outside the stores it syncs, and the sync report names what it refused.

🔴 The pull joined each pulled object's key onto its scratch folder as it came
(``pull_engine._materialize``), so a key with ``../`` in it wrote a file anywhere this machine's
user may. A peer's manifest named the files to read as its rows with no check either, so
``tasks/../../../x.jsonl`` read a file of this machine's as another machine's tasks and merged them
in, and an absolute path read one and then held the pull on it for good. The file a row stands for
was checked by its name alone, so a folder of a store that is a symlink carried another machine's
write out of the home; and a machine id in the registry named that machine's folder of the remote
as it was written.

Every path a peer names is now resolved before anything of its change is written
(``record_ids.is_path_in_store``), and one outside the export it came in, or the store it names, is
refused: nothing of that change is taken in, the cursor moves past it, and the sync report — the
job's detail and its audit — names the path.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from personalclaw.durability import inventory as inv
from personalclaw.durability import service
from personalclaw.durability.cursor import Cursor
from personalclaw.durability.registry import REGISTRY_KEY, shard_prefix
from personalclaw.durability.shards import canonical_json
from personalclaw.durability.sync_cycle import run_sync_cycle
from tests.test_durability_sync_cycle import SharedStore

PREFIX = shard_prefix("B", 1)

#: Why each was refused, as the report says it (``pull_engine``'s constants, spelled out here so a
#: test of the report is not a test of the constant against itself).
OUTSIDE_THE_EXPORT = "names a path outside the export it came in"
OUTSIDE_THE_STORE = "names a file outside its store"
NOT_ONE_NAME = "a machine id that is not one plain name, so it names another folder of the remote"
A_LINK = (
    "a symbolic link in this home, which nothing restored, imported or synced is written through"
)


def _task(home: Path, tid: str) -> None:
    (home / "tasks").mkdir(parents=True, exist_ok=True)
    (home / "tasks" / f"{tid}.json").write_text(json.dumps({"id": tid, "title": tid}))


@pytest.fixture
def scratch(tmp_path, monkeypatch) -> Path:
    """The pull's scratch folder made a known place, so a write that climbs out of it lands where
    the test can look: two levels up from ``scratch/<tmp>/`` is ``tmp_path`` itself."""
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _b_publishes(tmp_path: Path, prompts: dict[str, str] | None = None) -> SharedStore:
    """Machine B's first sync, published at seq 1: one task, and the prompt files named."""
    b = tmp_path / "B"
    _task(b, "from-b")
    for rel, text in (prompts or {}).items():
        (b / "prompts" / rel).parent.mkdir(parents=True, exist_ok=True)
        (b / "prompts" / rel).write_text(text, encoding="utf-8")
    store = SharedStore()
    assert run_sync_cycle(store, b, self_id="B", now="t").ok
    assert f"{PREFIX}manifest.json" in store.objects
    return store


def _a_pulls(store: SharedStore, tmp_path: Path):
    a = tmp_path / "A"
    a.mkdir(exist_ok=True)
    return a, run_sync_cycle(store, a, self_id="A", now="t2")


def _manifest(store: SharedStore) -> dict:
    return json.loads(store.objects[f"{PREFIX}manifest.json"])


def _set_manifest(store: SharedStore, manifest: dict) -> None:
    store.objects[f"{PREFIX}manifest.json"] = json.dumps(manifest, indent=2).encode()


def _declare(store: SharedStore, rel: str, data: bytes) -> None:
    """Declare *rel* in B's manifest as a shard holding *data*, as a manifest naming it would."""
    manifest = _manifest(store)
    manifest["shards"].append(
        {
            "path": rel,
            "bytes": len(data),
            "rows": len([ln for ln in data.decode().splitlines() if ln.strip()]),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )
    _set_manifest(store, manifest)


def _planted_rows() -> bytes:
    row = {"id": "planted", "data": {"id": "planted", "title": "a file of this machine's"}}
    return (canonical_json(row) + "\n").encode()


def _nothing_taken_in(a: Path) -> None:
    assert not (a / "tasks" / "planted.json").exists(), "a file of this machine's came in as rows"
    assert not (a / "tasks" / "from-b.json").exists(), "the rest of the change was taken in"


# ── the control: a clean change, nested files and all, is taken in ──────────────────────────


def test_the_control_a_clean_change_arrives_whole(tmp_path, scratch):
    store = _b_publishes(tmp_path, {"team/weekly.yaml": "name: weekly\n"})
    a, report = _a_pulls(store, tmp_path)
    assert report.ok
    assert (a / "tasks" / "from-b.json").exists()
    assert (a / "prompts" / "team" / "weekly.yaml").read_text() == "name: weekly\n"
    assert not getattr(report, "refused", {}) and "refused" not in report.detail


# ── an object's key ─────────────────────────────────────────────────────────────────────────


def test_a_key_that_climbs_out_of_the_export_writes_nothing(tmp_path, scratch):
    store = _b_publishes(tmp_path)
    store.objects[f"{PREFIX}../../x"] = b"planted by the remote"
    a, report = _a_pulls(store, tmp_path)
    planted = [p for p in tmp_path.rglob("x") if p.is_file()]
    assert planted == [], f"a pulled key wrote outside the pull's folder: {planted}"
    _nothing_taken_in(a)
    assert report.refused == {f"{PREFIX}../../x": OUTSIDE_THE_EXPORT}
    assert "refused 1 path(s)" in report.detail and f"{PREFIX}../../x" in report.detail
    assert Cursor(a / "sync").seq_of("B") == 1, "the change is moved past, never pulled again"


def test_an_absolute_key_is_refused(tmp_path, scratch):
    store = _b_publishes(tmp_path)
    key = f"{PREFIX}{tmp_path / 'abs-planted'}"
    assert key.startswith(f"{PREFIX}/"), "the part after the prefix is an absolute path"
    store.objects[key] = b"planted by the remote"
    a, report = _a_pulls(store, tmp_path)
    assert not (tmp_path / "abs-planted").exists()
    _nothing_taken_in(a)
    assert report.refused == {key: OUTSIDE_THE_EXPORT}


# ── a path the manifest declares ────────────────────────────────────────────────────────────


def test_a_manifest_path_that_climbs_out_reads_no_file_of_this_machine(tmp_path, scratch):
    # `scratch/<tmp>/tasks/../../../outside.jsonl` is `tmp_path/outside.jsonl`.
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(_planted_rows())
    store = _b_publishes(tmp_path)
    rel = "tasks/../../../outside.jsonl"
    _declare(store, rel, outside.read_bytes())
    a, report = _a_pulls(store, tmp_path)
    _nothing_taken_in(a)
    assert report.refused == {f"{PREFIX}{rel}": OUTSIDE_THE_EXPORT}
    assert Cursor(a / "sync").seq_of("B") == 1


def test_an_absolute_manifest_path_is_refused_not_held_for_good(tmp_path, scratch):
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(_planted_rows())
    store = _b_publishes(tmp_path)
    _declare(store, str(outside), outside.read_bytes())
    a, report = _a_pulls(store, tmp_path)
    assert Cursor(a / "sync").seq_of("B") == 1, "held on it, every later change waited for good"
    _nothing_taken_in(a)
    assert report.refused == {f"{PREFIX}{outside}": OUTSIDE_THE_EXPORT}


# ── the file a row stands for ───────────────────────────────────────────────────────────────


def test_a_folder_of_the_store_that_is_a_symlink_carries_no_write_out(tmp_path, scratch):
    """A link this home holds is never written through: the row behind it is left out, the link is
    named, and the change is held so the row comes in once the link is gone. The rest of the change
    comes in (``durability.home_paths``)."""
    assert inv.by_id("prompts").kind == inv.KIND_JSON_ENTITY_DIR
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    a = tmp_path / "A"
    (a / "prompts").mkdir(parents=True)
    (a / "prompts" / "shared").symlink_to(elsewhere, target_is_directory=True)
    store = _b_publishes(tmp_path, {"shared/evil.yaml": "name: evil\n"})
    a, report = _a_pulls(store, tmp_path)
    assert list(elsewhere.iterdir()) == [], "a pulled row was written through the symlink"
    assert (a / "tasks" / "from-b.json").exists(), "the rest of the change was not taken in"
    assert report.refused == {"prompts/shared": A_LINK}
    assert Cursor(a / "sync").seq_of("B") == 0, "moved past a row that never came in"


def test_a_row_that_names_a_path_out_of_its_store_is_refused(tmp_path, scratch):
    store = _b_publishes(tmp_path, {"weekly.yaml": "name: weekly\n"})
    manifest = _manifest(store)
    (record,) = [r for r in manifest["shards"] if r["path"].startswith("prompts/")]
    shard = store.objects[f"{PREFIX}{record['path']}"]
    shard += (canonical_json({"id": "../../../escaped.yaml", "text": "planted"}) + "\n").encode()
    store.objects[f"{PREFIX}{record['path']}"] = shard
    record.update(bytes=len(shard), sha256=hashlib.sha256(shard).hexdigest())
    record["rows"] += 1
    _set_manifest(store, manifest)
    a, report = _a_pulls(store, tmp_path)
    assert not (a / "prompts" / "weekly.yaml").exists(), "the rest of the change was taken in"
    _nothing_taken_in(a)
    assert report.refused == {"prompts/../../../escaped.yaml": OUTSIDE_THE_STORE}


# ── the machine id, which names the machine's folder of the remote ─────────────────────────


def test_a_machine_id_that_is_not_one_name_is_not_pulled_from(tmp_path, scratch):
    store = _b_publishes(tmp_path)
    # B's change, published again under an id that walks out of `machines/`: the only machine.
    registry = json.loads(store.objects[REGISTRY_KEY])
    registry["machines"] = {"../B": registry["machines"]["B"]}
    store.objects[REGISTRY_KEY] = json.dumps(registry).encode()
    staged = shard_prefix("../B", 1)
    for key in [k for k in store.objects if k.startswith(PREFIX)]:
        store.objects[staged + key[len(PREFIX) :]] = store.objects.pop(key)
    a, report = _a_pulls(store, tmp_path)
    _nothing_taken_in(a)
    assert report.refused == {"../B": NOT_ONE_NAME}


# ── the report: the job's detail, its audit, its outcome ────────────────────────────────────


class _Cfg:
    sync_enabled = True
    sync_transport = "shared"
    sync_encrypt = "off"
    sync_stale_after_secs = 900
    restore_drills = False
    time_travel = False


def test_the_sync_job_says_what_it_refused(tmp_path, scratch, monkeypatch):
    from personalclaw.sync_transports import registry

    store = _b_publishes(tmp_path)
    store.objects[f"{PREFIX}../../x"] = b"planted by the remote"
    a = tmp_path / "A"
    a.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(a))
    monkeypatch.setattr(service, "_cfg", lambda: _Cfg())
    audited: list[tuple[str, str, str]] = []

    def audit(event: str, resources: str, *, outcome: str = "allowed") -> None:
        audited.append((event, resources, outcome))

    monkeypatch.setattr(service, "_audit", audit)
    registry.register_transport(store)
    try:
        result = service.run_sync_job()
    finally:
        registry.unregister_transport("shared")
    assert not (tmp_path / "x").exists()
    assert not result.ok, "a refusal is the one thing in the report its owner must look at"
    assert result.extra["refused"] == {f"{PREFIX}../../x": OUTSIDE_THE_EXPORT}
    assert f"{PREFIX}../../x" in result.detail
    assert audited == [("durability_sync", result.detail, "denied")]
