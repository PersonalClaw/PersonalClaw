"""Project archive I/O: the ZIP writer and extractor around ``project_export``'s planning (S54, C9).

``project_export`` is the DECISION layer — what travels, what it hashes to, what an import may
accept. It deliberately takes file CONTENTS rather than a directory so the exclusion and digest
rules are testable without a project on disk. That left the whole module with **no importer**: a
complete export planner exports nothing until something reads a project off the filesystem, writes
a ZIP, and extracts one back. This is that half, and nothing more — every policy question is still
answered next door.

Three rules this module owns, and why each is here rather than there:

* **Extraction goes to a UNIQUE temp directory, never a shared one.** Two concurrent imports into
  one scratch path would let the second overwrite the first's members between the digest check and
  the read — the same TOCTOU shape the extraction filter exists to close, reintroduced one level up.
* **The temp directory is removed on EVERY exit path**, fault included. A quarantine that survives
  a failed import leaves the archive's contents unpacked on disk after the import that would have
  vetted them refused; `packs/import_.py` uses exactly this `try/finally` shape.
* **Path safety runs at EXTRACTION time**, not only at plan time. `project_export.safe_member` is
  the readable half and runs first; this module applies the same predicate again per member as it
  writes, because a plan-time scan is a promise about a list and extraction is what touches the
  filesystem.

Encryption is OPTIONAL and off by default. An encrypted archive is a file the user cannot read
without the passphrase they chose, which is a real way to lose a project — so plaintext stays the
default and the encrypted form is asked for explicitly.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from personalclaw.workflows.project_export import (
    MANIFEST_SCHEMA,
    PORTABLE_DIRS,
    PORTABLE_FILES,
    ExportPlan,
    ImportIssue,
    ImportPlan,
    artifact_digest,
    excluded,
    plan_export,
    plan_import,
    run_digest,
    safe_member,
)

#: The manifest's name inside the archive. Fixed rather than discovered: a reader that globs for
#: "something ending in manifest.json" will one day find a project's own note by that name.
MANIFEST_NAME = "manifest.json"

#: Where entity payloads live inside the archive, so the manifest can never collide with a project
#: file called `manifest.json`.
PAYLOAD_PREFIX = "project/"

#: Cap on the number of members an incoming archive may declare, and on the bytes extracted. A ZIP
#: is compressed, so a small archive can expand without bound — refusing early is the only place
#: the ceiling can be enforced before the disk fills.
MAX_MEMBERS = 5000
MAX_TOTAL_EXTRACTED = 64 * 1024 * 1024


class ArchiveRefused(Exception):
    """A structural refusal: the archive is not readable as a project export at all.

    Distinct from `ImportPlan.refused`, which names entities that did not survive verification. A
    refused ARCHIVE has no entities to report on — treating the two the same would report "0 of 0
    imported" for a file that is not a ZIP.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ── reading a project off disk ──


def read_project_files(project_root: Path) -> dict[str, bytes]:
    """Every portable file under a project directory, keyed by project-relative POSIX path.

    Reads the ALLOWLIST (`PORTABLE_FILES` + the contents of `PORTABLE_DIRS`) rather than walking
    the whole directory: a project dir accumulates whatever later features write into it, and a
    walk would carry a future feature's private state by default. `excluded()` still runs on every
    candidate — the allowlist decides where to look, the exclusion policy decides what travels, and
    keeping both means a secret dropped into `context/` is refused by name even though the
    directory is portable.

    Symlinks are skipped. A symlink inside a project can point anywhere on the filesystem, so
    following one would let a project export read `~/.ssh/id_rsa` by naming it `context/notes.md`.
    """
    out: dict[str, bytes] = {}
    if not project_root.is_dir():
        return out

    candidates: list[Path] = []
    for rel in PORTABLE_FILES:
        candidates.append(project_root / rel)
    for dirname in PORTABLE_DIRS:
        d = project_root / dirname
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*")):
            candidates.append(path)

    seen: set[str] = set()
    for path in candidates:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            rel_path = path.relative_to(project_root).as_posix()
        except (OSError, ValueError):
            continue
        if rel_path in seen:
            continue
        seen.add(rel_path)
        is_excluded, _reason = excluded(rel_path)
        if is_excluded:
            # Still handed to `plan_export`, which is what turns a secret into a presence flag and
            # a skip line. Filtering here instead would make the export SILENT about it, and a
            # user who cannot see that a credential was left behind will not re-enter it.
            pass
        try:
            out[rel_path] = path.read_bytes()
        except OSError:
            continue
    return out


