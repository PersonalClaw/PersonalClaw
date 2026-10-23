"""The release rehearsal reads an image's OCI layout as the release reads its registry (#3942).

``tools/ci/oci_layout.py`` takes a layout BuildKit wrote (``--output type=oci``) and gives the
image's multi-platform index, the bytes the digest-bound manifest records, each platform's SPDX
SBOM, and the base images each platform's build resolved, from the SLSA provenance beside it
(#3911's build identities). Each test builds a layout of its own.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from tools.ci import oci_layout

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def _spdx(arch: str) -> dict:
    return {"SPDXID": "SPDXRef-DOCUMENT", "name": f"personalclaw-gateway-{arch}", "packages": []}


def _base(arch: str) -> str:
    return "sha256:" + hashlib.sha256(f"python base {arch}".encode()).hexdigest()


def _provenance(arch: str) -> dict:
    """BuildKit's SLSA v1 provenance for one platform: what its build resolved, by digest."""
    uri = f"pkg:docker/mirror.gcr.io/library/python@3.13.16-slim?platform=linux%2F{arch}"
    return {
        "buildDefinition": {
            "resolvedDependencies": [
                {"uri": uri, "digest": {"sha256": _base(arch).removeprefix("sha256:")}},
                {
                    "uri": "https://github.com/PersonalClaw/PersonalClaw.git#main",
                    "digest": {"sha1": "c" * 40},
                },
            ]
        }
    }


