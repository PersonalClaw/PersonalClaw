"""One-link setup export — a pack as a single JSON document (AGENT-PACKS §2.3/§4.4, AP-4).

"Here is my setup" without the ZIP ceremony: the SAME :class:`packs.build.PackPreview`
content, serialized as one shareable JSON file/link. Small members ride inline as base64;
larger ones ride as a ``url`` + ``sha256`` so a big skill bundle does not bloat the link.

**It is a serialization, not a second format.** :func:`import_onelink` materializes the
document back into a byte-exact ``.pclaw`` and then calls :func:`packs.import_.import_pack` —
so a one-link import goes through the identical §3 pipeline (quarantine, integrity recompute,
referential-integrity lint, supply-chain scan, leaves-first journaled commit, rollback,
connector resolution). There is deliberately no second importer to keep in sync, because a
second importer is a second set of security decisions to get wrong.

**Per-resource hashes are the verification, and they are enforced.** Every member carries its
own ``sha256``; :func:`materialize` re-hashes the bytes it actually obtained and REFUSES on
any mismatch, before a ``.pclaw`` exists. That makes a partial/URL-backed fetch verifiable
member by member rather than "trust the link". The pack-level ``content_hash`` inside
``pack.json`` is then re-derived independently by the importer, so a doctored one-link
document has to survive two unrelated checks.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: The one-link document's own version. Independent of the pack ``schema_version`` it carries:
#: this versions the ENVELOPE, and a reader that understands envelope 1 can carry any pack
#: schema through unchanged.
ONELINK_VERSION = 1

#: Members at or below this size are embedded as base64. Above it, the document carries a
#: ``url`` + ``sha256`` instead (§2.3). 256 KiB keeps a typical personal-scale pack fully
#: self-contained — the two bundled Domain OS packs embed whole — while a link stays pasteable.
INLINE_MAX_BYTES = 256 * 1024

#: Hard ceiling on a materialized member, applied BEFORE decode/write. A one-link document is
#: untrusted input; without a cap a 20-byte base64 field could name a multi-gigabyte member.
MEMBER_MAX_BYTES = 8 * 1024 * 1024

#: Hard ceiling on the whole materialized pack.
TOTAL_MAX_BYTES = 64 * 1024 * 1024


class OneLinkError(Exception):
    """A one-link document that cannot be trusted or materialized. Always fail closed."""


def _safe_member(name: str) -> bool:
    """Reject any member name that could build a traversing/absolute path.

    Deliberately the same rule :func:`packs.import_._safe_member` applies to ZIP members —
    one-link is a second *transport* for the same bytes, so it gets the same path contract
    rather than a laxer one written from memory."""
    from personalclaw.packs.import_ import _safe_member as zip_safe_member

    return zip_safe_member(name)


def to_onelink(
    pack_path: Path | str,
    *,
    base_url: str = "",
) -> dict[str, Any]:
    """Serialize an existing ``.pclaw`` into a one-link JSON document.

    ``base_url`` is where the large members will be published (``<base_url>/<member>``); it is
    REQUIRED as soon as any member exceeds :data:`INLINE_MAX_BYTES`, because a document that
    referenced a resource with no way to obtain it would be a link that cannot import — a
    failure the recipient discovers, not the author.
    """
    path = Path(pack_path)
    resources: dict[str, dict[str, Any]] = {}
    manifest: dict[str, Any] = {}
    try:
        zf = zipfile.ZipFile(str(path))
    except (zipfile.BadZipFile, OSError) as exc:
        raise OneLinkError(f"not a readable .pclaw archive: {exc}") from exc
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not _safe_member(name):
                raise OneLinkError(f"unsafe pack member path: {name!r}")
            raw = zf.read(info)
            digest = hashlib.sha256(raw).hexdigest()
            if name == "pack.json":
                try:
                    manifest = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise OneLinkError(f"pack.json is not valid JSON: {exc}") from exc
            entry: dict[str, Any] = {"sha256": digest, "size": len(raw)}
            if len(raw) <= INLINE_MAX_BYTES:
                entry["b64"] = base64.b64encode(raw).decode("ascii")
            else:
                if not base_url:
                    raise OneLinkError(
                        f"member {name!r} is {len(raw)} bytes (> {INLINE_MAX_BYTES}); a "
                        "base_url is required so the link can reference it"
                    )
                entry["url"] = f"{base_url.rstrip('/')}/{name}"
            resources[name] = entry
    if not manifest:
        raise OneLinkError("pack has no pack.json manifest")
    return {
        "onelink_version": ONELINK_VERSION,
        "name": str(manifest.get("name", "") or ""),
        "version": str(manifest.get("version", "") or ""),
        "displayName": str(manifest.get("displayName", "") or ""),
        "description": str(manifest.get("description", "") or ""),
        "resources": resources,
    }


def _fetch_url(url: str) -> bytes:
    """GET ``url`` under the CONNECTOR egress profile — the module's ONLY network primitive.

    Reuses :func:`packs.catalog_marketplace.fetch_catalog_text`'s seam so a one-link fetch is
    governed by exactly the egress policy every other pack-side fetch is, rather than opening a
    second, unlayered path to the network."""
    from personalclaw.packs.catalog_marketplace import fetch_catalog_text

    return fetch_catalog_text(url).encode("utf-8")


def materialize(
    doc: dict[str, Any],
    out_path: Path,
    *,
    fetch: Callable[[str], bytes] | None = None,
) -> Path:
    """Rebuild the ``.pclaw`` a one-link document describes; return its path.

    Every member's bytes are re-hashed against the document's own ``sha256`` and a mismatch
    REFUSES the whole materialization — nothing partial is written, so a doctored resource
    cannot reach the importer. ``fetch`` overrides the network primitive (tests inject it); by
    default a ``url`` member is fetched under the CONNECTOR egress profile.
    """
    if not isinstance(doc, dict):
        raise OneLinkError("one-link document is not an object")
    if int(doc.get("onelink_version", 0) or 0) > ONELINK_VERSION:
        # Best-effort forward read is fine for a PACK schema, but not for the envelope: an
        # envelope we do not understand may have moved where the hashes live.
        raise OneLinkError(
            f"one-link envelope version {doc.get('onelink_version')!r} is newer than "
            f"{ONELINK_VERSION} — upgrade to import this link"
        )
    resources = doc.get("resources")
    if not isinstance(resources, dict) or not resources:
        raise OneLinkError("one-link document carries no resources")
    if "pack.json" not in resources:
        raise OneLinkError("one-link document carries no pack.json resource")

    getter = fetch if fetch is not None else _fetch_url
    materialized: dict[str, bytes] = {}
    total = 0
    for name in sorted(resources):
        entry = resources[name]
        if not isinstance(entry, dict):
            raise OneLinkError(f"resource {name!r} is not an object")
        if not _safe_member(name):
            raise OneLinkError(f"unsafe pack member path: {name!r}")
        declared = str(entry.get("sha256", "") or "")
        if not declared:
            raise OneLinkError(f"resource {name!r} declares no sha256 (unverifiable)")
        size = int(entry.get("size", 0) or 0)
        if size > MEMBER_MAX_BYTES:
            raise OneLinkError(f"resource {name!r} declares {size} bytes (> {MEMBER_MAX_BYTES})")
        raw = _obtain(name, entry, getter)
        if len(raw) > MEMBER_MAX_BYTES:
            raise OneLinkError(f"resource {name!r} is {len(raw)} bytes (> {MEMBER_MAX_BYTES})")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != declared:
            raise OneLinkError(
                f"resource {name!r} hash mismatch: declared={declared} actual={actual}"
            )
        total += len(raw)
        if total > TOTAL_MAX_BYTES:
            raise OneLinkError(f"one-link pack exceeds {TOTAL_MAX_BYTES} bytes")
        materialized[name] = raw

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for name in sorted(materialized):
            zf.writestr(str(PurePosixPath(name)), materialized[name])
    return out_path


def _obtain(name: str, entry: dict[str, Any], getter: Callable[[str], bytes]) -> bytes:
    """One member's bytes: inline base64, or a URL fetch. Neither present is an error."""
    b64 = entry.get("b64")
    if isinstance(b64, str) and b64:
        try:
            return base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise OneLinkError(f"resource {name!r} has undecodable base64: {exc}") from exc
    url = entry.get("url")
    if isinstance(url, str) and url:
        try:
            return getter(url)
        except OneLinkError:
            raise
        except Exception as exc:  # noqa: BLE001 — any transport failure is a refusal
            raise OneLinkError(f"resource {name!r} could not be fetched from {url}: {exc}") from exc
    raise OneLinkError(f"resource {name!r} carries neither inline bytes nor a url")


