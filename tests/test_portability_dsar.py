"""The §6 DSAR surface: per-domain export, `secret ∪ derived` exclusion, MANIFEST v3.

These tests exist to prove PROPERTIES, not to exercise code paths. Each one plants
real-shaped bytes and then asserts on the ARTIFACT — the zip's entry list and its
contents — rather than on whether some exclusion list happens to mention a name. A
test that reads `EXPORT_EXCLUDE` and finds `.env` in it proves nothing about what the
export actually wrote; the defect this suite was written against (`memory_index.db`,
declared `derived=True`, hardcoded into the export's database list) was invisible to
exactly that kind of test for four sessions.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw import portability as port
from personalclaw.portability import (
    MANIFEST_VERSION,
    apply_import_zip,
    create_export_zip,
    domain_of,
    export_domains,
    validate_import_zip,
)

# Distinctive byte markers. A substring search for these in the whole zip is the only
# honest way to ask "did this leak?" — a filename check misses a value copied into a
# database page or a nested tree file.
SECRET_ENV = "sk-ant-api03-DSARLEAKCANARY0001"
SECRET_LOCAL = "DSARLOCALSECRETCANARY0002"
SECRET_CRED = "DSARCREDSTORECANARY0003"
DERIVED_IDS = "DSARDERIVEDIDSCANARY0004"
DERIVED_FAISS = "DSARDERIVEDFAISSCANARY0005"
DERIVED_ROW = "DSARDERIVEDROWCANARY0006"
USER_DOC = "DSARUSERDOCUMENTCANARY0007"
MEMORY_ROW = "DSARMEMORYROWCANARY0008"
PLATFORM_NOTE = "DSARPLATFORMNOTECANARY0009"


def _db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS rows_t (v TEXT)")
        conn.execute("INSERT INTO rows_t (v) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def seeded_home(tmp_path, monkeypatch):
    """A home seeded ACROSS the inventory, with real-shaped secrets and derived caches.

    Every `secret=True` and `derived=True` store the inventory declares that this test
    can plant cheaply is planted, so an exclusion regression shows up as a canary in the
    zip rather than as a missing assertion.
    """
    home = tmp_path / "home"
    home.mkdir()

    # ── secret=True entries (must NEVER travel) ──
    (home / ".env").write_text(f"ANTHROPIC_API_KEY={SECRET_ENV}\n")
    (home / ".local_secret").write_text(SECRET_LOCAL)
    (home / "sel_hmac.key").write_bytes(b"hmac-key-bytes")
    (home / "telemetry_salt").write_text("salt")
    (home / "session_map.json").write_text('{"s":"map"}')
    (home / "credentials").mkdir()
    (home / "credentials" / "store.json").write_text(json.dumps({"key": SECRET_CRED}))

    # ── derived=True entries (rebuildable; must NEVER travel) ──
    (home / "memory.ids.json").write_text(json.dumps([DERIVED_IDS]))
    (home / "memory.faiss").mkdir()
    (home / "memory.faiss" / "index.bin").write_text(DERIVED_FAISS)
    _db(home / "memory_index.db", DERIVED_ROW)
    _db(home / "session_search.db", DERIVED_ROW)
    (home / "models").mkdir()
    (home / "models" / "weights.bin").write_text(DERIVED_ROW)

    # ── real state, one entry per domain ──
    _db(home / "memory.db", MEMORY_ROW)  # memory
    (home / "workspace" / "knowledge" / "files").mkdir(parents=True)
    (home / "workspace" / "knowledge" / "files" / "resume.txt").write_text(USER_DOC)  # knowledge
    (home / "workspace" / "memory").mkdir(parents=True)
    (home / "workspace" / "memory" / "notes.md").write_text(PLATFORM_NOTE)  # platform
    (home / "tasks").mkdir()
    (home / "tasks" / "t1.json").write_text(json.dumps({"id": "t1", "title": "a task"}))  # work
    (home / "triggers.json").write_text(json.dumps({"triggers": []}))  # automation
    (home / "config.json").write_text(json.dumps({"theme": "dark"}))  # config

    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    with patch("personalclaw.portability.config_dir", return_value=home):
        yield home


def _zip_of(blob: bytes) -> tuple[list[str], bytes]:
    """(entry names relative to the archive prefix, every byte the archive contains)."""
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = sorted(n.split("/", 1)[1] for n in zf.namelist() if "/" in n)
    body = b"".join(zf.read(n) for n in zf.namelist())
    return names, body


# ── the exclusion property ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "canary,label",
    [
        (SECRET_ENV, ".env API key"),
        (SECRET_LOCAL, ".local_secret"),
        (SECRET_CRED, "credential store value"),
        (DERIVED_IDS, "memory.ids.json (derived)"),
        (DERIVED_FAISS, "memory.faiss payload (derived)"),
        (DERIVED_ROW, "derived database row"),
    ],
)
def test_export_leaks_no_secret_or_derived_bytes(seeded_home, canary, label):
    """No `secret ∪ derived` byte appears ANYWHERE in a full export.

    Asserted on the archive's bytes, not on its filenames: `memory_index.db` leaked as a
    whole database, so its rows were in the zip even though no obviously-secret *name*
    was. Reading the exclusion list instead would have passed.
    """
    blob, _ = create_export_zip()
    names, body = _zip_of(blob)
    assert canary.encode() not in body, f"{label} leaked into the export ({names})"


def test_export_omits_the_derived_database_entry(seeded_home):
    """`memory_index.db` is not an entry either — the regression this closed.

    Kept separate from the byte assertion above so a future change that empties the
    database but still ships the file is caught: an empty derived index restored beside a
    newer store is the same hazard as a stale one.
    """
    names, _ = _zip_of(create_export_zip()[0])
    assert "memory_index.db" not in names
    assert "session_search.db" not in names
    # …while the real store it is derived FROM does travel.
    assert "memory.db" in names


def test_import_refuses_a_hand_built_archive_carrying_a_credential(seeded_home, tmp_path):
    """A merge import never writes a `secret ∪ derived` path, even when the zip has one.

    Our own exports cannot produce this archive; a hand-built or tampered one can, and
    the plan's amendment requires the import side to refuse it independently. Built by
    hand for exactly that reason — deriving the fixture from `create_export_zip` would
    only re-test the export.
    """
    target = tmp_path / "fresh"
    target.mkdir()
    hostile = tmp_path / "hostile.zip"
    with zipfile.ZipFile(hostile, "w") as zf:
        zf.writestr("personalclaw-export-hand/config.json", json.dumps({"theme": "light"}))
        zf.writestr("personalclaw-export-hand/.env", f"ANTHROPIC_API_KEY={SECRET_ENV}\n")
        zf.writestr("personalclaw-export-hand/credentials/store.json", SECRET_CRED)
        zf.writestr("personalclaw-export-hand/memory.ids.json", DERIVED_IDS)
        zf.writestr("personalclaw-export-hand/MANIFEST.json", json.dumps({"version": 2}))

    with patch("personalclaw.portability.config_dir", return_value=target):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
            summary = apply_import_zip(hostile, "merge")

    assert not (target / ".env").exists()
    assert not (target / "credentials" / "store.json").exists()
    assert not (target / "memory.ids.json").exists()
    assert (target / "config.json").exists(), "the legitimate file should still arrive"
    assert ".env" in summary.get("refused", []), summary


# ── per-domain export (criterion 9's export half) ────────────────────────────


def test_knowledge_export_carries_the_user_documents_and_nothing_else(seeded_home):
    """A knowledge export contains the `files/` originals — §6's "whole point".

    The trap this pins: `workspace/knowledge/files` (knowledge) is nested inside the
    `workspace` tree entry (platform). An ancestor-wins domain rule produced an EMPTY
    knowledge export while filing every user document under platform.
    """
    names, body = _zip_of(create_export_zip(["knowledge"])[0])
    assert "workspace/knowledge/files/resume.txt" in names
    assert USER_DOC.encode() in body
    assert "memory.db" not in names
    assert "config.json" not in names
    assert PLATFORM_NOTE.encode() not in body


def test_platform_export_does_not_smuggle_the_user_documents(seeded_home):
    """The boundary holds in the other direction too — the half a one-sided test misses."""
    names, body = _zip_of(create_export_zip(["platform"])[0])
    assert "workspace/memory/notes.md" in names
    assert PLATFORM_NOTE.encode() in body
    assert "workspace/knowledge/files/resume.txt" not in names
    assert USER_DOC.encode() not in body


def test_memory_and_knowledge_are_separately_exportable(seeded_home):
    """Criterion 9: memory and knowledge come out as separate archives, not one blob."""
    mem_names, mem_body = _zip_of(create_export_zip(["memory"])[0])
    know_names, know_body = _zip_of(create_export_zip(["knowledge"])[0])
    assert "memory.db" in mem_names and USER_DOC.encode() not in mem_body
    assert "workspace/knowledge/files/resume.txt" in know_names
    assert MEMORY_ROW.encode() not in know_body


def test_multiple_domains_union(seeded_home):
    names, _ = _zip_of(create_export_zip(["memory", "config"])[0])
    assert {"memory.db", "config.json"} <= set(names)
    assert "tasks/t1.json" not in names


def test_unknown_domain_is_refused_loudly(seeded_home):
    """A typo must not silently produce an empty archive — the worst DSAR failure."""
    with pytest.raises(ValueError) as exc:
        create_export_zip(["knowlege"])
    assert "knowlege" in str(exc.value)
    assert "knowledge" in str(exc.value), "the error should name the valid set"


def test_empty_domain_list_is_refused(seeded_home):
    with pytest.raises(ValueError):
        create_export_zip([])


def test_export_domains_are_all_reachable(seeded_home):
    """Every domain `export_domains()` offers must be acceptable to the exporter.

    A vacuity floor: without it, `export_domains()` could drift into advertising a
    domain the export rejects, and every per-domain test above would still pass.
    """
    assert export_domains(), "there must be at least one exportable domain"
    for domain in export_domains():
        _, manifest = create_export_zip([domain])
        assert manifest["domains"] == [domain]


def test_domain_of_uses_the_longest_declared_match():
    assert domain_of("workspace/knowledge/files/a.pdf") == "knowledge"
    assert domain_of("workspace/memory/a.md") == "platform"
    assert domain_of("memory.db") == "memory"


# ── MANIFEST v3 + v1|v2 back-compat ─────────────────────────────────────────


def test_v3_manifest_carries_the_integrity_shape(seeded_home):
    """§2's manifest fields, inside the export: schema version, machine id, per-member sha."""
    _, manifest = create_export_zip()
    assert manifest["version"] == MANIFEST_VERSION == 3
    assert manifest["scope"] == "full"
    assert isinstance(manifest["machine_id"], str) and manifest["machine_id"]
    assert isinstance(manifest["schema_version"], int)
    assert manifest["members"], "v3 must declare its members"
    for member in manifest["members"]:
        assert len(member["sha256"]) == 64
        assert member["bytes"] >= 0
    # The excluded set is named IN the artifact, so the exclusion is auditable offline.
    assert "memory_index.db" in manifest["excluded"]
    assert ".env" in manifest["excluded"]


