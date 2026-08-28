"""Project export/import: a manifest ZIP with integrity and path safety (§1.7, R15 — S54).

This is **net-new coverage**, not an extension: `snapshot.VALID_COMPONENTS` covers memory, crons,
config, skills, workspace, notifications and security — and neither `projects/` nor `tasks/` nor
`artifacts/` is in it. A project today cannot be moved off the machine at all.

What travels: the brief, the living overview, the wayfinder ledgers, context files, project-local
templates, artifact METADATA and run digests — with a sha256 per entity in `manifest.json`.

What does NOT travel, and why each is a decision rather than an omission:

* **Secrets never travel.** Not encrypted, not optional — absent, with presence flags in
their place.
  The exclude-set is `portability.EXPORT_EXCLUDE`, which is itself a projection of the state
  inventory's `secret=True` entries. Reusing it is the whole point: a second hand-maintained list is
  exactly the drift that let stores escape coverage before.
  Secrets reach an export by TWO routes, and both end in a presence flag: a secret-named FILE inside
  the project is excluded by `excluded()` and flagged by its basename, and a project's VAULT keys
  (EI-10) arrive as `plan_export(secret_names=…)` — those have no file at all, because a project
  secret lives in the credential store under a namespaced key. The second route exists because the
  first cannot see it: a file-exclusion loop over a project directory will never encounter a
  credential that was never in a file, so an export would silently report none.
* **Workspace directories are excluded** (too large). Only metadata, templates and
  digests — an export
  that tried to carry a `node_modules` would be an export nobody completes.
* **Artifact BODIES do not travel, only their metadata.** A 50-version image history would dwarf
  everything else in the archive, and the metadata is what makes the lineage readable on
  the far side.

Import is where the danger lives, so its rules are stated as refusals:

* **Path safety reuses `snapshot._data_filter`** — traversal, absolute paths, symlinks
and hardlinks
  are rejected inside the extraction filter, which is what closes the TOCTOU gap a pre-scan leaves
  open. Writing a second checker here would mean two postures, and the weaker one would win wherever
  it ran.
* **A checksum mismatch REFUSES the entity**, and says which. Importing a file whose hash does not
  match the manifest is importing something the exporter did not send.
* **A name collision gets an `imported-N` slot**, never an overwrite. The user's existing project is
  the one thing an import must not damage.

Pure planning + manifest arithmetic. The archive I/O stays with the caller; this module decides what
belongs, what it hashes to, and what an import may accept.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

#: The manifest's schema version. Present from the first export: a manifest with no version is one a
#: later reader has to guess the shape of, and the guess will be wrong exactly when the
#: shape changed.
MANIFEST_SCHEMA = 1

#: Files inside a project directory that DO travel. An allowlist rather than a denylist, because a
#: project dir accumulates whatever features write into it — and a denylist would export a future
#: feature's private state by default, which is how a credential escapes.
PORTABLE_FILES = (
    "project.json",
    "context/overview.md",
    "context/decisions.md",
    "context/not-yet-specified.md",
    "context/out-of-scope.md",
)

#: Subdirectories whose CONTENTS travel, filtered by extension. `context/` holds user-
#: authored notes;
#: `templates/` holds project-local workflow definitions.
PORTABLE_DIRS = ("context", "templates")

#: Extensions allowed inside a portable dir. Text and structured data only — a binary in
#: a project's
#: context dir is either an artifact (which travels as metadata) or something the
#: exporter has no way
#: to reason about, and exporting an unknown binary is the same class of risk as exporting a secret.
PORTABLE_SUFFIXES = frozenset({".md", ".json", ".yaml", ".yml", ".txt", ".csv"})

#: Directories never exported, whatever else says. `worktrees/` can be gigabytes of checkout, and
#: `secrets/` is named for what it holds.
NEVER_EXPORT_DIRS = frozenset({"worktrees", "secrets", "__pycache__", ".git", "node_modules"})

#: Cap per exported file. A project note is prose; a multi-megabyte "note" is something else, and
#: silently carrying it would make an export unpredictable in size for reasons the user cannot see.
MAX_FILE_BYTES = 1 * 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: Typed exclusion reasons. Measured: an earlier version matched the reason PROSE with a substring
#: test, and "directory is never exported (size or secrets)" contains "secret" — so
#: every file inside
#: `worktrees/` was reported to the user as a credential they must re-enter. A prose string is for
#: reading; a code is for branching, and conflating them makes the branch depend on wording.
EXCLUDED_SECRET = "secret"
EXCLUDED_DIR = "never_exported_dir"
EXCLUDED_DOTFILE = "dotfile"
EXCLUDED_TYPE = "unportable_type"
EXCLUDED_EMPTY = "empty_path"

#: Human text per reason, so a report reads well without the branch depending on the prose.
EXCLUSION_TEXT = {
    EXCLUDED_SECRET: "secret — presence flag only, never the value",
    EXCLUDED_DIR: "in a directory that is never exported (size or secrets)",
    EXCLUDED_DOTFILE: "dotfile",
    EXCLUDED_EMPTY: "empty path",
}


def exclusion_text(reason: str) -> str:
    """Human wording for an exclusion code. Unknown codes pass through verbatim."""
    return EXCLUSION_TEXT.get(reason, reason)


def secret_basenames() -> frozenset[str]:
    """The secret exclude-set, read from `portability` rather than re-listed.

            message=f"sha256 mismatch: manifest says {declared[:12]}…, "
            f"contents hash {actual[:12]}…",
    two hand-maintained lists is exactly what let stores escape coverage before. A local copy here
    would re-create the problem one directory over.
    """
    try:
        from personalclaw.portability import EXPORT_EXCLUDE

        return frozenset(EXPORT_EXCLUDE)
    except Exception:
        # Fail CLOSED on the historical literals: an export that cannot read the inventory must not
        # therefore include credentials.
        return frozenset(
            {".env", ".local_secret", "credentials", "sel_hmac.key", "session_map.json"}
        )


def excluded(rel_path: str) -> tuple[bool, str]:
    """Whether a project-relative path is excluded, and why.

    The reason is returned so an export can REPORT what it left behind. A silent exclusion makes an
    import look lossy for reasons nobody can name, and the user cannot tell a deliberate
    omission from
    a bug.
    """
    parts = PurePosixPath(rel_path).parts
    if not parts:
        return True, EXCLUDED_EMPTY
    if any(p in NEVER_EXPORT_DIRS for p in parts):
        return True, EXCLUDED_DIR
    name = parts[-1]
    if name in secret_basenames():
        return True, EXCLUDED_SECRET
    if name.startswith("."):
        return True, EXCLUDED_DOTFILE
    suffix = PurePosixPath(name).suffix.lower()
    if suffix and suffix not in PORTABLE_SUFFIXES:
        return True, f"{EXCLUDED_TYPE}: {suffix} is not a portable content type"
    return False, ""


@dataclass
class Entry:
    """One entity in the archive, with its own digest.

    Per-entity hashes rather than one archive hash: a whole-archive checksum tells the importer that
    SOMETHING is wrong, and a per-entity one tells it which file to refuse. On an import
    the second is
    the only actionable form.
    """

    path: str
    size: int
    sha256: str
    kind: str = "file"  # file | metadata | digest

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256, "kind": self.kind}


@dataclass
class ExportPlan:
    """What an export will contain, what it refused, and the manifest to write.

    `skipped` is returned rather than logged, for the same reason `excluded` returns a reason: an
    export that quietly dropped half a project would produce an import the user cannot reconcile
    against the original.
    """

    project_id: str
    project_name: str = ""
    entries: list[Entry] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    secrets_present: list[str] = field(default_factory=list)
    artifact_count: int = 0
    run_count: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)

    def manifest(self) -> dict[str, Any]:
        """The manifest, with a digest per entity and presence flags for secrets.

        `secrets` lists NAMES, never values — the flag exists so an importer knows a credential is
        expected on the far side and can prompt for it, which is strictly more useful than the
        credential itself travelling.
        """
        return {
            "schema": MANIFEST_SCHEMA,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "entries": [e.to_dict() for e in self.entries],
            "secrets": sorted(self.secrets_present),
            "artifact_count": self.artifact_count,
            "run_count": self.run_count,
            "skipped": list(self.skipped),
            "total_bytes": self.total_bytes,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.manifest()


def plan_export(
    project_id: str,
    *,
    project_name: str = "",
    files: dict[str, bytes] | None = None,
    artifact_metadata: list[dict[str, Any]] | None = None,
    run_digests: list[dict[str, Any]] | None = None,
    secret_names: list[str] | None = None,
) -> ExportPlan:
    """Decide what travels, hashing each entity as it goes.

    Takes the file CONTENTS rather than a directory, so the exclusion and hashing rules are testable
    without a project on disk — and so a caller that already read the files does not
    read them twice.

    *secret_names* are the project's VAULT key names (EI-10). They are presence flags with no file
    behind them: a project secret lives in the credential store under a namespaced key, not in a
    file inside the project, so the file-exclusion loop below can never see one and the archive has
    nothing to leave out. Without this argument a project's secrets would be invisible to an
    export — the importer would report "0 credentials to re-enter" for a project with five, which
    is the one wrong answer here, because the user acts on it by not re-entering anything.

    They are NAMES only, and the caller supplies them from
    ``secrets_vault.project_secret_names`` — a function whose whole read path is name-based. There
    is no code path from an export to a credential VALUE.
    """
    plan = ExportPlan(project_id=project_id, project_name=project_name)
    for name in secret_names or []:
        plan.secrets_present.append(name)
    for rel_path, data in sorted((files or {}).items()):
        is_excluded, reason = excluded(rel_path)
        if is_excluded:
            plan.skipped.append(f"{rel_path}: {exclusion_text(reason)}")
            # Branch on the CODE, not the prose. Only a genuine secret earns a presence
            # flag — a file
            # excluded for living in `worktrees/` is not a credential the user has to re-enter.
            if reason == EXCLUDED_SECRET:
                plan.secrets_present.append(PurePosixPath(rel_path).name)
            continue
        if len(data) > MAX_FILE_BYTES:
            plan.skipped.append(f"{rel_path}: {len(data)} bytes exceeds the per-file cap")
            continue
        plan.entries.append(
            Entry(path=rel_path, size=len(data), sha256=sha256_bytes(data), kind="file")
        )

    # Artifact metadata and run digests travel as ONE entity each rather than one per item: the
    # importer reads them as a list, and a per-item entry would put hundreds of rows in a manifest
    # whose job is to be readable.
    if artifact_metadata:
        payload = _canonical(artifact_metadata)
        plan.artifact_count = len(artifact_metadata)
        plan.entries.append(
            Entry(
                path="artifacts.json",
                size=len(payload),
                sha256=sha256_bytes(payload),
                kind="metadata",
            )
        )
    if run_digests:
        payload = _canonical(run_digests)
        plan.run_count = len(run_digests)
        plan.entries.append(
            Entry(path="runs.json", size=len(payload), sha256=sha256_bytes(payload), kind="digest")
        )
    return plan


def _canonical(value: Any) -> bytes:
    """Deterministic JSON bytes, so the same content hashes the same.

    Sorted keys and no whitespace variance: an unstable serialization would make two exports of an
    unchanged project produce different digests, and a digest that changes without the content
    changing is a digest nobody can use to detect tampering.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def artifact_digest(artifact: dict[str, Any]) -> dict[str, Any]:
    """One artifact reduced to portable metadata. The BODY does not travel.

    A 50-version image history would dwarf everything else in the archive, and the metadata plus the
    lineage is what makes the artifact readable on the far side — the body is recoverable from
    whichever run produced it, which the lineage names.
    """
    return {
        "slug": str(artifact.get("slug", "") or ""),
        "name": str(artifact.get("name", "") or ""),
        "kind": str(artifact.get("kind", "") or ""),
        "version": int(artifact.get("version", 1) or 1),
        "updated_at": str(artifact.get("updated_at", "") or ""),
        # Lineage is the reason metadata alone is useful: it names the run that produced this.
        "lineage": {
            k: v for k, v in (artifact.get("meta") or {}).items() if str(k).startswith("lineage_")
        }
        or {k: v for k, v in (artifact.get("lineage") or {}).items()},
    }