def _layout(
    tmp_path: Path,
    *,
    platforms=("amd64", "arm64"),
    sbom_for=("amd64", "arm64"),
    spdx=_spdx,
    provenance_for=("amd64", "arm64"),
    provenance=_provenance,
    slsa=oci_layout.SLSA_PREDICATES[0],
    indexes=1,
) -> Path:
    """A layout tar: one multi-platform index with an image manifest and an attestation per arch."""
    blobs: dict[str, bytes] = {}

    def put(data: bytes) -> str:
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        blobs[digest] = data
        return digest

    descriptors = []
    for arch in platforms:
        image = put(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "mediaType": MANIFEST,
                    "layers": [],
                    "annotations": {"arch": arch},
                }
            ).encode()
        )
        descriptors.append(
            {
                "mediaType": MANIFEST,
                "digest": image,
                "size": 1,
                "platform": {"os": "linux", "architecture": arch},
            }
        )
        layers = []
        if arch in sbom_for:
            statement = put(
                json.dumps(
                    {"predicateType": oci_layout.SPDX_PREDICATE, "predicate": spdx(arch)}
                ).encode()
            )
            layers.append(
                {
                    "mediaType": "application/vnd.in-toto+json",
                    "digest": statement,
                    "annotations": {oci_layout.PREDICATE_TYPE: oci_layout.SPDX_PREDICATE},
                }
            )
        if arch in provenance_for:
            statement = put(
                json.dumps({"predicateType": slsa, "predicate": provenance(arch)}).encode()
            )
            layers.append(
                {
                    "mediaType": "application/vnd.in-toto+json",
                    "digest": statement,
                    "annotations": {oci_layout.PREDICATE_TYPE: slsa},
                }
            )
        attestation = put(
            json.dumps({"schemaVersion": 2, "mediaType": MANIFEST, "layers": layers}).encode()
        )
        descriptors.append(
            {
                "mediaType": MANIFEST,
                "digest": attestation,
                "size": 1,
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {
                    oci_layout.ATTESTATION_TYPE[0]: oci_layout.ATTESTATION_TYPE[1],
                    oci_layout.ATTESTS: image,
                },
            }
        )
    index_bytes = json.dumps(
        {"schemaVersion": 2, "mediaType": INDEX, "manifests": descriptors}
    ).encode()
    index = put(index_bytes)
    top = {
        "schemaVersion": 2,
        "manifests": [{"mediaType": INDEX, "digest": index, "size": len(index_bytes)}] * indexes,
    }
    path = tmp_path / "layout.tar"
    with tarfile.open(path, "w") as tar:
        for name, data in [
            ("index.json", json.dumps(top).encode()),
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
        ] + [(f"blobs/sha256/{d.removeprefix('sha256:')}", b) for d, b in blobs.items()]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_the_index_is_the_bytes_a_registry_would_store_and_each_platform_has_its_sbom(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    written = oci_layout.write(_layout(tmp_path), "personalclaw-gateway", out)
    assert [p.name for p in written] == [
        "personalclaw-gateway.index.json",
        "personalclaw-gateway-amd64.spdx.json",
        "personalclaw-gateway-arm64.spdx.json",
        "personalclaw-gateway-amd64.bases.json",
        "personalclaw-gateway-arm64.bases.json",
    ]
    # Each platform's bases are the images its build resolved, by digest, as BuildKit's
    # provenance names them; the source repository it read is not an image and is not one.
    assert json.loads((out / "personalclaw-gateway-arm64.bases.json").read_text()) == [
        {
            "image": "pkg:docker/mirror.gcr.io/library/python@3.13.16-slim?platform=linux%2Farm64",
            "digest": _base("arm64"),
        }
    ]
    index = json.loads((out / "personalclaw-gateway.index.json").read_bytes())
    assert index["mediaType"] == INDEX
    assert (
        json.loads((out / "personalclaw-gateway-arm64.spdx.json").read_text())["name"]
        == "personalclaw-gateway-arm64"
    )


def test_the_index_it_writes_is_byte_for_byte_the_blob(tmp_path: Path) -> None:
    layout = oci_layout.Layout(_layout(tmp_path))
    data = oci_layout.image_index(layout)
    top = json.loads(layout.file("index.json"))
    assert f"sha256:{hashlib.sha256(data).hexdigest()}" == top["manifests"][0]["digest"]


@pytest.mark.parametrize(
    ("plant", "said"),
    [
        ({"sbom_for": ("amd64",)}, "linux/arm64: missing SPDX SBOM attestation"),
        (
            {"platforms": ("amd64",), "sbom_for": ("amd64",)},
            "the image's index has no linux/arm64 manifest",
        ),
        ({"spdx": lambda arch: {"SPDXID": "something-else"}}, "malformed SPDX document"),
        ({"indexes": 2}, "names 2 multi-platform indexes, not one"),
        ({"provenance_for": ("amd64",)}, "linux/arm64: missing SLSA provenance attestation"),
        (
            {"provenance": lambda arch: {"buildDefinition": {"resolvedDependencies": []}}},
            "linux/amd64: the provenance names no base image",
        ),
        (
            {
                "provenance": lambda arch: {
                    "buildDefinition": {
                        "resolvedDependencies": [{"uri": "pkg:docker/x@1", "digest": {}}]
                    }
                }
            },
            "linux/amd64: pkg:docker/x@1 has no sha256 digest",
        ),
    ],
)
def test_what_the_release_reads_and_the_layout_lacks_fails_it(
    tmp_path: Path, plant: dict, said: str
) -> None:
    with pytest.raises(oci_layout.LayoutError, match=said):
        oci_layout.write(_layout(tmp_path, **plant), "personalclaw-gateway", tmp_path / "out")


def test_the_script_exits_1_naming_the_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path, sbom_for=())
    assert (
        oci_layout.main([str(layout), "--name", "personalclaw-web", "--out", str(tmp_path / "out")])
        == 1
    )
    assert "missing SPDX SBOM attestation" in capsys.readouterr().err
    assert oci_layout.main([str(layout)]) == 2, "a usage error is input it cannot rely on"


def test_slsa_v0_2_provenance_names_its_bases_as_materials(tmp_path: Path) -> None:
    """BuildKit wrote v0.2 before v1; a builder that still does is read the same way."""

    def materials(arch: str) -> dict:
        return {"materials": _provenance(arch)["buildDefinition"]["resolvedDependencies"]}

    out = tmp_path / "out"
    layout = _layout(tmp_path, provenance=materials, slsa=oci_layout.SLSA_PREDICATES[1])
    oci_layout.write(layout, "personalclaw-gateway", out)
    bases = json.loads((out / "personalclaw-gateway-amd64.bases.json").read_text())
    assert [b["digest"] for b in bases] == [_base("amd64")]
