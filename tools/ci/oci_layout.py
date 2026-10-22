"""An image built to an OCI layout, read the way the release reads one from its registry (#3942).

The release rehearsal builds each image for both arches with its supply-chain attestations, as
the release does, but to an OCI layout (``docker buildx build --output type=oci``) instead of a
registry: it pushes nothing. This reads that layout's tar:

- the image's multi-platform index, the bytes a registry would store, which the digest-bound
  release manifest records (``tools/ci/release_manifest.py``, ``--image NAME=INDEX_FILE``);
- each platform's SPDX SBOM, from the attestation manifest BuildKit adds beside it, which the
  release reads with ``docker buildx imagetools inspect --format '{{ json .SBOM }}'``.

    python3 -I tools/ci/oci_layout.py <layout.tar> --name <image> --out <folder>

writes ``<image>.index.json`` and ``<image>-<arch>.spdx.json`` for each platform into the folder,
and exits 1 naming what it could not find: a layout with no index, an index with no platform, a
platform with no SPDX SBOM, or a document that is not SPDX. Standard library only, so a job runs
it as a script.
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
#: How BuildKit marks an attestation manifest in an index, and names what it attests.
ATTESTATION_TYPE = ("vnd.docker.reference.type", "attestation-manifest")
ATTESTS = "vnd.docker.reference.digest"
#: The in-toto predicate an SPDX SBOM attestation carries.
SPDX_PREDICATE = "https://spdx.dev/Document"
PREDICATE_TYPE = "in-toto.io/predicate-type"
#: The platforms the release builds every image for.
PLATFORMS = ("linux/amd64", "linux/arm64")


class LayoutError(Exception):
    """The layout lacks something the release reads from its registry."""


class Layout:
    """An OCI layout's tar, read by blob digest."""

    def __init__(self, path: Path) -> None:
        self._tar = tarfile.open(path)
        self._names = set(self._tar.getnames())

    def file(self, name: str) -> bytes:
        name = name.removeprefix("./")
        for candidate in (name, f"./{name}"):
            if candidate in self._names:
                member = self._tar.extractfile(candidate)
                if member is not None:
                    return member.read()
        raise LayoutError(f"the layout has no {name}")

    def blob(self, digest: str) -> bytes:
        algorithm, _, hexdigest = digest.partition(":")
        if algorithm != "sha256" or not hexdigest:
            raise LayoutError(f"{digest!r} is not a sha256 digest")
        return self.file(f"blobs/sha256/{hexdigest}")


def _json(data: bytes, what: str) -> dict:
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise LayoutError(f"{what} is not JSON: {exc}") from None
    if not isinstance(value, dict):
        raise LayoutError(f"{what} is not an object")
    return value


def image_index(layout: Layout) -> bytes:
    """The multi-platform index the layout's top index names: the bytes a registry would store."""
    top = _json(layout.file("index.json"), "the layout's index.json")
    indexes = [
        m
        for m in top.get("manifests") or []
        if isinstance(m, dict) and m.get("mediaType") in INDEX_TYPES
    ]
    if len(indexes) != 1:
        raise LayoutError(
            f"the layout's index.json names {len(indexes)} multi-platform indexes, not one"
        )
    return layout.blob(indexes[0]["digest"])


def sboms(layout: Layout, index: bytes) -> dict[str, dict]:
    """Each platform's SPDX document, from the attestation manifest beside its image manifest."""
    manifests = [
        m for m in _json(index, "the image's index").get("manifests") or [] if isinstance(m, dict)
    ]
    platform_of = {}
    for m in manifests:
        p = m.get("platform") or {}
        if (m.get("annotations") or {}).get(ATTESTATION_TYPE[0]) != ATTESTATION_TYPE[1] and p.get(
            "os"
        ):
            platform_of[m.get("digest")] = f"{p.get('os')}/{p.get('architecture')}"
    out: dict[str, dict] = {}
    for m in manifests:
        annotations = m.get("annotations") or {}
        if annotations.get(ATTESTATION_TYPE[0]) != ATTESTATION_TYPE[1]:
            continue
        platform = platform_of.get(annotations.get(ATTESTS))
        if platform is None:
            continue
        attestation = _json(layout.blob(m["digest"]), f"{platform}'s attestation manifest")
        for layer in attestation.get("layers") or []:
            if (layer.get("annotations") or {}).get(PREDICATE_TYPE) != SPDX_PREDICATE:
                continue
            statement = _json(layout.blob(layer["digest"]), f"{platform}'s SPDX attestation")
            document = statement.get("predicate")
            if not isinstance(document, dict) or document.get("SPDXID") != "SPDXRef-DOCUMENT":
                raise LayoutError(f"{platform}: malformed SPDX document")
            out[platform] = document
    for platform in PLATFORMS:
        if platform not in platform_of.values():
            raise LayoutError(f"the image's index has no {platform} manifest")
        if platform not in out:
            raise LayoutError(f"{platform}: missing SPDX SBOM attestation")
    return out


def write(layout_path: Path, name: str, out: Path) -> list[Path]:
    """Write the image's index and each platform's SBOM into *out*; return the files written."""
    layout = Layout(layout_path)
    index = image_index(layout)
    out.mkdir(parents=True, exist_ok=True)
    written = [out / f"{name}.index.json"]
    written[0].write_bytes(index)
    for platform, document in sorted(sboms(layout, index).items()):
        path = out / f"{name}-{platform.removeprefix('linux/')}.spdx.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oci_layout.py", add_help=False)
    parser.add_argument("layout", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--out", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    try:
        for path in write(args.layout, args.name, args.out):
            print(f"oci-layout: wrote {path}")
    except (LayoutError, OSError, tarfile.TarError) as exc:
        print(f"oci-layout: {args.layout}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