def import_onelink(
    doc: dict[str, Any],
    *,
    consent: bool = False,
    tier: Any = None,
    connector_choices: dict[str, dict[str, Any]] | None = None,
    fetch: Callable[[str], bytes] | None = None,
):
    """Import a one-link document through the SAME §3 pipeline as a ``.pclaw`` file.

    Materializes to a SYSTEM tempdir (never the home) and hands the resulting archive to
    :func:`packs.import_.import_pack`, so every §3 guarantee — dry-run inspect, integrity
    recompute, lint refusal, DANGEROUS/WARNING gating, journaled rollback — applies unchanged.
    Returns the :class:`packs.import_.ImportPlan`.
    """
    import shutil
    import tempfile

    from personalclaw.packs.import_ import import_pack

    staging = Path(tempfile.mkdtemp(prefix="pclaw-onelink-"))
    try:
        archive = materialize(doc, staging / "onelink.pclaw", fetch=fetch)
        return import_pack(archive, consent=consent, tier=tier, connector_choices=connector_choices)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def inspect_onelink(
    doc: dict[str, Any],
    *,
    tier: Any = None,
    fetch: Callable[[str], bytes] | None = None,
):
    """The dry-run half: materialize + :func:`packs.import_.inspect_pack`, no home writes."""
    import shutil
    import tempfile

    from personalclaw.packs.import_ import inspect_pack

    staging = Path(tempfile.mkdtemp(prefix="pclaw-onelink-"))
    try:
        archive = materialize(doc, staging / "onelink.pclaw", fetch=fetch)
        return inspect_pack(archive, tier=tier)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
