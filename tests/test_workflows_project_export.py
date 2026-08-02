"""Tests for project export/import (WORK-CONTAINERS §1.7 R15, S54). The property that matters
most: **no secret value can appear in an archive or its manifest.** Every other test in this
file is about integrity or safety; this one is about the thing that cannot be undone once an
archive is shared. Two findings encoded here: * The safety rules were compared against the
REAL `snapshot._data_filter` rather than assumed to match. They agree on every traversal
case; the one divergence is deliberately stricter and project-scoped. * An earlier version
branched on the exclusion REASON PROSE with a substring test, and "directory is never
exported (size or secrets)" contains "secret" — so every file inside `worktrees/` was
reported to the user as a credential they must re-enter. A prose string is for reading; a
code is for branching.
"""

import tarfile

import pytest

from personalclaw.workflows.project_export import (
    EXCLUDED_DIR,
    EXCLUDED_SECRET,
    MANIFEST_SCHEMA,
    MAX_FILE_BYTES,
    NEVER_EXPORT_DIRS,
    PORTABLE_SUFFIXES,
    artifact_digest,
    collision_name,
    excluded,
    import_summary,
    plan_export,
    plan_import,
    run_digest,
    safe_member,
    secret_basenames,
    sha256_bytes,
    verify_entry,
)

SECRET_VALUE = "hunter2-do-not-export"


def export(files: dict, **kw):
    return plan_export("p-1", project_name=kw.pop("name", "Ingest rework"), files=files, **kw)


# ── secrets never travel ──


def test_a_secret_file_is_EXCLUDED():
    """Not encrypted, not optional — absent. An archive is a thing that gets shared, and a
    credential inside one is a credential the user cannot recall.
    """
    plan = export({"project.json": b"{}", ".env": SECRET_VALUE.encode()})
    assert [e.path for e in plan.entries] == ["project.json"]


def test_no_secret_VALUE_appears_anywhere_in_the_manifest():
    """The strongest form of the check: not "the file was skipped" but "the bytes are not in
    the output", which is what actually matters if the manifest is pasted into a bug report.
    """
    plan = export({"project.json": b"{}", ".env": SECRET_VALUE.encode()})
    assert SECRET_VALUE not in str(plan.manifest())


def test_a_secret_is_reported_as_a_presence_FLAG():
    """The flag exists so an importer knows a credential is expected and can prompt — strictly
    more useful than the credential travelling.
    """
    plan = export({".env": SECRET_VALUE.encode()})
    assert plan.manifest()["secrets"] == [".env"]


def test_the_secret_set_is_READ_from_portability_not_re_listed():
    """`EXPORT_EXCLUDE` is itself a projection of the state inventory's `secret=True` entries.
    A local copy here would re-create the drift that let stores escape coverage before.
    """
    from personalclaw.portability import EXPORT_EXCLUDE

    assert secret_basenames() == frozenset(EXPORT_EXCLUDE)


def test_the_secret_set_covers_the_known_stores():
    for name in (".env", ".local_secret", "credentials", "sel_hmac.key"):
        assert name in secret_basenames()


@pytest.mark.parametrize("directory", sorted(NEVER_EXPORT_DIRS))
def test_a_never_exported_directory_is_refused(directory):
    plan = export({f"{directory}/inner.md": b"x", "project.json": b"{}"})
    assert [e.path for e in plan.entries] == ["project.json"]


def test_a_worktrees_file_is_NOT_reported_as_a_credential():
    """Measured: branching on the reason prose with a substring test meant "…(size or secrets)"
    matched "secret", so every file inside `worktrees/` was reported as a credential the
    user must re-enter. A prose string is for reading; a code is for branching.
    """
    plan = export({"worktrees/run-1/big.js": b"x" * 10, ".env": b"K=V"})
    assert plan.secrets_present == [".env"]


def test_the_exclusion_codes_are_TYPED():
    assert excluded(".env")[1] == EXCLUDED_SECRET
    assert excluded("worktrees/a.md")[1] == EXCLUDED_DIR


