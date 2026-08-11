"""Project archive I/O — the round trip that proves the export planner exports something (S54, C9).

`project_export` was a complete planning layer with ZERO importers: the exclusion policy, the
digests and the path-safety predicate were all written and unused. So the tests that mattered here
are the ones a plan-only test cannot answer — does a ZIP get written, does it extract on a CLEAN
home, do the entities survive byte-identical, and are the secrets genuinely absent from the BYTES
rather than merely absent from the plan.

The zero-secrets assertions read `secret_basenames()`/`excluded()` rather than restating a list.
A second hand-maintained list of credential names is the drift that let stores escape coverage
before; a test carrying its own copy would pass while the policy it claims to check moved.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from personalclaw.workflows import project_archive as pa
from personalclaw.workflows.project_export import (
    MANIFEST_SCHEMA,
    excluded,
    secret_basenames,
    sha256_bytes,
)

# ── fixtures: a project on disk, with every portable shape and a planted credential ──

BRIEF = '{"id": "p-round", "name": "Round Trip", "brief": "ship the archive"}'
OVERVIEW = "# Overview\n\nCurrent state: the archive round-trips.\n"
DECISIONS = "- 2026-08-08 chose a manifest ZIP over a tarball\n"
FOG = "- how big is too big for a context file?\n"
OUT_OF_SCOPE = "- migrating pre-1.0 homes (reason: clean break under the banner)\n"
TEMPLATE = '{"name": "nightly", "nodes": []}'


def _seed_project(root: Path) -> None:
    """A project directory holding every portable shape, a worktree, and a real credential name."""
    (root / "context").mkdir(parents=True, exist_ok=True)
    (root / "templates").mkdir(parents=True, exist_ok=True)
    (root / "project.json").write_text(BRIEF, encoding="utf-8")
    (root / "context" / "overview.md").write_text(OVERVIEW, encoding="utf-8")
    (root / "context" / "decisions.md").write_text(DECISIONS, encoding="utf-8")
    (root / "context" / "not-yet-specified.md").write_text(FOG, encoding="utf-8")
    (root / "context" / "out-of-scope.md").write_text(OUT_OF_SCOPE, encoding="utf-8")
    (root / "templates" / "nightly.json").write_text(TEMPLATE, encoding="utf-8")

    # A credential, by each name the policy knows. Planted INSIDE a portable dir on purpose: the
    # allowlist says `context/` travels, so the only thing standing between a `.env` in there and
    # the archive is the exclusion policy itself.
    for name in sorted(secret_basenames()):
        target = root / "context" / name
        if target.suffix or "/" not in name:
            target.write_text("SUPER-SECRET-VALUE-8f3a", encoding="utf-8")

    # A git worktree: gigabytes in the real world, `derived_within` in the inventory.
    (root / "worktrees" / "repo").mkdir(parents=True, exist_ok=True)
    (root / "worktrees" / "repo" / "huge.md").write_text("x" * 4096, encoding="utf-8")


ARTIFACTS = [
    {
        "slug": "sales-dash",
        "name": "Sales Dashboard",
        "kind": "widget",
        "version": 3,
        "updated_at": "2026-08-01T00:00:00Z",
        "content": "<html>a 900KB body that must NOT travel</html>",
        "meta": {"lineage_run_id": "r-42", "lineage_node": "render"},
    }
]

RUNS = [
    {
        "id": "r-42",
        "workflow_name": "nightly",
        "status": "succeeded",
        "created_at": "2026-08-01T00:00:00Z",
        "completed_at": "2026-08-01T00:05:00Z",
        "total_tokens": 1234,
        "journal": [{"prompt": "a resolved prompt with SUPER-SECRET-VALUE-8f3a in it"}],
    }
]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "home-a" / "projects" / "p-round"
    _seed_project(root)
    return root


# ── clause 1: the ZIP writer exists and the planner has an importer ──


def test_the_export_PLANNER_now_has_an_importer():
    """🔴 The atom's centre. Every symbol below existed and nothing in `src/` called any of them, so
    the exclusion policy, the digests and the path-safety predicate were written and unused."""
    import inspect

    src = inspect.getsource(pa)
    for symbol in ("plan_export", "plan_import", "safe_member", "artifact_digest", "run_digest"):
        assert symbol in src, f"{symbol} is still unreachable from the archive layer"


def test_an_export_writes_a_REAL_zip(project: Path):
    raw, plan = pa.export_project_archive(
        "p-round", project_root=project, project_name="Round Trip"
    )
    assert raw[:2] == b"PK", "not a zip"
    assert plan.entries, "the plan carried no entities"
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = set(zf.namelist())
    assert pa.MANIFEST_NAME in names
    assert f"{pa.PAYLOAD_PREFIX}project.json" in names


def test_the_archive_contents_and_the_manifest_AGREE(project: Path):
    """An archive whose members and manifest disagree is worse than either: the importer trusts the
    manifest and a secrets grep reads the bytes, so a mismatch makes one of the two lie."""
    raw, plan = pa.export_project_archive("p-round", project_root=project)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        members = {n[len(pa.PAYLOAD_PREFIX) :] for n in zf.namelist() if n != pa.MANIFEST_NAME}
        manifest = json.loads(zf.read(pa.MANIFEST_NAME))
    assert members == {e["path"] for e in manifest["entries"]}


# ── clause 4: the round trip on a CLEAN home ──


def test_ROUND_TRIP_on_a_clean_home_keeps_every_entity_sha256_verified(
    tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch
):
    """Export from home A, import into a home B that has never seen this project.

    The clean home is the whole point: an import that only works where the project already exists
    proves nothing about portability, because the files it 'restored' were already there.
    """
    raw, plan = pa.export_project_archive(
        "p-round",
        project_root=project,
        project_name="Round Trip",
        artifacts=ARTIFACTS,
        runs=RUNS,
    )

    home_b = tmp_path / "home-b"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home_b))
    archive = tmp_path / "archive.zip"
    archive.write_bytes(raw)

    import_plan, extracted = pa.read_archive_plan(archive, existing_names=[])
    assert (
        import_plan.ok
    ), f"nothing imported; refusals={[r.to_dict() for r in import_plan.refused]}"
    assert import_plan.refused == [], "a clean archive must refuse nothing"

    dest = home_b / "projects" / "p-imported"
    written = pa.commit_import(import_plan, extracted, project_root=dest)

    # The BRIEF, the OVERVIEW, the three ledgers, the TEMPLATE.
    for rel, expected in (
        ("project.json", BRIEF),
        ("context/overview.md", OVERVIEW),
        ("context/decisions.md", DECISIONS),
        ("context/not-yet-specified.md", FOG),
        ("context/out-of-scope.md", OUT_OF_SCOPE),
        ("templates/nightly.json", TEMPLATE),
    ):
        assert rel in written, f"{rel} did not arrive"
        got = (dest / rel).read_text(encoding="utf-8")
        assert got == expected, f"{rel} changed in transit"
        # sha256-verified against the MANIFEST, not merely against the source: the manifest is what
        # an importer on another machine has to trust.
        declared = {e["path"]: e["sha256"] for e in extracted.manifest["entries"]}
        assert declared[rel] == sha256_bytes(expected.encode("utf-8"))

    # ARTIFACT METADATA and RUN DIGESTS, as one entity each.
    assert "artifacts.json" in written and "runs.json" in written
    arts = json.loads((dest / "artifacts.json").read_text(encoding="utf-8"))
    assert arts[0]["slug"] == "sales-dash" and arts[0]["version"] == 3
    assert arts[0]["lineage"]["lineage_run_id"] == "r-42", "the lineage did not travel"
    assert "content" not in arts[0], "the artifact BODY travelled"
    runs = json.loads((dest / "runs.json").read_text(encoding="utf-8"))
    assert runs[0]["id"] == "r-42" and runs[0]["total_tokens"] == 1234
    assert "journal" not in runs[0], "the run JOURNAL travelled"

    assert plan.artifact_count == 1 and plan.run_count == 1


def test_ZERO_secrets_appear_in_the_archive_BYTES(project: Path):
    """Against the POLICY, not a restated list.

    Reads `secret_basenames()` and `excluded()` — the two things that define the posture — so this
    test follows the policy when it moves instead of pinning a stale copy of it.
    """
    raw, plan = pa.export_project_archive("p-round", project_root=project, runs=RUNS)

    # (a) no secret NAME is a member.
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        members = [pa.normalize_member(n) for n in zf.namelist() if n != pa.MANIFEST_NAME]
    for member in members:
        is_excluded, reason = excluded(member)
        assert (
            not is_excluded
        ), f"{member} is excluded by policy ({reason}) yet it is in the archive"
    basenames = {Path(m).name for m in members}
    assert not (
        basenames & secret_basenames()
    ), f"a credential is a member: {basenames & secret_basenames()}"

    # (b) no secret VALUE appears anywhere in the raw archive bytes. Compression means a plaintext
    # grep on the zip is not sufficient on its own, so both the raw bytes AND every decompressed
    # member are checked.
    assert b"SUPER-SECRET-VALUE-8f3a" not in raw
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for name in zf.namelist():
            assert b"SUPER-SECRET-VALUE-8f3a" not in zf.read(name), f"the secret leaked via {name}"

    # (c) each one is reported as a PRESENCE FLAG, so the far side knows to re-enter it.
    assert plan.secrets_present, "the credentials were dropped SILENTLY"
    assert set(plan.secrets_present) <= secret_basenames()


def test_a_worktree_does_NOT_travel(project: Path):
    """`derived_within=("*/worktrees",)` — git-owned, re-creatable, and gigabytes."""
    raw, _plan = pa.export_project_archive("p-round", project_root=project)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert not [n for n in zf.namelist() if "worktrees" in n]


def test_the_real_home_is_UNTOUCHED_by_a_round_trip(
    tmp_path: Path, project: Path, monkeypatch: pytest.MonkeyPatch
):
    """Every path is explicit, so an import cannot reach the live home even by default."""
    home_b = tmp_path / "home-b"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home_b))
    raw, _ = pa.export_project_archive("p-round", project_root=project)
    archive = tmp_path / "a.zip"
    archive.write_bytes(raw)
    plan, extracted = pa.read_archive_plan(archive)
    pa.commit_import(plan, extracted, project_root=home_b / "projects" / "x")
    assert (home_b / "projects" / "x" / "project.json").is_file()


# ── clause 1: extraction-time path safety, unique tmp, janitor cleanup ──


def test_a_TRAVERSAL_member_is_refused_at_EXTRACTION_time(tmp_path: Path):
    """Hand-built archive: a manifest naming a safe file and a member escaping the directory.

    Plan-time safety is a promise about a LIST; extraction is what touches the filesystem, so the
    refusal has to be provable against the write.
    """
    victim = tmp_path / "victim.txt"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            pa.MANIFEST_NAME,
            json.dumps({"schema": MANIFEST_SCHEMA, "project_name": "evil", "entries": []}),
        )
        zf.writestr(f"{pa.PAYLOAD_PREFIX}../../../{victim.name}", "pwned")
    archive = tmp_path / "evil.zip"
    archive.write_bytes(buf.getvalue())

    extracted = pa.extract_archive(archive)
    assert not victim.exists(), "the traversal member escaped the extraction directory"
    assert any(r.code == "unsafe_member" for r in extracted.refused)


def test_the_extraction_TEMP_DIR_is_unique_and_CLEANED(tmp_path: Path, project: Path):
    """A quarantine that survives a read leaves an unvetted archive unpacked on disk."""
    import tempfile

    raw, _ = pa.export_project_archive("p-round", project_root=project)
    archive = tmp_path / "a.zip"
    archive.write_bytes(raw)

    seen: list[str] = []
    real = tempfile.mkdtemp

    def _spy(*args, **kwargs):
        d = real(*args, **kwargs)
        if "pclaw-project-" in str(kwargs.get("prefix", "")):
            seen.append(d)
        return d

    tempfile.mkdtemp = _spy  # type: ignore[assignment]
    try:
        pa.extract_archive(archive)
        pa.extract_archive(archive)
    finally:
        tempfile.mkdtemp = real  # type: ignore[assignment]

    assert len(seen) == 2, "extraction did not use a per-call temp directory"
    assert seen[0] != seen[1], "two extractions SHARED a scratch directory"
    for d in seen:
        assert not os.path.exists(d), f"the janitor left {d} behind"


def test_the_temp_dir_is_cleaned_even_on_a_FAULT(tmp_path: Path):
    import tempfile

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip at all")
    seen: list[str] = []
    real = tempfile.mkdtemp

    def _spy(*args, **kwargs):
        d = real(*args, **kwargs)
        if "pclaw-project-" in str(kwargs.get("prefix", "")):
            seen.append(d)
        return d

    tempfile.mkdtemp = _spy  # type: ignore[assignment]
    try:
        with pytest.raises(pa.ArchiveRefused):
            pa.extract_archive(bad)
    finally:
        tempfile.mkdtemp = real  # type: ignore[assignment]
    for d in seen:
        assert not os.path.exists(d), "a refused archive left its quarantine on disk"


def test_a_TAMPERED_entity_is_refused_and_NAMED(tmp_path: Path, project: Path):
    """A digest mismatch costs that entity, not the project — and says which."""
    raw, _ = pa.export_project_archive("p-round", project_root=project)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    items[f"{pa.PAYLOAD_PREFIX}context/overview.md"] = b"# tampered\n"

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in items.items():
            zf.writestr(name, data)
    archive = tmp_path / "tampered.zip"
    archive.write_bytes(out.getvalue())

    plan, extracted = pa.read_archive_plan(archive)
    refused = {r.path: r.code for r in plan.refused}
    assert refused.get("context/overview.md") == "digest_mismatch"
    assert "project.json" in plan.accepted, "one bad entity cost the whole project"

    dest = tmp_path / "dest"
    written = pa.commit_import(plan, extracted, project_root=dest)
    assert "context/overview.md" not in written
    assert not (dest / "context" / "overview.md").exists()


def test_a_MISSING_manifest_is_refused_structurally(tmp_path: Path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{pa.PAYLOAD_PREFIX}project.json", "{}")
    archive = tmp_path / "no-manifest.zip"
    archive.write_bytes(buf.getvalue())
    with pytest.raises(pa.ArchiveRefused) as exc:
        pa.extract_archive(archive)
    assert exc.value.reason == "no_manifest"


def test_a_NAME_COLLISION_gets_a_slot_never_an_overwrite(tmp_path: Path, project: Path):
    raw, _ = pa.export_project_archive("p-round", project_root=project, project_name="Round Trip")
    archive = tmp_path / "a.zip"
    archive.write_bytes(raw)
    plan, _ = pa.read_archive_plan(archive, existing_names=["Round Trip"])
    assert plan.project_name == "Round Trip (imported-1)"


# ── clause 2: optional AES-GCM ──


def test_ENCRYPTION_is_available_on_this_install():
    """`cryptography` ships as the optional `oauth2` extra; the capability is REPORTED so a surface
    can hide a control it cannot honor rather than failing at the click."""
    assert pa.encryption_available() is True


@pytest.mark.skipif(not pa.encryption_available(), reason="optional `cryptography` extra absent")
def test_an_ENCRYPTED_archive_round_trips(tmp_path: Path, project: Path):
    raw, _ = pa.export_project_archive(
        "p-round", project_root=project, project_name="Round Trip", passphrase="correct horse"
    )
    assert pa.is_encrypted(raw), "the archive is not encrypted"
    assert raw[:2] != b"PK", "the plaintext zip header is still visible"
    assert b"ship the archive" not in raw, "the plaintext brief is readable in the ciphertext"

    archive = tmp_path / "enc.zip"
    archive.write_bytes(raw)
    plan, extracted = pa.read_archive_plan(archive, passphrase="correct horse")
    assert plan.ok
    dest = tmp_path / "dest"
    written = pa.commit_import(plan, extracted, project_root=dest)
    assert "project.json" in written
    assert (dest / "project.json").read_text(encoding="utf-8") == BRIEF


@pytest.mark.skipif(not pa.encryption_available(), reason="optional `cryptography` extra absent")
def test_a_WRONG_passphrase_is_refused(tmp_path: Path, project: Path):
    raw, _ = pa.export_project_archive("p-round", project_root=project, passphrase="right")
    archive = tmp_path / "enc.zip"
    archive.write_bytes(raw)
    with pytest.raises(pa.ArchiveRefused) as exc:
        pa.extract_archive(archive, passphrase="wrong")
    assert exc.value.reason == "decrypt_failed"


@pytest.mark.skipif(not pa.encryption_available(), reason="optional `cryptography` extra absent")
def test_TAMPERED_ciphertext_is_refused_like_a_wrong_passphrase(tmp_path: Path, project: Path):
    """One refusal for both: AES-GCM cannot tell them apart, and inventing a distinction would tell
    an attacker which of the two they achieved."""
    raw, _ = pa.export_project_archive("p-round", project_root=project, passphrase="right")
    mutated = bytearray(raw)
    mutated[-1] ^= 0xFF
    archive = tmp_path / "enc.zip"
    archive.write_bytes(bytes(mutated))
    with pytest.raises(pa.ArchiveRefused) as exc:
        pa.extract_archive(archive, passphrase="right")
    assert exc.value.reason == "decrypt_failed"


@pytest.mark.skipif(not pa.encryption_available(), reason="optional `cryptography` extra absent")
def test_an_encrypted_archive_without_a_passphrase_says_SO(tmp_path: Path, project: Path):
    """Not "corrupt". The magic header exists precisely so this case is distinguishable."""
    raw, _ = pa.export_project_archive("p-round", project_root=project, passphrase="right")
    archive = tmp_path / "enc.zip"
    archive.write_bytes(raw)
    with pytest.raises(pa.ArchiveRefused) as exc:
        pa.extract_archive(archive)
    assert exc.value.reason == "passphrase_required"


@pytest.mark.skipif(not pa.encryption_available(), reason="optional `cryptography` extra absent")
def test_two_encryptions_of_ONE_archive_differ(project: Path):
    """A reused salt/nonce pair under one passphrase is the one mistake AES-GCM does not survive."""
    a, _ = pa.export_project_archive("p-round", project_root=project, passphrase="same")
    b, _ = pa.export_project_archive("p-round", project_root=project, passphrase="same")
    assert a != b


# ── clause 3: the `projects` component ──


def test_PROJECTS_is_a_registered_snapshot_component():
    from personalclaw.snapshot import COMPONENT_HELP, VALID_COMPONENTS

    assert "projects" in VALID_COMPONENTS
    assert "projects" in COMPONENT_HELP, "--list-components must advertise it"


def test_the_projects_component_selects_ONLY_projects():
    """🔴 `--components projects` must not silently widen to every store, and must not narrow to
    nothing. `everything` remains a superset marker."""
    from personalclaw import snapshot

    assert snapshot._store_selected(["projects"], "projects/p-1") is True
    assert snapshot._store_selected(["projects"], "tasks") is False
    assert snapshot._store_selected(["everything"], "tasks") is True
    assert snapshot._store_selected(None, "projects/p-1") is True
    assert snapshot._store_selected(["memory"], "projects/p-1") is False


def test_DERIVED_WITHIN_now_has_an_executor():
    """🔴 The field was declared on four inventory entries and read by NOTHING, so every capture
    path copied `projects/*/worktrees` wholesale — a declaration that reads as a decision and
    behaves as an omission."""
    from personalclaw import snapshot
    from personalclaw.portability import _is_derived_within

    assert snapshot._derived_within("projects") == ("*/worktrees",)
    assert _is_derived_within("projects", "p-1/worktrees") is True
    # A FILE nested inside it, which is what an export walk actually offers.
    assert _is_derived_within("projects", "p-1/worktrees/repo/src/a.py") is True
    assert _is_derived_within("projects", "p-1/context/overview.md") is False
    assert _is_derived_within("tasks", "anything/worktrees") is False


def test_the_component_paths_are_PER_PROJECT(tmp_path: Path):
    home = tmp_path / "home"
    (home / "projects" / "p-1").mkdir(parents=True)
    (home / "projects" / "p-2").mkdir(parents=True)
    (home / "projects" / "loose.json").write_text("{}", encoding="utf-8")
    assert pa.project_component_paths(home) == ["projects/p-1", "projects/p-2"]


def test_a_SNAPSHOT_excludes_worktrees_but_keeps_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Driven through the real staging path: the capture must carry the brief and drop the worktree.

    `PERSONALCLAW_HOME` AND the module-level `config_dir` binding are both set — patching the loader
    alone is not isolation, because modules that bound `config_dir` at import keep writing to the
    real home.
    """
    from personalclaw import snapshot

    home = tmp_path / "home"
    proj = home / "projects" / "p-round"
    _seed_project(proj)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr(snapshot, "_pc_dir", lambda: home)

    ignore = snapshot._derived_ignore("projects", proj.parent)
    skipped = ignore(str(proj), [p.name for p in proj.iterdir()])
    assert "worktrees" in skipped, "the capture would carry the git worktree"
    assert "context" not in skipped and "project.json" not in skipped


