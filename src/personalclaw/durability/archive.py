"""Reading a snapshot archive's own manifest — the archive browser's row data (§6, DAS-10).

§6 asks the archive browser to show "per-domain row counts (from the manifest)". The
counts are written INTO the archive by `snapshot.py` (MANIFEST v3's ``domains`` block),
so this module's whole job is getting them back out cheaply.

**Why a sidecar.** Reading a member out of a `.tar.gz` means streaming-decompressing
until that member appears — worst case the whole archive. A settings page listing twenty
snapshots would decompress twenty multi-hundred-megabyte tarballs to render a table, so
the counts are also written beside the tar as ``<name>.manifest.json`` at snapshot time
and read from there. An archive taken before v3 has no sidecar: it is backfilled ONCE by
extracting its manifest, and reports ``None`` if it has no manifest at all. That is an
idempotent backfill, not a migration — nothing is rewritten, a missing sidecar simply
gets created the first time someone looks.

``None`` and ``{}`` are deliberately different answers: ``None`` means "this archive
recorded no counts", ``{}`` means "it recorded counts and there were none". Collapsing
them would make an empty backup indistinguishable from an unlabelled one.
"""

from __future__ import annotations

import json
import logging
import tarfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: Suffix of the manifest written beside each archive.
SIDECAR_SUFFIX = ".manifest.json"


def sidecar_path(archive: Path) -> Path:
    """Where ``archive``'s manifest sidecar lives."""
    return archive.with_name(archive.name + SIDECAR_SUFFIX)


def write_sidecar(archive: Path, manifest: dict) -> None:
    """Write ``archive``'s manifest beside it. Never raises — a missing sidecar is
    recoverable (it gets backfilled on read), but a snapshot that FAILS because its
    sidecar could not be written would trade a real backup for a cosmetic one."""
    from personalclaw.atomic_write import atomic_write

    try:
        atomic_write(sidecar_path(archive), json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    except OSError:
        logger.debug("archive: could not write manifest sidecar for %s", archive.name)


def read_manifest(archive: Path) -> dict | None:
    """``archive``'s MANIFEST, from its sidecar or (once) from inside the tar.

    Returns None when the archive carries no manifest at all — an archive from before
    manifests existed, or a corrupt one. A corrupt archive is NOT an error here: the
    browser's job is to list it so the user can see it exists and drill/restore it.
    """
    side = sidecar_path(archive)
    try:
        return json.loads(side.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    manifest = _manifest_from_tar(archive)
    if manifest is not None:
        write_sidecar(archive, manifest)
    return manifest


def _manifest_from_tar(archive: Path) -> dict | None:
    """Extract ``<prefix>/MANIFEST.json`` from a snapshot tar. One pass, first match."""
    try:
        with tarfile.open(str(archive), "r:gz") as tar:
            for member in tar:
                if not member.isfile() or not member.name.endswith("/MANIFEST.json"):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    return None
                return json.loads(handle.read().decode("utf-8"))
    except (tarfile.TarError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        logger.debug("archive: no readable manifest in %s", archive.name, exc_info=True)
    return None


def domain_counts(archive: Path) -> dict | None:
    """``archive``'s per-domain ``{files, bytes, rows}`` block, or None if it has none."""
    manifest = read_manifest(archive)
    if not isinstance(manifest, dict):
        return None
    domains = manifest.get("domains")
    return domains if isinstance(domains, dict) else None