def test_an_exclusion_reason_is_REPORTED():
    """A silent exclusion makes an import look lossy for reasons nobody can name, and the user
    cannot tell a deliberate omission from a bug.
    """
    plan = export({"context/photo.png": b"\x89PNG"})
    assert any("photo.png" in s for s in plan.skipped)


# ── the portable set is an ALLOWLIST ──


@pytest.mark.parametrize("suffix", sorted(PORTABLE_SUFFIXES))
def test_a_portable_content_type_travels(suffix):
    plan = export({f"context/note{suffix}": b"content"})
    assert len(plan.entries) == 1


@pytest.mark.parametrize("suffix", [".png", ".zip", ".so", ".exe", ".db"])
def test_an_unportable_type_is_refused(suffix):
    """An allowlist rather than a denylist: a project dir accumulates whatever features write
    into it, and a denylist would export a future feature's private state by default.
    """
    plan = export({f"context/thing{suffix}": b"bytes"})
    assert plan.entries == []


def test_an_oversize_file_is_skipped_WITH_its_size():
    """A project note is prose; a multi-megabyte "note" is something else, and silently
    carrying it would make an export unpredictable in size for reasons the user cannot see.
    """
    plan = export({"context/huge.md": b"y" * (MAX_FILE_BYTES + 1)})
    assert plan.entries == []
    assert any("exceeds the per-file cap" in s for s in plan.skipped)


def test_a_dotfile_is_refused():
    plan = export({"context/.hidden": b"x"})
    assert plan.entries == []


# ── per-entity digests ──


def test_every_entry_carries_its_OWN_digest():
    """A whole-archive checksum tells the importer SOMETHING is wrong; a per-entity one tells
    it which file to refuse. On import the second is the only actionable form.
    """
    plan = export({"project.json": b"{}", "context/overview.md": b"hi"})
    assert all(e.sha256 for e in plan.entries)
    assert len({e.sha256 for e in plan.entries}) == 2


def test_the_digest_matches_the_content():
    body = b"the overview"
    plan = export({"context/overview.md": body})
    assert plan.entries[0].sha256 == sha256_bytes(body)


def test_the_manifest_declares_its_SCHEMA():
    """A manifest with no version is one a later reader has to guess the shape of, and the
    guess will be wrong exactly when the shape changed.
    """
    assert export({"project.json": b"{}"}).manifest()["schema"] == MANIFEST_SCHEMA


def test_metadata_travels_as_ONE_entity_each():
    """One entry per artifact would put hundreds of rows in a manifest whose job is to be
    readable.
    """
    plan = plan_export(
        "p-1",
        files={"project.json": b"{}"},
        artifact_metadata=[{"slug": f"a{i}"} for i in range(50)],
        run_digests=[{"id": f"r{i}"} for i in range(30)],
    )
    paths = [e.path for e in plan.entries]
    assert paths.count("artifacts.json") == 1
    assert paths.count("runs.json") == 1
    assert plan.artifact_count == 50
    assert plan.run_count == 30


def test_the_same_content_hashes_the_SAME_across_exports():
    """An unstable serialization would make two exports of an unchanged project produce
    different digests — and a digest that changes without the content changing cannot detect
    tampering.
    """
    first = plan_export("p-1", artifact_metadata=[{"slug": "a", "name": "A"}])
    second = plan_export("p-1", artifact_metadata=[{"slug": "a", "name": "A"}])
    assert first.entries[0].sha256 == second.entries[0].sha256


# ── artifact + run reduction ──


def test_an_artifact_travels_as_METADATA_not_a_body():
    """A 50-version image history would dwarf everything else, and the metadata plus lineage is
    what makes the artifact readable on the far side.
    """
    digest = artifact_digest(
        {"slug": "a", "name": "A", "kind": "markdown", "version": 3, "content": "x" * 10_000}
    )
    assert "content" not in digest
    assert digest["version"] == 3


def test_an_artifacts_LINEAGE_travels():
    """It is the reason metadata alone is useful: it names the run that produced this."""
    digest = artifact_digest({"slug": "a", "meta": {"lineage_source": "run:r1#write"}})
    assert digest["lineage"] == {"lineage_source": "run:r1#write"}