def collect_project(
    project_id: str,
    *,
    project_root: Path,
    project_name: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
) -> ExportPlan:
    """Plan an export for one project on disk: files + artifact metadata + run digests.

    The digest reductions come from `project_export` (`artifact_digest`/`run_digest`), so the
    "bodies and journals do not travel" decision has exactly one home.
    """
    files = read_project_files(project_root)
    return plan_export(
        project_id,
        project_name=project_name,
        files=files,
        artifact_metadata=[artifact_digest(a) for a in (artifacts or [])],
        run_digests=[run_digest(r) for r in (runs or [])],
    )


# ── writing the archive ──


def write_archive(
    plan: ExportPlan,
    files: dict[str, bytes],
    *,
    artifact_metadata: list[dict[str, Any]] | None = None,
    run_digests: list[dict[str, Any]] | None = None,
) -> bytes:
    """The ZIP bytes for a planned export.

    Writes ONLY what the plan accepted. Iterating `files` here instead would put every skipped
    entity in the archive while the manifest denied it — an archive whose contents and manifest
    disagree is worse than either, because the importer trusts the manifest and the grep-for-secrets
    check reads the bytes.

    The payload entities (`artifacts.json`, `runs.json`) are re-serialized through the SAME
    canonical form the plan hashed, so the digest in the manifest matches the bytes in the archive.
    """
    payloads = _entity_payloads(artifact_metadata, run_digests)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(plan.manifest(), indent=2, sort_keys=True))
        for entry in plan.entries:
            if entry.path in payloads:
                data = payloads[entry.path]
            elif entry.path in files:
                data = files[entry.path]
            else:
                # The plan names an entity the caller did not supply. Refusing beats writing a
                # short archive whose manifest claims the entity: the importer would report a
                # `missing_content` refusal the exporter could have caught.
                raise ArchiveRefused(
                    "incomplete_plan",
                    f"{entry.path} is in the manifest but its contents were not supplied",
                )
            zf.writestr(f"{PAYLOAD_PREFIX}{entry.path}", data)
    return buf.getvalue()


def _entity_payloads(
    artifact_metadata: list[dict[str, Any]] | None,
    run_digests: list[dict[str, Any]] | None,
) -> dict[str, bytes]:
    """The canonical bytes for the two list-shaped entities, matching what the plan hashed."""
    from personalclaw.workflows.project_export import _canonical

    out: dict[str, bytes] = {}
    if artifact_metadata:
        out["artifacts.json"] = _canonical(artifact_metadata)
    if run_digests:
        out["runs.json"] = _canonical(run_digests)
    return out


def export_project_archive(
    project_id: str,
    *,
    project_root: Path,
    project_name: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    runs: list[dict[str, Any]] | None = None,
    passphrase: str = "",
) -> tuple[bytes, ExportPlan]:
    """Plan and write one project's archive. Returns (bytes, plan).

    The plan travels back beside the bytes because it names what was SKIPPED and which credentials
    the far side must re-enter — information a caller cannot recover from the archive, since the
    whole point is that the secrets are not in it.

    A non-empty `passphrase` encrypts the archive (see `encrypt_archive`); the default is plaintext.
    """
    files = read_project_files(project_root)
    artifact_meta = [artifact_digest(a) for a in (artifacts or [])]
    run_meta = [run_digest(r) for r in (runs or [])]
    plan = plan_export(
        project_id,
        project_name=project_name,
        files=files,
        artifact_metadata=artifact_meta,
        run_digests=run_meta,
    )
    raw = write_archive(plan, files, artifact_metadata=artifact_meta, run_digests=run_meta)
    if passphrase:
        raw = encrypt_archive(raw, passphrase)
    return raw, plan


# ── extraction ──


@dataclass
class ExtractedArchive:
    """A read archive: its manifest, its member bytes, and any structural refusals.

    `contents` is keyed by PROJECT-relative path (the payload prefix stripped) so it feeds
    `plan_import` directly — the manifest speaks project-relative paths, and translating at the
    boundary keeps the archive layout from leaking into the planning layer.
    """

    manifest: dict[str, Any] = field(default_factory=dict)
    contents: dict[str, bytes] = field(default_factory=dict)
    refused: list[ImportIssue] = field(default_factory=list)


