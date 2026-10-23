"""An image built to an OCI layout, read the way the release reads one from its registry (#3942).

The release rehearsal builds each image for both arches with its supply-chain attestations, as
the release does, but to an OCI layout (``docker buildx build --output type=oci``) instead of a
registry: it pushes nothing. This reads that layout's tar:

- the image's multi-platform index, the bytes a registry would store, which the digest-bound
  release manifest records (``tools/ci/release_manifest.py``, ``--image NAME=INDEX_FILE``);
- each platform's SPDX SBOM, from the attestation manifest BuildKit adds beside it, which the
  release reads with ``docker buildx imagetools inspect --format '{{ json .SBOM }}'``;
- each platform's bases: every image its build resolved, with the digest it ran, as BuildKit's
  SLSA provenance beside it names them (#3911's build identities, which the release manifest
  records through tools/ci/build_identity.py).

    python3 -I tools/ci/oci_layout.py <layout.tar> --name <image> --out <folder>

writes ``<image>.index.json``, and ``<image>-<arch>.spdx.json`` and ``<image>-<arch>.bases.json``
for each platform into the folder, and exits 1 naming what it could not find: a layout with no
index, an index with no platform, a platform with no SPDX SBOM or no provenance, a document that
is not SPDX, or a provenance that names no base image or one without its sha256 digest.
Standard library only, so a job runs it as a script.
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
#: The in-toto predicates BuildKit's provenance carries: SLSA v1, and the v0.2 it wrote before.
SLSA_PREDICATES = ("https://slsa.dev/provenance/v1", "https://slsa.dev/provenance/v0.2")
#: How a provenance names an image it resolved.
DOCKER_PURL = "pkg:docker/"
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


def _statements(layout: Layout, index: bytes, predicates: tuple[str, ...]) -> dict[str, dict]:
    """Each platform's in-toto statement of one of *predicates*, from the attestation manifest
    beside its image manifest."""
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
            if (layer.get("annotations") or {}).get(PREDICATE_TYPE) not in predicates:
                continue
            out[platform] = _json(layout.blob(layer["digest"]), f"{platform}'s attestation")
    for platform in PLATFORMS:
        if platform not in platform_of.values():
            raise LayoutError(f"the image's index has no {platform} manifest")
    return out


def sboms(layout: Layout, index: bytes) -> dict[str, dict]:
    """Each platform's SPDX document, from the attestation manifest beside its image manifest."""
    out: dict[str, dict] = {}
    statements = _statements(layout, index, (SPDX_PREDICATE,))
    for platform in PLATFORMS:
        if platform not in statements:
            raise LayoutError(f"{platform}: missing SPDX SBOM attestation")
        document = statements[platform].get("predicate")
        if not isinstance(document, dict) or document.get("SPDXID") != "SPDXRef-DOCUMENT":
            raise LayoutError(f"{platform}: malformed SPDX document")
        out[platform] = document
    return out


def bases(layout: Layout, index: bytes) -> dict[str, list[dict[str, str]]]:
    """Each platform's bases: every image its build resolved, as ``{"image", "digest"}``, from
    the SLSA provenance beside its image manifest (v1's ``resolvedDependencies``, v0.2's
    ``materials``). What else the build read, its source repository, is not an image."""
    out: dict[str, list[dict[str, str]]] = {}
    statements = _statements(layout, index, SLSA_PREDICATES)
    for platform in PLATFORMS:
        if platform not in statements:
            raise LayoutError(f"{platform}: missing SLSA provenance attestation")
        predicate = statements[platform].get("predicate")
        predicate = predicate if isinstance(predicate, dict) else {}
        definition = predicate.get("buildDefinition")
        resolved = (definition if isinstance(definition, dict) else {}).get("resolvedDependencies")
        found: list[dict[str, str]] = []
        for dependency in resolved or predicate.get("materials") or []:
            uri = dependency.get("uri") if isinstance(dependency, dict) else None
            if not isinstance(uri, str) or not uri.startswith(DOCKER_PURL):
                continue
            digest = (dependency.get("digest") or {}).get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise LayoutError(f"{platform}: {uri} has no sha256 digest")
            found.append({"image": uri, "digest": f"sha256:{digest}"})
        if not found:
            raise LayoutError(f"{platform}: the provenance names no base image")
        out[platform] = found
    return out


def write(layout_path: Path, name: str, out: Path) -> list[Path]:
    """Write the image's index, and each platform's SBOM and bases, into *out*; return the files
    written."""
    layout = Layout(layout_path)
    index = image_index(layout)
    out.mkdir(parents=True, exist_ok=True)
    written = [out / f"{name}.index.json"]
    written[0].write_bytes(index)
    for platform, document in sorted(sboms(layout, index).items()):
        path = out / f"{name}-{platform.removeprefix('linux/')}.spdx.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    for platform, found in sorted(bases(layout, index).items()):
        path = out / f"{name}-{platform.removeprefix('linux/')}.bases.json"
        path.write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
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