def test_a_run_digest_carries_NO_journal():
    """A journal carries every resolved prompt, which is the single most likely place a
    credential was echoed into an output.
    """
    digest = run_digest(
        {"id": "r1", "workflow_name": "w", "status": "complete", "total_tokens": 500}
    )
    assert set(digest) == {
        "id",
        "workflow_name",
        "status",
        "created_at",
        "completed_at",
        "total_tokens",
    }


# ── import safety mirrors the REAL filter ──


@pytest.mark.parametrize(
    "member", ["../../../etc/passwd", "/etc/passwd", "a/../../b", "ctx/../../../x"]
)
def test_a_traversal_member_is_REFUSED(member):
    ok, why = safe_member(member)
    assert ok is False
    assert why


@pytest.mark.parametrize(
    "member", ["../../../etc/passwd", "/etc/passwd", "a/../../b", "context/overview.md"]
)
def test_the_safety_rules_AGREE_with_snapshots_own_filter(member):
    """Compared against the real `_data_filter` rather than assumed to match. Two checkers with
    different rules would mean the weaker one wins wherever it runs — so these deliberately
    mirror it, and the mirroring is verified.
    """
    from personalclaw.snapshot import _data_filter

    filter_accepts = _data_filter(tarfile.TarInfo(name=member)) is not None
    mine_accepts, _why = safe_member(member)
    assert filter_accepts == mine_accepts


def test_the_real_filter_still_rejects_symlinks():
    """The name carries no signal for a symlink, so this refusal lives in the extraction filter
    — which is also where the TOCTOU gap is. This test pins that the filter still does it,
    because the plan-time check deliberately does not duplicate it.
    """
    from personalclaw.snapshot import _data_filter

    info = tarfile.TarInfo(name="context/evil.md")
    info.type = tarfile.SYMTYPE
    assert _data_filter(info) is None


def test_a_null_byte_member_is_refused():
    assert safe_member("context/ev\x00il.md")[0] is False


def test_an_implausibly_long_member_is_refused():
    assert safe_member("a/" * 400 + "x.md")[0] is False


def test_a_whitespace_padded_member_is_refused():
    """A name that differs from its stripped form is a name two readers will disagree about."""
    assert safe_member(" context/overview.md")[0] is False


def test_an_ordinary_member_is_accepted():
    assert safe_member("context/overview.md") == (True, "")


# ── digest verification refuses ──


def test_a_TAMPERED_entry_is_refused():
    """Importing a file whose hash does not match is importing something the exporter did not
    send — whether that is corruption or tampering does not change what the importer should
    do.
    """
    entry = {"path": "a.md", "sha256": sha256_bytes(b"original"), "size": 8}
    issue = verify_entry(entry, b"modified")
    assert issue is not None
    assert issue.code == "digest_mismatch"
    assert issue.fatal is True


def test_a_matching_entry_verifies():
    entry = {"path": "a.md", "sha256": sha256_bytes(b"original"), "size": 8}
    assert verify_entry(entry, b"original") is None


def test_an_entry_with_NO_digest_is_refused():
    """Unverifiable is not the same as fine. Accepting it would make the manifest's integrity
    claim optional, which means it is not a claim.
    """
    issue = verify_entry({"path": "a.md"}, b"anything")
    assert issue is not None
    assert issue.code == "no_digest"


def test_a_SIZE_mismatch_is_caught_too():
    entry = {"path": "a.md", "sha256": sha256_bytes(b"ab"), "size": 99}
    issue = verify_entry(entry, b"ab")
    assert issue is not None
    assert issue.code == "size_mismatch"


# ── the import plan ──


def build_archive(files: dict) -> tuple[dict, dict]:
    plan = export(files)
    manifest = plan.manifest()
    contents = {e["path"]: files[e["path"]] for e in manifest["entries"] if e["path"] in files}
    return manifest, contents


def test_a_clean_archive_imports_completely():
    manifest, contents = build_archive(
        {"project.json": b"{}", "context/overview.md": b"# Overview"}
    )
    plan = plan_import(manifest, contents)
    assert sorted(plan.accepted) == ["context/overview.md", "project.json"]
    assert plan.refused == []
    assert plan.ok is True