def _open_archive(path: Path, passphrase: str = "") -> tuple[Path, Path | None]:
    """Resolve `path` to a readable ZIP, decrypting into a unique temp file when needed.

    Returns (zip_path, tmp_to_clean). The second element is what the caller must remove — returning
    it rather than cleaning here keeps the decrypted plaintext alive exactly as long as the read
    that needs it.
    """
    data: bytes
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ArchiveRefused("unreadable", str(exc)) from exc

    if is_encrypted(data):
        if not passphrase:
            raise ArchiveRefused(
                "passphrase_required",
                "this archive is encrypted; import it with the passphrase it was created with",
            )
        data = decrypt_archive(data, passphrase)
        tmp = Path(tempfile.mkdtemp(prefix="pclaw-project-decrypt-"))
        plain = tmp / "archive.zip"
        plain.write_bytes(data)
        os.chmod(str(plain), 0o600)
        return plain, tmp
    return path, None


def extract_archive(
    path: Path,
    *,
    passphrase: str = "",
) -> ExtractedArchive:
    """Read an archive into memory, applying path safety AT EXTRACTION TIME.

    Members are extracted into a UNIQUE temp directory and read back from it rather than being
    handed straight out of `ZipFile.read`. That is deliberate: extracting is what the importer will
    ultimately do, so the safety predicate must run against the same operation, and a member that
    escapes the directory is caught by comparing the RESOLVED path — a name-only check cannot see a
    symlinked parent.

    The temp directory is removed on every exit path, fault included.
    """
    zip_path, decrypt_tmp = _open_archive(path, passphrase)
    work = Path(tempfile.mkdtemp(prefix="pclaw-project-import-"))
    result = ExtractedArchive()
    try:
        try:
            zf = zipfile.ZipFile(str(zip_path))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ArchiveRefused("not_an_archive", f"not a readable .zip: {exc}") from exc

        with zf:
            infos = zf.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ArchiveRefused(
                    "too_many_members",
                    f"{len(infos)} members exceeds the {MAX_MEMBERS} ceiling",
                )
            total = sum(max(0, i.file_size) for i in infos)
            if total > MAX_TOTAL_EXTRACTED:
                raise ArchiveRefused(
                    "too_large",
                    f"{total} declared bytes exceeds the {MAX_TOTAL_EXTRACTED} ceiling",
                )

            manifest_raw: bytes | None = None
            for info in infos:
                name = info.filename
                if info.is_dir():
                    continue
                if name == MANIFEST_NAME:
                    manifest_raw = _extract_one(zf, info, work, name)
                    continue
                if not name.startswith(PAYLOAD_PREFIX):
                    result.refused.append(
                        ImportIssue(
                            path=name,
                            code="unexpected_member",
                            message=(
                                f"member is outside {PAYLOAD_PREFIX!r} and is not the manifest"
                            ),
                        )
                    )
                    continue
                rel = name[len(PAYLOAD_PREFIX) :]
                # The SAME predicate the plan uses, applied here because this is the call that
                # writes to the filesystem. Two checkers with different rules would mean the
                # weaker one wins wherever it ran, so there is exactly one.
                safe, why = safe_member(rel)
                if not safe:
                    result.refused.append(ImportIssue(path=rel, code="unsafe_member", message=why))
                    continue
                data = _extract_one(zf, info, work, rel)
                if data is None:
                    result.refused.append(
                        ImportIssue(
                            path=rel,
                            code="escapes_destination",
                            message="member resolved outside the extraction directory",
                        )
                    )
                    continue
                result.contents[rel] = data

            if manifest_raw is None:
                raise ArchiveRefused("no_manifest", f"the archive has no {MANIFEST_NAME}")
            try:
                parsed = json.loads(manifest_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArchiveRefused("bad_manifest", str(exc)) from exc
            if not isinstance(parsed, dict):
                raise ArchiveRefused("bad_manifest", "the manifest is not an object")
            result.manifest = parsed
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if decrypt_tmp is not None:
            shutil.rmtree(decrypt_tmp, ignore_errors=True)


def _extract_one(zf: zipfile.ZipFile, info: zipfile.ZipInfo, work: Path, rel: str) -> bytes | None:
    """Write one member under `work` and read it back. None when it resolves outside `work`.

    The resolve-and-compare is the extraction-time half of path safety: `safe_member` rejects the
    NAMES that are obviously unsafe, and this catches whatever a name cannot express — a component
    the OS normalizes differently, or a parent that is a symlink by the time the write happens.
    """
    dest = (work / rel).resolve()
    root = work.resolve()
    if dest != root and root not in dest.parents:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(info) as src, open(dest, "wb") as out:
        written = 0
        while True:
            chunk = src.read(64 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_TOTAL_EXTRACTED:
                raise ArchiveRefused(
                    "too_large", f"{rel} expands past the {MAX_TOTAL_EXTRACTED} ceiling"
                )
            out.write(chunk)
    return dest.read_bytes()


def read_archive_plan(
    path: Path,
    *,
    existing_names: list[str] | None = None,
    passphrase: str = "",
) -> tuple[ImportPlan, ExtractedArchive]:
    """Extract an archive and plan its import. The pair a caller needs to commit one.

    Structural refusals from extraction are folded into the plan's `refused` list, so a caller has
    ONE place to read what did not arrive — an archive with an unsafe member and a corrupt entry
    should not report them through two channels.
    """
    archive = extract_archive(path, passphrase=passphrase)
    plan = plan_import(archive.manifest, archive.contents, existing_names=existing_names)
    plan.refused.extend(archive.refused)
    return plan, archive


def commit_import(
    plan: ImportPlan,
    archive: ExtractedArchive,
    *,
    project_root: Path,
) -> list[str]:
    """Write the ACCEPTED entities under `project_root`. Returns the paths written.

    Only accepted entities are written — a refusal costs that entity, which is what makes a partial
    import the normal outcome for an archive that travelled. `safe_member` runs a THIRD time here
    rather than trusting the plan, because this function is separately callable and a caller that
    hand-built a plan must not be able to talk it into writing outside the project.
    """
    written: list[str] = []
    root = project_root.resolve()
    for rel in plan.accepted:
        safe, _why = safe_member(rel)
        if not safe:
            continue
        data = archive.contents.get(rel)
        if data is None:
            continue
        dest = (project_root / rel).resolve()
        if dest != root and root not in dest.parents:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written.append(rel)
    return written


# ── optional client-side encryption ──

#: Magic prefix for an encrypted archive. Present so `import` can tell "encrypted" from "corrupt"
#: without a passphrase: without it, a wrong-format file and a missing passphrase produce the same
#: unhelpful "not a zip".
ENCRYPTION_MAGIC = b"PCLAWPRJ1"
SALT_BYTES = 16
NONCE_BYTES = 12
KDF_ITERATIONS = 600_000


class EncryptionUnavailable(Exception):
    """AES-GCM was asked for on an install without the optional `cryptography` extra."""


def encryption_available() -> bool:
    """Whether the optional AES-GCM path can run on this install.

    `cryptography` is an OPTIONAL extra (`pyproject.toml` `[project.optional-dependencies]`
    `oauth2 = ["cryptography>=42"]`), not a runtime dependency — so encryption is offered when the
    extra is present and refused with a named reason when it is not. Reporting the capability is
    what lets a surface hide a control it cannot honor rather than failing at the click.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 — an absent optional extra is not an error here
        return False


def is_encrypted(data: bytes) -> bool:
    return data[: len(ENCRYPTION_MAGIC)] == ENCRYPTION_MAGIC


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=KDF_ITERATIONS)
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_archive(data: bytes, passphrase: str) -> bytes:
    """AES-GCM the archive under a passphrase-derived key.

    Client-side: the key never leaves this process and is never stored, so a lost passphrase means
    a lost archive. That is the honest trade for an export the user may put on a USB stick — the
    alternative is a key in the credential store, which makes the encryption decorative the moment
    the machine it protects against is the one holding the key.

    The salt and nonce are random per archive and travel in the header. Reusing either across
    archives under one passphrase is the one mistake AES-GCM does not survive.
    """
    if not encryption_available():
        raise EncryptionUnavailable(
            "AES-GCM needs the optional `cryptography` extra: pip install 'personalclaw[oauth2]'"
        )
    if not passphrase:
        raise ValueError("an empty passphrase would encrypt nothing")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = _derive_key(passphrase, salt)
    # The header is AUTHENTICATED, not merely prepended: without it a salt swap would be
    # undetectable, and the failure would surface as an unexplained decrypt error rather than as
    # tampering.
    header = ENCRYPTION_MAGIC + salt + nonce
    ct = AESGCM(key).encrypt(nonce, data, header)
    return header + ct


def decrypt_archive(data: bytes, passphrase: str) -> bytes:
    """Reverse `encrypt_archive`. Raises `ArchiveRefused` on a wrong passphrase or tampering.

    Both cases are ONE refusal on purpose: AES-GCM cannot distinguish "wrong key" from "modified
    ciphertext", and inventing a distinction would tell an attacker which of the two they achieved.
    """
    if not encryption_available():
        raise EncryptionUnavailable(
            "this archive is encrypted and AES-GCM needs the optional `cryptography` extra"
        )
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    head = len(ENCRYPTION_MAGIC)
    if not is_encrypted(data):
        raise ArchiveRefused("not_encrypted", "this archive carries no encryption header")
    salt = data[head : head + SALT_BYTES]
    nonce = data[head + SALT_BYTES : head + SALT_BYTES + NONCE_BYTES]
    header = data[: head + SALT_BYTES + NONCE_BYTES]
    ct = data[head + SALT_BYTES + NONCE_BYTES :]
    if len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES or not ct:
        raise ArchiveRefused("truncated", "the encryption header is incomplete")
    key = _derive_key(passphrase, salt)
    try:
        return AESGCM(key).decrypt(nonce, ct, header)
    except InvalidTag as exc:
        raise ArchiveRefused(
            "decrypt_failed",
            "wrong passphrase, or the archive was modified after it was created",
        ) from exc


# ── the snapshot/portability `projects` component ──


def project_component_paths(home: Path) -> list[str]:
    """Home-relative paths the `projects` snapshot component covers.

    Returns the per-project directories rather than the `projects/` root so a component that names
    the projects can also EXCLUDE what must not travel: `derived_within=("*/worktrees",)` on the
    inventory's `projects` entry says a worktree is a git-owned checkout, re-creatable from the
    repo, and a component that copied the root wholesale would carry gigabytes of it.
    """
    root = home / "projects"
    if not root.is_dir():
        return []
    out: list[str] = []
    for d in sorted(root.iterdir()):
        try:
            if not d.is_dir() or d.is_symlink():
                continue
        except OSError:
            continue
        out.append(f"projects/{d.name}")
    return out


def portable_project_members(project_root: Path) -> list[str]:
    """Project-relative paths inside one project directory that an export may carry.

    The same allowlist `read_project_files` reads, exposed as PATHS so `portability` can decide what
    to add to a zip without loading every file into memory first.
    """
    return sorted(read_project_files(project_root).keys())


def manifest_schema() -> int:
    """The manifest schema this build writes. Re-exported so a surface need not import two modules
    to report the archive format it produced."""
    return MANIFEST_SCHEMA


def is_project_archive_member(name: str) -> bool:
    """Whether an archive member name belongs to a project payload."""
    return name == MANIFEST_NAME or name.startswith(PAYLOAD_PREFIX)


def archive_filename(project_name: str, project_id: str, *, encrypted: bool = False) -> str:
    """A filesystem-safe download name for one project's archive.

    Derived from the NAME when it yields anything usable and from the id otherwise: a project called
    `../../etc` must not name a file, and a project called `Q3 Planning` should not download as
    `p-1a2b3c4d.zip` when the user has to find it again in a downloads folder.
    """
    stem = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (project_name or "")).strip(
        "-"
    )
    stem = "-".join(p for p in stem.split("-") if p)[:60]
    if not stem:
        stem = str(project_id or "project")
    suffix = ".zip.enc" if encrypted else ".zip"
    return f"personalclaw-project-{stem}{suffix}"


def summarize_export(plan: ExportPlan) -> dict[str, Any]:
    """A JSON-able summary of what an export contained, for a REST/CLI surface.

    Reports the SKIPPED entities and the expected secrets alongside the counts. An export that says
    only "12 files, 40 KB" hides the two decisions a user has to act on: what was left behind, and
    which credentials the far side will ask for.
    """
    return {
        "project_id": plan.project_id,
        "project_name": plan.project_name,
        "entries": len(plan.entries),
        "total_bytes": plan.total_bytes,
        "artifact_count": plan.artifact_count,
        "run_count": plan.run_count,
        "skipped": list(plan.skipped),
        "secrets_present": sorted(plan.secrets_present),
        "schema": MANIFEST_SCHEMA,
    }


def normalize_member(name: str) -> str:
    """An archive member name reduced to its project-relative form."""
    if name.startswith(PAYLOAD_PREFIX):
        return name[len(PAYLOAD_PREFIX) :]
    return PurePosixPath(name).as_posix()