def test_v3_manifest_reports_per_domain_counts(seeded_home):
    _, manifest = create_export_zip()
    counts = manifest["domain_counts"]
    assert counts["knowledge"]["files"] == 1
    assert counts["memory"]["files"] == 1
    assert counts["work"]["files"] == 1


def test_v3_archive_validates_and_reports_verified(seeded_home, tmp_path):
    blob, _ = create_export_zip()
    path = tmp_path / "export.zip"
    path.write_bytes(blob)
    ok, err, manifest = validate_import_zip(path)
    assert ok and err == ""
    assert manifest["verified"] is True


def test_v3_corruption_is_detected_and_names_the_member(seeded_home, tmp_path):
    """A single flipped byte fails validation BEFORE anything is written.

    This is what the §2 manifest buys: with v1/v2 the same truncation imported as far as
    it went.
    """
    blob, _ = create_export_zip()
    src = zipfile.ZipFile(io.BytesIO(blob))
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(corrupt, "w") as out:
        for name in src.namelist():
            data = src.read(name)
            if name.endswith("config.json"):
                data = data.replace(b"dark", b"DARK")
            out.writestr(name, data)
    ok, err, _ = validate_import_zip(corrupt)
    assert not ok
    assert "config.json" in err, err


def test_v3_undeclared_member_is_refused(seeded_home, tmp_path):
    """A manifest that does not describe the whole archive cannot vouch for it."""
    blob, _ = create_export_zip()
    src = zipfile.ZipFile(io.BytesIO(blob))
    tampered = tmp_path / "extra.zip"
    with zipfile.ZipFile(tampered, "w") as out:
        for name in src.namelist():
            out.writestr(name, src.read(name))
        prefix = src.namelist()[0].split("/", 1)[0]
        out.writestr(f"{prefix}/smuggled.json", '{"injected": true}')
    ok, err, _ = validate_import_zip(tampered)
    assert not ok
    assert "smuggled.json" in err, err