def test_ONE_corrupt_entry_costs_that_entry_not_the_project():
    """A partial import is the normal outcome for an archive that travelled."""
    manifest, contents = build_archive(
        {"project.json": b"{}", "context/overview.md": b"# Overview"}
    )
    contents["context/overview.md"] = b"tampered"
    plan = plan_import(manifest, contents)
    assert plan.accepted == ["project.json"]
    assert [r.code for r in plan.refused] == ["digest_mismatch"]
    assert plan.ok is True


def test_a_MISSING_content_entry_is_named():
    manifest, contents = build_archive({"project.json": b"{}", "context/overview.md": b"x"})
    contents.pop("context/overview.md")
    plan = plan_import(manifest, contents)
    assert [r.code for r in plan.refused] == ["missing_content"]


def test_an_UNKNOWN_schema_is_refused_rather_than_guessed():
    """Guessing at a shape this build does not know is how an import silently writes the wrong
    thing.
    """
    plan = plan_import({"schema": 99, "project_name": "P", "entries": []}, {})
    assert plan.ok is False
    assert [r.code for r in plan.refused] == ["schema_mismatch"]


def test_an_unsafe_member_in_the_manifest_is_refused():
    """The manifest is attacker-controlled input on an import. A traversal entry listed there
    must be refused at plan time, before anything touches the filesystem.
    """
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "project_name": "P",
        "entries": [{"path": "../../etc/passwd", "sha256": "x", "size": 1}],
    }
    plan = plan_import(manifest, {"../../etc/passwd": b"x"})
    assert [r.code for r in plan.refused] == ["unsafe_member"]


def test_a_wholly_refused_archive_is_NOT_an_import():
    manifest, contents = build_archive({"project.json": b"{}"})
    contents["project.json"] = b"tampered"
    assert plan_import(manifest, contents).ok is False


def test_the_secrets_a_user_must_RE_ENTER_are_surfaced():
    """ "Imported 12 files" without "3 credentials must be re-entered" produces a project that
    looks complete and fails on its first run.
    """
    manifest, contents = build_archive({"project.json": b"{}", ".env": b"K=V"})
    plan = plan_import(manifest, contents)
    assert plan.secrets_expected == [".env"]
    assert "re-entered" in import_summary(plan)


# ── collision slots ──


def test_a_fresh_name_is_used_as_is():
    assert collision_name("Ingest rework", ["Other"]) == "Ingest rework"


def test_a_COLLIDING_name_gets_a_slot_never_an_overwrite():
    """The user's existing project is the one thing an import must not damage, and a silent
    merge would be worse than either — a project that is neither the original nor the
    imported one.
    """
    assert collision_name("P", ["P"]) == "P (imported-1)"


def test_repeated_imports_get_DISTINCT_slots():
    """Importing the same archive three times should produce three projects rather than failing
    on the second.
    """
    existing = ["P", "P (imported-1)"]
    assert collision_name("P", existing) == "P (imported-2)"


def test_a_slot_name_re_imported_does_not_double_suffix():
    """ "P (imported-1) (imported-1)" is a name nobody can read."""
    assert collision_name("P (imported-1)", ["P (imported-1)"]) == "P (imported-2)"


def test_the_import_plan_carries_the_resolved_name():
    manifest, contents = build_archive({"project.json": b"{}"})
    plan = plan_import(manifest, contents, existing_names=["Ingest rework"])
    assert plan.project_name == "Ingest rework (imported-1)"


def test_the_summary_names_counts_and_credentials():
    manifest, contents = build_archive({"project.json": b"{}", ".env": b"K=V"})
    summary = import_summary(plan_import(manifest, contents))
    assert "1 entity imported" in summary
    assert "credential" in summary


def test_an_empty_export_produces_a_valid_manifest():
    """An empty project is a real project, and a manifest that refused to describe one would
    make the export path fail on exactly the simplest case.
    """
    manifest = export({}).manifest()
    assert manifest["entries"] == []
    assert manifest["schema"] == MANIFEST_SCHEMA