def run_digest(run: dict[str, Any]) -> dict[str, Any]:
    """One run reduced to a digest. Journal and outputs do NOT travel.

    A run's journal is its cache and can be large; what a reader needs on the far side
    is what the run
    WAS and what it cost. Carrying the journal would also carry every resolved prompt, which is the
    single most likely place for a credential to have been echoed into an output.
    """
    return {
        "id": str(run.get("id", "") or ""),
        "workflow_name": str(run.get("workflow_name", "") or ""),
        "status": str(run.get("status", "") or ""),
        "created_at": str(run.get("created_at", "") or ""),
        "completed_at": str(run.get("completed_at", "") or ""),
        "total_tokens": int(run.get("total_tokens", 0) or 0),
    }


# ── import ──


@dataclass
class ImportIssue:
    """One problem with an incoming archive. `fatal` refuses the entity."""

    path: str
    code: str
    message: str
    fatal: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "fatal": self.fatal,
        }


@dataclass
class ImportPlan:
    """What an import will accept, what it refuses, and the name it will land under.

    `accepted` and `refused` are both lists because a partial import is the normal case
    for an archive
    that travelled: one corrupt entry should cost that entry, not the project. The
    refusals are named
    so the user can see exactly what did not arrive.
    """

    project_name: str
    accepted: list[str] = field(default_factory=list)
    refused: list[ImportIssue] = field(default_factory=list)
    secrets_expected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether anything at all can be imported. A wholly-refused archive is not an import."""
        return bool(self.accepted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "accepted": list(self.accepted),
            "refused": [r.to_dict() for r in self.refused],
            "secrets_expected": list(self.secrets_expected),
            "ok": self.ok,
        }


def safe_member(name: str) -> tuple[bool, str]:
    """Whether an archive member name is safe to extract, and why not when it is not.

    The SAME refusals `snapshot._data_filter` makes, applied at plan time so an importer can report
    them before touching the filesystem. The filter still runs at extraction — that is
    what closes the
    TOCTOU gap — and this is the readable half, not a replacement. Two checkers with
    different rules
    would mean the weaker one wins wherever it runs, so these deliberately mirror it.
    """
    if not name or name.strip() != name:
        return False, "empty or whitespace-padded member name"
    if name.startswith("/"):
        return False, "absolute path"
    parts = PurePosixPath(name).parts
    if ".." in parts:
        return False, "path traversal"
    if any(p in NEVER_EXPORT_DIRS for p in parts):
        return False, "member is in a never-exported directory"
    if "\x00" in name:
        return False, "null byte in member name"
    if len(name) > 512:
        return False, "member name is implausibly long"
    return True, ""


def verify_entry(entry: dict[str, Any], data: bytes) -> ImportIssue | None:
    """Check one entry against its manifest digest. Returns an issue, or None when it matches.

    A mismatch REFUSES the entity. Importing a file whose hash does not match is importing something
    the exporter did not send — whether that is corruption in transit or tampering does not change
    what the importer should do about it.
    """
    path = str(entry.get("path", "") or "")
    declared = str(entry.get("sha256", "") or "")
    if not declared:
        return ImportIssue(
            path=path,
            code="no_digest",
            message="manifest entry carries no sha256, so its contents cannot be verified",
        )
    actual = sha256_bytes(data)
    if actual != declared:
        return ImportIssue(
            path=path,
            code="digest_mismatch",
            message=f"sha256 mismatch: manifest says {declared[:12]}…, "
            f"contents hash {actual[:12]}…",
        )
    declared_size = entry.get("size")
    if isinstance(declared_size, int) and declared_size != len(data):
        return ImportIssue(
            path=path,
            code="size_mismatch",
            message=f"manifest says {declared_size} bytes, contents are {len(data)}",
        )
    return None


_SLOT_RE = re.compile(r"^(?P<base>.*?)(?: \(imported-(?P<n>\d+)\))?$")


def collision_name(name: str, existing: list[str]) -> str:
    """A non-colliding project name, using `imported-N` slots.

    Never an overwrite: the user's existing project is the one thing an import must not
    damage, and a
    silent merge would be worse than either — it would produce a project that is neither
    the original
    nor the imported one.

    Slots count from the EXISTING ones rather than always starting at 1, so importing
    the same archive
    three times produces three distinct projects rather than failing on the second.
    """
    taken = set(existing or [])
    if name not in taken:
        return name
    match = _SLOT_RE.match(name)
    base = (match.group("base") if match else name) or name
    index = 1
    while f"{base} (imported-{index})" in taken:
        index += 1
    return f"{base} (imported-{index})"


def plan_import(
    manifest: dict[str, Any],
    contents: dict[str, bytes],
    *,
    existing_names: list[str] | None = None,
) -> ImportPlan:
    """Decide what an incoming archive may contribute.

    Every entry is checked for member safety AND digest. A partial import is the normal
    outcome for an
    archive that travelled: one corrupt entry costs that entry, and the refusals are
    named so the user
    can see what did not arrive.
    """
    name = str(manifest.get("project_name", "") or "imported project")
    plan = ImportPlan(project_name=collision_name(name, existing_names or []))

    schema = manifest.get("schema")
    if schema != MANIFEST_SCHEMA:
        plan.refused.append(
            ImportIssue(
                path="manifest.json",
                code="schema_mismatch",
                message=(
                    f"manifest schema {schema!r} is not {MANIFEST_SCHEMA} — refusing rather than "
                    "guessing at a shape this build does not know"
                ),
            )
        )
        return plan

    plan.secrets_expected = [str(s) for s in (manifest.get("secrets") or [])]

    for entry in manifest.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", "") or "")
        safe, why = safe_member(path)
        if not safe:
            plan.refused.append(ImportIssue(path=path, code="unsafe_member", message=why))
            continue
        if path not in contents:
            plan.refused.append(
                ImportIssue(
                    path=path,
                    code="missing_content",
                    message="the manifest lists this entry but the archive does not contain it",
                )
            )
            continue
        issue = verify_entry(entry, contents[path])
        if issue is not None:
            plan.refused.append(issue)
            continue
        plan.accepted.append(path)
    return plan


def import_summary(plan: ImportPlan) -> str:
    """One line a user can act on.

    Names the counts AND the expected secrets, because "imported 12 files" without "3
    credentials must
    be re-entered" produces a project that looks complete and fails on its first run.
    """
    parts = [f"{len(plan.accepted)} entit{'y' if len(plan.accepted) == 1 else 'ies'} imported"]
    if plan.refused:
        parts.append(f"{len(plan.refused)} refused")
    if plan.secrets_expected:
        parts.append(
            f"{len(plan.secrets_expected)} credential(s) must be re-entered "
            f"({', '.join(plan.secrets_expected[:3])})"
        )
    return f"{plan.project_name}: " + "; ".join(parts)