def _v1_fixture(path: Path) -> None:
    """A genuine v1-shaped archive: the flat file set and the v1 manifest, no hashes.

    Written by hand rather than by editing a v3 manifest's version field — a v3 zip with
    `"version": 1` still carries `members`, so "back-compat" would be tested against an
    archive no v1 exporter could ever have produced.
    """
    with zipfile.ZipFile(path, "w") as zf:
        root = "personalclaw-export-20240101T000000Z"
        zf.writestr(f"{root}/config.json", json.dumps({"theme": "v1-era"}))
        zf.writestr(f"{root}/crons.json", json.dumps({"jobs": []}))
        zf.writestr(f"{root}/hooks.json", json.dumps({}))
        zf.writestr(
            f"{root}/MANIFEST.json",
            json.dumps(
                {
                    "version": 1,
                    "format": "zip",
                    "created_at": "2024-01-01T00:00:00Z",
                    "hostname": "old-laptop",
                    "user": "someone",
                    "contents": {"config.json": 24},
                }
            ),
        )


def test_v1_archive_still_validates_and_imports(tmp_path):
    """A v1 archive a user has on disk keeps working. Back-compat, driven end to end."""
    archive = tmp_path / "v1.zip"
    _v1_fixture(archive)
    ok, err, manifest = validate_import_zip(archive)
    assert ok, err
    assert manifest["version"] == 1
    # There are no hashes in a v1 archive, so it must NOT claim to have been verified.
    assert manifest["verified"] is False

    target = tmp_path / "target"
    target.mkdir()
    with patch("personalclaw.portability.config_dir", return_value=target):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
            apply_import_zip(archive, "merge")
    assert json.loads((target / "config.json").read_text())["theme"] == "v1-era"