def test_a_PORTABILITY_export_excludes_worktrees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The whole-home export path, driven — not just the predicate.

    Both whole-home directions must honor the same declaration; a rule enforced in one is the
    asymmetry that made a restore drop what a backup captured.
    """
    import personalclaw.portability as port

    home = tmp_path / "home"
    proj = home / "projects" / "p-round"
    _seed_project(proj)
    (home / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr(port, "_pc_dir", lambda: home)

    raw, _manifest = port.create_export_zip()
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
    assert not [n for n in names if "worktrees" in n], "the export carried a git worktree"
    assert [
        n for n in names if n.endswith("projects/p-round/project.json")
    ], "the export dropped the project itself"


# ── the download name ──


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Round Trip", "personalclaw-project-Round-Trip.zip"),
        ("../../etc", "personalclaw-project-etc.zip"),
        ("", "personalclaw-project-p-1.zip"),
    ],
)
def test_the_download_name_is_filesystem_SAFE(name: str, expected: str):
    assert pa.archive_filename(name, "p-1") == expected


def test_an_encrypted_download_is_NAMED_encrypted():
    assert pa.archive_filename("X", "p-1", encrypted=True).endswith(".zip.enc")


def test_the_export_summary_reports_what_was_LEFT_BEHIND(project: Path):
    """ "12 files, 40 KB" hides the two things a user has to act on."""
    _raw, plan = pa.export_project_archive("p-round", project_root=project)
    summary = pa.summarize_export(plan)
    assert summary["skipped"], "the skipped entities were not reported"
    assert summary["secrets_present"], "the credentials to re-enter were not reported"
    assert summary["schema"] == MANIFEST_SCHEMA
    json.dumps(summary)  # a REST surface has to be able to serialize it


def test_an_incomplete_plan_is_REFUSED_not_shipped_short(project: Path):
    """A manifest that claims an entity the archive lacks makes the importer report a refusal the
    exporter could have caught."""
    from personalclaw.workflows.project_export import plan_export

    plan = plan_export("p-x", files={"project.json": b"{}"})
    with pytest.raises(pa.ArchiveRefused) as exc:
        pa.write_archive(plan, {})
    assert exc.value.reason == "incomplete_plan"


def test_a_SYMLINK_inside_a_project_does_not_travel(tmp_path: Path):
    """A symlink in `context/` could name `~/.ssh/id_rsa` and call itself `notes.md`."""
    root = tmp_path / "p"
    (root / "context").mkdir(parents=True)
    (root / "project.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("SUPER-SECRET-VALUE-8f3a", encoding="utf-8")
    try:
        (root / "context" / "notes.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    files = pa.read_project_files(root)
    assert "context/notes.md" not in files
    assert "project.json" in files