def test_v2_archive_still_validates(tmp_path):
    archive = tmp_path / "v2.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        root = "personalclaw-export-20250101T000000Z"
        zf.writestr(f"{root}/config.json", "{}")
        zf.writestr(f"{root}/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
    ok, err, manifest = validate_import_zip(archive)
    assert ok, err
    assert manifest["verified"] is False


def test_an_unknown_future_version_is_refused(tmp_path):
    archive = tmp_path / "v9.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("x/MANIFEST.json", json.dumps({"version": 9}))
    ok, err, _ = validate_import_zip(archive)
    assert not ok
    assert "9" in err


# ── the round trip, and the unhappy path ─────────────────────────────────────


def test_round_trip_into_a_fresh_home_restores_every_claimed_domain(seeded_home, tmp_path):
    """Export → fresh home → import → the claimed domains are EQUAL.

    The property that makes an export worth having. A test that only checked the zip was
    well-formed would pass while restore silently dropped every store — which is exactly
    what happened before the import side became inventory-driven.
    """
    blob, manifest = create_export_zip()
    archive = tmp_path / "full.zip"
    archive.write_bytes(blob)

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    with patch("personalclaw.portability.config_dir", return_value=fresh):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(fresh)}):
            ok, err, _ = validate_import_zip(archive)
            assert ok, err
            apply_import_zip(archive, "merge")

    # knowledge: the user's document, byte-identical.
    assert (fresh / "workspace" / "knowledge" / "files" / "resume.txt").read_text() == USER_DOC
    # work + automation + config + platform.
    assert json.loads((fresh / "tasks" / "t1.json").read_text())["id"] == "t1"
    assert json.loads((fresh / "triggers.json").read_text()) == {"triggers": []}
    assert json.loads((fresh / "config.json").read_text())["theme"] == "dark"
    assert (fresh / "workspace" / "memory" / "notes.md").read_text() == PLATFORM_NOTE
    # memory: the row survives the backup-API copy.
    conn = sqlite3.connect(str(fresh / "memory.db"))
    try:
        assert conn.execute("SELECT v FROM rows_t").fetchone()[0] == MEMORY_ROW
    finally:
        conn.close()
    # …and the derived index did NOT come back.
    assert not (fresh / "memory_index.db").exists()
    assert manifest["scope"] == "full"


def test_a_domain_round_trip_restores_only_that_domain(seeded_home, tmp_path):
    blob, _ = create_export_zip(["knowledge"])
    archive = tmp_path / "know.zip"
    archive.write_bytes(blob)
    fresh = tmp_path / "fresh-know"
    fresh.mkdir()
    with patch("personalclaw.portability.config_dir", return_value=fresh):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(fresh)}):
            apply_import_zip(archive, "merge")
    assert (fresh / "workspace" / "knowledge" / "files" / "resume.txt").read_text() == USER_DOC
    assert not (fresh / "config.json").exists()
    assert not (fresh / "memory.db").exists()


def test_merge_never_overwrites_what_the_home_already_has(seeded_home, tmp_path):
    """Merge is copy-if-missing, so a partial merge is a SUBSET of a complete one.

    This is the whole reason merge has no hybrid failure state: re-running it is safe.
    """
    blob, _ = create_export_zip()
    archive = tmp_path / "full.zip"
    archive.write_bytes(blob)

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "config.json").write_text(json.dumps({"theme": "MINE"}))
    with patch("personalclaw.portability.config_dir", return_value=target):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
            apply_import_zip(archive, "merge")
            # Idempotent: a second run changes nothing either.
            apply_import_zip(archive, "merge")
    assert json.loads((target / "config.json").read_text())["theme"] == "MINE"
    assert (target / "tasks" / "t1.json").exists(), "absent stores should still arrive"


def test_a_failed_replace_leaves_the_displaced_state_recoverable(seeded_home, tmp_path):
    """The unhappy path: a replace that dies mid-way is RECOVERABLE, not a hybrid.

    `_do_replace` moves each live path into `pre-restore-<ts>/` before writing the
    incoming one, so the failure mode is "some paths swapped, the originals all present
    under one directory" — never "originals gone, replacements missing". Driven by making
    the replace raise part-way rather than by reasoning about the ordering.
    """
    blob, _ = create_export_zip()
    archive = tmp_path / "full.zip"
    archive.write_bytes(blob)

    target = tmp_path / "victim"
    target.mkdir()
    (target / "config.json").write_text(json.dumps({"theme": "PRECIOUS"}))
    (target / "memory.db").write_bytes(b"precious-db-bytes")

    real_replace = port._do_replace
    calls: list[int] = []

    def exploding_replace(snap, pc, components):
        """Run the real replace, then fail — the state on disk is genuinely part-done."""
        calls.append(1)
        real_replace(snap, pc, components)
        raise OSError("disk full part-way through the replace")

    with patch("personalclaw.portability.config_dir", return_value=target):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
            with patch.object(port, "_do_replace", exploding_replace):
                with pytest.raises(OSError):
                    apply_import_zip(archive, "replace")

    assert calls, "the replace must actually have been attempted"
    backups = [p for p in target.glob("pre-restore-*") if p.is_dir()]
    assert backups, "a failed replace must leave the displaced originals on disk"
    recovered = backups[0] / "config.json"
    assert recovered.is_file(), sorted(p.name for p in backups[0].rglob("*"))
    assert json.loads(recovered.read_text())["theme"] == "PRECIOUS"


def test_replace_reports_where_the_displaced_state_went(seeded_home, tmp_path):
    """A successful replace still names its escape hatch when it displaced anything."""
    blob, _ = create_export_zip()
    archive = tmp_path / "full.zip"
    archive.write_bytes(blob)
    target = tmp_path / "replaced"
    target.mkdir()
    (target / "config.json").write_text(json.dumps({"theme": "old"}))
    with patch("personalclaw.portability.config_dir", return_value=target):
        with patch.dict(os.environ, {"PERSONALCLAW_HOME": str(target)}):
            summary = apply_import_zip(archive, "replace")
    assert summary["pre_restore"].startswith("pre-restore-")
    assert json.loads((target / "config.json").read_text())["theme"] == "dark"


def test_traversal_is_still_refused(tmp_path):
    """The traversal guard predates this atom; pinned so widening never loses it."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("x/MANIFEST.json", json.dumps({"version": 2, "contents": {}}))
        zf.writestr("../../escaped.txt", "nope")
    ok, err, _ = validate_import_zip(archive)
    assert not ok
    assert "traversal" in err.lower()


def test_the_real_home_is_never_touched(seeded_home, tmp_path):
    """A guard on the SUITE, not the feature: an export must read only the patched home.

    `portability._pc_dir()` reads `PERSONALCLAW_HOME` first, so a test that patched only
    `config_dir` would silently walk the developer's real home. Asserting the resolved
    directory is the cheap way to keep that impossible.
    """
    assert port._pc_dir() == seeded_home
    assert str(Path.home() / ".personalclaw") not in str(port._pc_dir())
    create_export_zip()
    assert port._pc_dir() == seeded_home
