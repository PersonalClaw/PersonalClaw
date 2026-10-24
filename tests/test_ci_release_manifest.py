"""``tools/ci/release_manifest.py``: the release's digest-bound manifest, and every way it refuses.

A release builds every artifact, smokes it, and records it in one manifest before anything
publishes (#3911). The manifest names each staged file's sha256 and size, each image's
per-platform and index digests, and its own digest. A folder is verified against it, naming every
missing, extra or changed artifact, and an already-published target is compared with it before
any write: absent means create, identical means resume, different fails.

Each refusal row plants one way a manifest, a folder, an image index or a target falls short and
requires the refusal to name it; each has a green control beside it, so a module that refuses
everything cannot pass either.

Security-marked: this is a fail-closed boundary (rule 2 in ``docs/maintainers/testing.md``).
Files in ``tmp_path``, and one fresh interpreter that runs the script the way a trusted job does.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from table_rows import row

from tools.ci import __main__ as ci
from tools.ci import release_manifest as rm

pytestmark = pytest.mark.security

COMMIT = "c" * 40
REF = "refs/tags/v0.3.0"
GATEWAY = "ghcr.io/personalclaw/personalclaw-gateway"
WEB = "ghcr.io/personalclaw/personalclaw-web"

OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_LIST = "application/vnd.docker.distribution.manifest.list.v2+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"

STAGED = {
    "dist/personalclaw-0.3.0-py3-none-any.whl": b"wheel bytes",
    "dist/personalclaw-0.3.0.tar.gz": b"sdist bytes",
    "desktop/PersonalClaw-0.3.0.AppImage": b"appimage bytes",
    "release-notes.md": b"## 0.3.0\n",
}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _job(name: str, artifacts: list[str], *, tools: bool = True, bases: int = 0) -> dict:
    """One build, as tools/ci/build_identity.py collects it from a record and a set-up log."""
    return {
        "job": name,
        "runner": {
            "image": "ubuntu-24.04",
            "version": "20261012.334.1",
            "image_os": "ubuntu24",
            "image_version": "20261012.334.1",
        },
        "actions": [
            {"action": "actions/checkout", "ref": "a" * 40, "sha": "a" * 40},
            {"action": "someone/nested", "ref": "v1", "sha": "b" * 40},
        ],
        "toolchain": {
            tool: (
                {"python": "3.12.15", "uv": "uv 0.13.1", "node": "v24.22.0", "npm": "11.6.2"}[tool]
                if tools
                else None
            )
            for tool in rm.TOOLS
        },
        "bases": [
            {
                "image": f"pkg:docker/python@3.13?platform=linux%2F{arch}",
                "digest": _sha(arch.encode()),
            }
            for arch in ("amd64", "arm64")[:bases]
        ],
        "artifacts": artifacts,
    }


#: What built STAGED and the gateway image: one job per artifact kind, each artifact once.
BUILDS = [
    _job(
        "artifacts / build",
        [
            "dist/personalclaw-0.3.0-py3-none-any.whl",
            "dist/personalclaw-0.3.0.tar.gz",
            "release-notes.md",
        ],
    ),
    _job("artifacts / desktop-linux", ["desktop/PersonalClaw-0.3.0.AppImage"]),
    _job(
        "artifacts / images (gateway)",
        ["ghcr.io/personalclaw/personalclaw-gateway"],
        tools=False,
        bases=2,
    ),
]


def _descriptor(platform: str, seed: str, media_type: str = OCI_MANIFEST) -> dict:
    os_, _, arch = platform.partition("/")
    arch, _, variant = arch.partition("/")
    plat = {"os": os_, "architecture": arch, **({"variant": variant} if variant else {})}
    return {"mediaType": media_type, "digest": _sha(seed.encode()), "size": 1, "platform": plat}


def _attestation(of: str) -> dict:
    """The attestation manifest buildx adds beside each platform's, which carries no platform."""
    return {
        "mediaType": OCI_MANIFEST,
        "digest": _sha(f"attestation {of}".encode()),
        "size": 1,
        "annotations": {
            "vnd.docker.reference.digest": _sha(of.encode()),
            "vnd.docker.reference.type": "attestation-manifest",
        },
        "platform": {"architecture": "unknown", "os": "unknown"},
    }


def _index(*manifests: dict, media_type: str | None = OCI_INDEX) -> bytes:
    body: dict = {"schemaVersion": 2, "manifests": list(manifests)}
    if media_type is not None:
        body["mediaType"] = media_type
    return json.dumps(body, indent=2).encode()


def _gateway_index(seed: str = "gateway") -> bytes:
    return _index(
        _descriptor("linux/amd64", f"{seed} amd64"),
        _attestation(f"{seed} amd64"),
        _descriptor("linux/arm64", f"{seed} arm64"),
        _attestation(f"{seed} arm64"),
    )


def _stage(folder: Path, files: dict[str, bytes] = STAGED) -> Path:
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return folder


def _manifest(tmp_path: Path) -> tuple[dict, bytes]:
    staged = _stage(tmp_path / "staged")
    images = {GATEWAY: rm.image_from_index(GATEWAY, _gateway_index())}
    data = rm.canonical(rm.build(rm.staged(staged), images, ref=REF, commit=COMMIT, builds=BUILDS))
    return rm.load(data), data


# ── writing ────────────────────────────────────────────────────────────────────────────────────


def test_a_manifest_names_every_staged_file_and_image_under_its_own_digest(tmp_path: Path) -> None:
    manifest, data = _manifest(tmp_path)
    assert set(manifest) == {"version", "source", "files", "images", "builds", "digest"}
    assert manifest["builds"] == BUILDS
    assert manifest["version"] == rm.VERSION
    assert manifest["source"] == {"ref": REF, "commit": COMMIT}
    assert manifest["files"] == {
        name: {"digest": _sha(body), "size": len(body)} for name, body in STAGED.items()
    }
    # The index digest is the sha256 of the index's own bytes, as the registry names it; each
    # platform's is read out of the index, and the attestation manifests are not platforms.
    assert manifest["images"] == {
        GATEWAY: {
            "index": _sha(_gateway_index()),
            "platforms": {
                "linux/amd64": _sha(b"gateway amd64"),
                "linux/arm64": _sha(b"gateway arm64"),
            },
        }
    }
    body = {k: v for k, v in manifest.items() if k != "digest"}
    assert manifest["digest"] == _sha(rm.canonical(body))
    assert rm.load(data, expected=manifest["digest"]) == manifest


def test_the_byte_form_is_canonical(tmp_path: Path) -> None:
    _, data = _manifest(tmp_path)
    manifest = json.loads(data)
    shuffled = json.loads(json.dumps(dict(reversed(list(manifest.items())))))
    assert rm.canonical(shuffled) == data
    assert data == json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def test_a_docker_manifest_list_is_read_like_an_oci_index() -> None:
    data = _index(
        _descriptor("linux/amd64", "a", DOCKER_MANIFEST),
        _descriptor("linux/arm/v7", "b", DOCKER_MANIFEST),
        media_type=DOCKER_LIST,
    )
    image = rm.image_from_index(WEB, data)
    assert image.index == _sha(data)
    assert dict(image.platforms) == {"linux/amd64": _sha(b"a"), "linux/arm/v7": _sha(b"b")}


def _refusing_indexes() -> list[tuple[str, bytes, str]]:
    amd64 = _descriptor("linux/amd64", "a")
    no_platform = {k: v for k, v in amd64.items() if k != "platform"}
    unknown = {**amd64, "platform": {"os": "unknown", "architecture": "unknown"}}
    nested = _descriptor("linux/arm64", "b", OCI_INDEX)
    bad_digest = {**amd64, "digest": "sha256:" + "A" * 64}

    def platform(**fields: object) -> bytes:
        return _index({**amd64, "platform": fields})

    return [
        ("not JSON", b"not json", "not JSON"),
        ("a single manifest", _index(amd64, media_type=OCI_MANIFEST), "not an image index"),
        ("no media type", _index(amd64, media_type=None), "not an image index"),
        (
            "another schema",
            _index(amd64).replace(b'"schemaVersion": 2', b'"schemaVersion": 1'),
            "schema",
        ),
        ("a manifest with no platform", _index(amd64, no_platform), "no platform"),
        ("an unknown platform that is no attestation", _index(amd64, unknown), "unknown/unknown"),
        ("a platform twice", _index(amd64, _descriptor("linux/amd64", "c")), "twice"),
        ("only attestations", _index(_attestation("a")), "no platform image"),
        ("a nested index", _index(amd64, nested), "not an image manifest"),
        ("a malformed digest", _index(bad_digest), "digest"),
        # The first of a repeated key would be read past: here, an empty list of manifests.
        ("a key twice", b'{"manifests": [],' + _index(amd64)[1:], "^key 'manifests' twice$"),
        ("an array", b"[]", "the index is not an object"),
        (
            "no manifests",
            json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX}).encode(),
            "names no manifests",
        ),
        (
            "a descriptor that is a string",
            json.dumps({"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": ["a"]}).encode(),
            "descriptor is not an object",
        ),
        # A field that is no string is refused as the platform it came in, never spelled out.
        ("an os that is a number", platform(os=1, architecture="amd64"), r"names platform \{"),
        (
            "an os that is NaN",
            platform(os="nan", architecture="amd64").replace(b'"nan"', b"NaN"),
            r"names platform \{",
        ),
        (
            "a number beside a variant",
            platform(os=1, architecture="linux", variant="amd64"),
            r"names platform \{",
        ),
        ("no os", platform(architecture="amd64"), r"names platform \{"),
        ("an uppercase os", platform(os="Linux", architecture="amd64"), "'Linux/amd64'"),
        (
            "an integer too long to read",
            _index(amd64).replace(b'"size": 1', b'"size": ' + b"9" * 5000),
            "not JSON",
        ),
        ("nested too deeply", b"[" * 100_000 + b"]" * 100_000, "nested too deeply"),
    ]


def test_an_image_index_that_cannot_name_its_platforms_is_refused(subtests: object) -> None:
    rows = _refusing_indexes()
    assert len(rows) == 21, "a refusal row was dropped"
    for name, data, reason in rows:
        with row(subtests, case=name):
            with pytest.raises(rm.Refused, match=reason):
                rm.image_from_index(GATEWAY, data)
    with row(subtests, case="an image name that is not a repository"):
        with pytest.raises(rm.Refused, match="not an image repository"):
            rm.image_from_index(GATEWAY.upper(), _gateway_index())
    assert rm.image_from_index(GATEWAY, _gateway_index()).platforms  # the control


def test_only_an_attestation_is_left_out_of_the_platforms() -> None:
    """An ordinary annotation, or a reference of another type, does not make a descriptor an
    attestation: each is a platform the manifest records."""
    annotated = {
        **_descriptor("linux/amd64", "a"),
        "annotations": {"org.opencontainers.image.created": "2026-10-19T00:00:00Z"},
    }
    referenced = {
        **_descriptor("linux/arm64", "b"),
        "annotations": {"vnd.docker.reference.type": "another-type"},
    }
    image = rm.image_from_index(GATEWAY, _index(annotated, referenced, _attestation("a")))
    assert dict(image.platforms) == {"linux/amd64": _sha(b"a"), "linux/arm64": _sha(b"b")}


def _unreadable(path: Path, locked: list[Path], mode: int = 0) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads a file or folder whatever its mode")
    path.chmod(mode)
    locked.append(path)


def test_staging_refuses_what_a_manifest_cannot_hold(tmp_path: Path, subtests: object) -> None:
    def symlinked_file(folder: Path) -> None:
        (folder / "dist").mkdir()
        (folder / "dist" / "link.whl").symlink_to(tmp_path / "elsewhere")

    def symlinked_folder(folder: Path) -> None:
        (folder / "dist").symlink_to(tmp_path, target_is_directory=True)

    def fifo(folder: Path) -> None:
        os.mkfifo(folder / "pipe")

    locked: list[Path] = []

    def unreadable_file(folder: Path) -> None:
        (folder / "secret.whl").write_bytes(b"secret")
        _unreadable(folder / "secret.whl", locked)

    def unreadable_folder(folder: Path) -> None:
        _stage(folder, {"dist/secret.whl": b"secret"})
        _unreadable(folder / "dist", locked)

    def unsearchable_folder(folder: Path) -> None:
        # Its names can be listed, but not one of them looked up.
        _stage(folder, {"dist/secret.whl": b"secret"})
        _unreadable(folder / "dist", locked, mode=0o400)

    rows = [
        ("an empty folder", lambda folder: None, "no files"),
        ("a symlinked file", symlinked_file, "dist/link.whl: not a regular file"),
        ("a symlinked folder", symlinked_folder, "dist: not a regular file"),
        ("a named pipe", fifo, "pipe: not a regular file"),
        ("a hidden file", lambda folder: (folder / ".DS_Store").write_bytes(b""), ".DS_Store"),
        ("a name with a space", lambda folder: (folder / "a b.whl").write_bytes(b""), "a b.whl"),
        ("a name with a newline", lambda folder: (folder / "a\nb").write_bytes(b""), "a\\nb"),
        ("an empty subfolder", lambda folder: (folder / "empty").mkdir(), "empty: an empty folder"),
        (
            "a hidden subfolder",
            lambda folder: _stage(folder, {".hidden/notes.md": b"notes"}),
            "'.hidden': not a name",
        ),
        ("a subfolder with a space", lambda folder: (folder / "a b").mkdir(), "a b"),
        ("a file it cannot read", unreadable_file, "cannot read secret.whl"),
        ("a subfolder it cannot read", unreadable_folder, "cannot read .*dist"),
        ("a subfolder it cannot search", unsearchable_folder, "cannot read dist/secret.whl"),
    ]
    assert len(rows) == 13, "a refusal row was dropped"
    (tmp_path / "elsewhere").write_bytes(b"outside")
    try:
        for n, (name, plant, reason) in enumerate(rows):
            folder = tmp_path / f"staged-{n}"
            folder.mkdir()
            _stage(folder, {"release-notes.md": b"notes"} if name != "an empty folder" else {})
            with row(subtests, case=name):
                plant(folder)
                with pytest.raises(rm.Refused, match=reason.replace("\\", "\\\\")):
                    rm.staged(folder)
        link = tmp_path / "staged-link"
        link.symlink_to(_stage(tmp_path / "real"), target_is_directory=True)
        with row(subtests, case="a staged folder that is a symlink"):
            with pytest.raises(rm.Refused, match="staged-link: not a folder"):
                rm.staged(link)
        # The read itself refuses what an lstat let through and was swapped before the open.
        (tmp_path / "swapped").symlink_to(tmp_path / "elsewhere")
        os.mkfifo(tmp_path / "swapped-pipe")
        for name, reason in [("swapped", "cannot read"), ("swapped-pipe", "not a regular file")]:
            with row(subtests, case=f"{name} at the read"):
                with pytest.raises(rm.Refused, match=f"{name}: {reason}|{reason} {name}"):
                    rm._file_entry(str(tmp_path / name), name)
    finally:
        for path in locked:
            path.chmod(0o700)
    assert rm.staged(_stage(tmp_path / "control")).keys() == STAGED.keys()


# ── reading ────────────────────────────────────────────────────────────────────────────────────


def _refusing_manifests(manifest: dict, data: bytes) -> list[tuple[str, bytes, str | None, str]]:
    def remade(**changes: object) -> bytes:
        body = {k: v for k, v in {**manifest, **changes}.items() if k != "digest"}
        return rm.canonical({**body, "digest": _sha(rm.canonical(body))})

    wheel = "dist/personalclaw-0.3.0-py3-none-any.whl"
    files = manifest["files"]
    gateway = manifest["images"][GATEWAY]
    amd64 = gateway["platforms"]["linux/amd64"]

    def image(**changes: object) -> bytes:
        return remade(images={GATEWAY: {**gateway, **changes}})

    def file(**changes: object) -> bytes:
        return remade(files={wheel: {**files[wheel], **changes}})

    def source(**changes: object) -> bytes:
        return remade(source={"ref": REF, "commit": COMMIT, **changes})

    builds = manifest["builds"]
    first = builds[0]

    def build(at: int, **changes: object) -> bytes:
        return remade(builds=[{**b, **changes} if i == at else b for i, b in enumerate(builds)])

    return [
        ("a changed byte", data.replace(b"release-notes", b"release-notez"), None, "own digest"),
        ("another approved digest", data, _sha(b"another"), "not the approved manifest"),
        ("whitespace", data.replace(b",", b", ", 1), None, "canonical"),
        ("a key twice", data[:-1] + b',"version":2}', None, "twice"),
        ("not JSON", b"{", None, "not JSON"),
        ("another version", remade(version=1), None, "version 2"),
        ("a version that is a bool", remade(version=True), None, "version 2"),
        ("an unknown key", remade(signed=True), None, "signed"),
        ("no files", remade(files={}), None, "no files"),
        ("a path out of the folder", remade(files={"../x": files[wheel]}), None, "../x"),
        ("an absolute path", remade(files={"/etc/x": files[wheel]}), None, "/etc/x"),
        ("an empty component", remade(files={"dist//x": files[wheel]}), None, "dist//x"),
        ("a hidden file", remade(files={".x": files[wheel]}), None, ".x"),
        (
            "a bare hex digest",
            remade(files={wheel: {**files[wheel], "digest": "f" * 64}}),
            None,
            "digest",
        ),
        (
            "a size that is a bool",
            remade(files={wheel: {**files[wheel], "size": True}}),
            None,
            "size",
        ),
        ("a negative size", remade(files={wheel: {**files[wheel], "size": -1}}), None, "size"),
        ("an image with a tag", remade(images={GATEWAY + ":0.3.0": gateway}), None, ":0.3.0"),
        (
            "an image with no platforms",
            remade(images={GATEWAY: {**gateway, "platforms": {}}}),
            None,
            "platforms",
        ),
        ("a short commit", remade(source={"ref": REF, "commit": "abc"}), None, "commit"),
        ("a short ref", remade(source={"ref": "v0.3.0", "commit": COMMIT}), None, "ref"),
        ("an abbreviated commit", source(commit="abcdef0"), None, "commit"),
        ("a commit between the two lengths", source(commit="c" * 50), None, "commit"),
        ("an uppercase commit", source(commit="C" * 40), None, "commit"),
        ("a pull request ref", source(ref="refs/pull/1/merge"), None, "refs/pull/1/merge"),
        ("a source with an unknown key", source(signed=True), None, "the source: unknown.*signed"),
        ("a source with no commit", remade(source={"ref": REF}), None, "missing.*commit"),
        ("files that are a list", remade(files=[files[wheel]]), None, "files are not an object"),
        ("a file with an unknown key", file(signed=True), None, f"{wheel}: unknown.*signed"),
        ("a file with no size", remade(files={wheel: {"digest": amd64}}), None, "missing.*size"),
        ("a path that reads as an option", remade(files={"-x": files[wheel]}), None, "'-x'"),
        ("images that are a list", remade(images=[gateway]), None, "images are not an object"),
        ("an image with an unknown key", image(signed=True), None, "unknown.*signed"),
        ("an image index that is no digest", image(index="latest"), None, "'latest'"),
        ("an uppercase image name", remade(images={GATEWAY.upper(): gateway}), None, "GHCR"),
        ("an uppercase platform", image(platforms={"Linux/AMD64": amd64}), None, "Linux/AMD64"),
        (
            "a platform digest in bare hex",
            image(platforms={"linux/amd64": amd64.removeprefix("sha256:")}),
            None,
            "linux/amd64: digest",
        ),
        (
            "a lone surrogate",
            data.replace(b'"release-notes.md"', b'"release-notes\\ud800.md"'),
            None,
            "UTF-8",
        ),
        (
            "an integer too long to read",
            data.replace(b'"version":2', b'"version":' + b"2" * 5000),
            None,
            "not JSON",
        ),
        ("nested too deeply", b"[" * 100_000 + b"]" * 100_000, None, "nested too deeply"),
        ("an array", b"[]", None, "the manifest is not an object"),
        (
            "no digest of its own",
            rm.canonical({k: v for k, v in manifest.items() if k != "digest"}),
            None,
            "digest None",
        ),
        ("a source that is a list", remade(source=[REF, COMMIT]), None, "source is not an object"),
        # What built each artifact (#3911's build identities, #4585's step 7).
        ("no builds", remade(builds=[]), None, "names no builds"),
        ("builds that are an object", remade(builds={}), None, "builds are not a list"),
        ("a build with an unknown key", build(0, signed=True), None, "unknown.*signed"),
        (
            "a build with no runner image version",
            build(0, runner={**first["runner"], "version": ""}),
            None,
            "artifacts / build: its runner image version is missing",
        ),
        (
            "a build with no image version of the runner's own",
            build(0, runner={**first["runner"], "image_version": " "}),
            None,
            "artifacts / build: its runner's own image version is missing",
        ),
        (
            "a runner named by its set-up log alone",
            build(0, runner={"image": "ubuntu-24.04", "version": "20261012.334.1"}),
            None,
            r"missing key\(s\) \['image_os', 'image_version'\]",
        ),
        (
            "a nested action with no SHA",
            build(0, actions=[*first["actions"][:1], {**first["actions"][1], "sha": ""}]),
            None,
            "someone/nested@v1 has no SHA",
        ),
        ("a build with no action", build(0, actions=[]), None, "names no action"),
        (
            "an image's build with no base image",
            build(2, bases=[]),
            None,
            "builds an image and names no base image",
        ),
        (
            "a base with no digest",
            build(2, bases=[{"image": "pkg:docker/python@3.13", "digest": "3.13"}]),
            None,
            "pkg:docker/python@3.13: digest",
        ),
        (
            "a file's build with no npm version",
            build(0, toolchain={**first["toolchain"], "npm": None}),
            None,
            "names no npm version",
        ),
        (
            "an artifact no build claims",
            build(0, artifacts=first["artifacts"][:2]),
            None,
            "release-notes.md: built by no build",
        ),
        (
            "an artifact two builds claim",
            build(1, artifacts=[*builds[1]["artifacts"], "release-notes.md"]),
            None,
            "release-notes.md: claimed by 2 builds",
        ),
        (
            "a build of what the manifest does not hold",
            build(1, artifacts=[*builds[1]["artifacts"], "elsewhere.deb"]),
            None,
            "'elsewhere.deb' is not an artifact of this manifest",
        ),
    ]


def test_a_manifest_that_is_not_the_approved_one_is_refused(
    tmp_path: Path, subtests: object
) -> None:
    manifest, data = _manifest(tmp_path)
    rows = _refusing_manifests(manifest, data)
    assert len(rows) == 56, "a refusal row was dropped"
    for name, bytes_, expected, reason in rows:
        with row(subtests, case=name):
            with pytest.raises(rm.Refused, match=reason):
                rm.load(bytes_, expected=expected)
    assert rm.load(data, expected=manifest["digest"]) == manifest  # the control

    # The writer holds the source to the reader's rule, so it never prints a digest of a
    # manifest no reader would load.
    files, images = manifest["files"], {GATEWAY: rm.image_from_index(GATEWAY, _gateway_index())}
    built = [
        ("a short ref", "v0.3.0", COMMIT),
        ("a pull request ref", "refs/pull/1/merge", COMMIT),
        ("an abbreviated commit", REF, "abcdef0"),
        ("an uppercase commit", REF, "C" * 40),
    ]
    for name, ref, commit in built:
        with row(subtests, case=f"built from {name}"):
            with pytest.raises(rm.Refused, match="ref|commit"):
                rm.build(files, images, ref=ref, commit=commit, builds=BUILDS)
    assert rm.build(files, images, ref=REF, commit=COMMIT, builds=BUILDS) == manifest  # control


# ── verifying a folder ─────────────────────────────────────────────────────────────────────────


def test_verify_names_every_missing_extra_and_changed_artifact(
    tmp_path: Path, subtests: object
) -> None:
    manifest, _ = _manifest(tmp_path)
    wheel = "dist/personalclaw-0.3.0-py3-none-any.whl"
    image = rm.image_from_index(GATEWAY, _gateway_index())
    other = rm.image_from_index(GATEWAY, _gateway_index("rebuilt"))
    web = rm.image_from_index(WEB, _index(_descriptor("linux/amd64", "web")))
    one_platform = rm.image_from_index(GATEWAY, _index(_descriptor("linux/amd64", "gateway amd64")))
    # The same platform manifests, and another provenance or SBOM beside one of them: only the
    # index digest covers it.
    attested = rm.image_from_index(
        GATEWAY,
        _index(
            _descriptor("linux/amd64", "gateway amd64"),
            _attestation("another gateway amd64"),
            _descriptor("linux/arm64", "gateway arm64"),
            _attestation("gateway arm64"),
        ),
    )
    assert dict(attested.platforms) == dict(image.platforms)
    rows: list[tuple[str, dict[str, bytes], dict, list[tuple[str, str]], str]] = [
        (
            "a missing file",
            {k: v for k, v in STAGED.items() if k != wheel},
            {GATEWAY: image},
            [("missing", wheel)],
            "not in the folder",
        ),
        (
            "an extra file",
            {**STAGED, "dist/extra.whl": b"x"},
            {GATEWAY: image},
            [("extra", "dist/extra.whl")],
            _sha(b"x"),
        ),
        (
            "a changed file",
            {**STAGED, wheel: b"wheel bytez"},
            {GATEWAY: image},
            [("changed", wheel)],
            _sha(b"wheel bytez"),
        ),
        ("a missing image", STAGED, {}, [("missing", f"image {GATEWAY}")], "not given"),
        (
            "an extra image",
            STAGED,
            {GATEWAY: image, WEB: web},
            [("extra", f"image {WEB}")],
            web.index,
        ),
        (
            "a rebuilt image",
            STAGED,
            {GATEWAY: other},
            [("changed", f"image {GATEWAY}")],
            f"linux/amd64 {_sha(b'rebuilt amd64')}",
        ),
        (
            "a dropped platform",
            STAGED,
            {GATEWAY: one_platform},
            [("changed", f"image {GATEWAY}")],
            "linux/arm64 absent",
        ),
        (
            "a swapped attestation",
            STAGED,
            {GATEWAY: attested},
            [("changed", f"image {GATEWAY}")],
            f"index {attested.index}",
        ),
    ]
    assert len(rows) == 8, "a row was dropped"
    for n, (name, files, images, want, says) in enumerate(rows):
        folder = _stage(tmp_path / f"verify-{n}", files)
        with row(subtests, case=name):
            got = rm.verify(manifest, rm.staged(folder), images)
            assert [(f.kind, f.artifact) for f in got] == want, f"{name}: {got}"
            assert all(says in f.detail for f in got), f"{name}: no finding says {says}: {got}"
    control = rm.verify(manifest, rm.staged(_stage(tmp_path / "control")), {GATEWAY: image})
    assert control == []


# ── comparing a published target ───────────────────────────────────────────────────────────────


def test_a_published_target_is_created_resumed_or_refused(tmp_path: Path, subtests: object) -> None:
    manifest, _ = _manifest(tmp_path)
    wheel, sdist = "personalclaw-0.3.0-py3-none-any.whl", "personalclaw-0.3.0.tar.gz"
    wheel_sha, sdist_sha = _sha(STAGED[f"dist/{wheel}"]), _sha(STAGED[f"dist/{sdist}"])
    index = _sha(_gateway_index())
    pypi = ["dist/personalclaw-*"]
    rows: list[
        tuple[str, list[str], dict[str, str], list[str], list[str], list[tuple[str, str]]]
    ] = [
        ("absent", pypi, {}, [wheel, sdist], [], []),
        ("identical", pypi, {wheel: wheel_sha, sdist: sdist_sha}, [], [wheel, sdist], []),
        ("half published", pypi, {wheel: wheel_sha}, [sdist], [wheel], []),
        # PyPI's JSON names a file's sha256 in bare hex.
        ("bare hex", pypi, {wheel: wheel_sha.removeprefix("sha256:")}, [sdist], [wheel], []),
        ("a different file", pypi, {wheel: _sha(b"other")}, [], [], [("differs", wheel)]),
        (
            "an unexpected file",
            pypi,
            {"personalclaw-0.3.0-py3-none-musllinux.whl": wheel_sha},
            [],
            [],
            [("unexpected", "personalclaw-0.3.0-py3-none-musllinux.whl")],
        ),
        ("an absent image tag", [GATEWAY], {}, [GATEWAY], [], []),
        ("an identical image tag", [GATEWAY], {GATEWAY: index}, [], [GATEWAY], []),
        (
            "a different image tag",
            [GATEWAY],
            {GATEWAY: _sha(b"other index")},
            [],
            [],
            [("differs", GATEWAY)],
        ),
    ]
    assert len(rows) == 9, "a row was dropped"
    for name, select, published, create, resume, refused in rows:
        with row(subtests, case=name):
            got = rm.compare(manifest, select, published)
            assert [(f.kind, f.artifact) for f in got.refused] == refused, f"{name}: {got}"
            if refused:
                # Nothing is to be written when anything differs: not even what was absent.
                assert got.create == () and got.resume == (), f"{name}: {got}"
            else:
                assert (list(got.create), list(got.resume)) == (create, resume), f"{name}: {got}"


def test_a_comparison_that_compares_nothing_or_cannot_tell_objects_apart_is_refused(
    tmp_path: Path, subtests: object
) -> None:
    manifest, _ = _manifest(tmp_path)
    twice = {**manifest["files"], "other/release-notes.md": manifest["files"]["release-notes.md"]}
    collides = {**manifest, "files": twice}
    rows: list[tuple[str, dict, list[str], dict, str]] = [
        ("no selection", manifest, [], {}, "selects nothing"),
        ("a selection matching nothing", manifest, ["dist/*.dmg"], {}, "selects nothing"),
        ("two files with one name", collides, ["*release-notes.md"], {}, "twice"),
        ("a published digest of another kind", manifest, ["dist/*"], {"x": "md5:00"}, "digest"),
        ("uppercase hex", manifest, ["dist/*"], {"x": "F" * 64}, "digest"),
    ]
    assert len(rows) == 5, "a refusal row was dropped"
    for name, against, select, published, reason in rows:
        with row(subtests, case=name):
            with pytest.raises(rm.Refused, match=reason):
                rm.compare(against, select, published)
    control = rm.compare(manifest, ["dist/*", "*release-notes.md"], {})
    assert control.create and not control.refused, control


# ── the command ────────────────────────────────────────────────────────────────────────────────


def _cli_inputs(tmp_path: Path) -> dict[str, Path]:
    staged = _stage(tmp_path / "staged")
    index = tmp_path / "gateway-index.json"
    index.write_bytes(_gateway_index())
    changed = _stage(tmp_path / "changed", {**STAGED, "release-notes.md": b"## 0.3.1\n"})
    published = tmp_path / "pypi.json"
    published.write_text(json.dumps({"personalclaw-0.3.0.tar.gz": _sha(b"rebuilt sdist")}))
    absent = tmp_path / "absent.json"
    absent.write_text("{}")
    builds = tmp_path / "builds.json"
    builds.write_text(json.dumps(BUILDS))
    (tmp_path / "builds-short.json").write_text(json.dumps(BUILDS[:2]))
    sdist = '"personalclaw-0.3.0.tar.gz": "' + _sha(STAGED["dist/personalclaw-0.3.0.tar.gz"]) + '"'
    rebuilt = '"personalclaw-0.3.0.tar.gz": "' + _sha(b"rebuilt sdist") + '"'
    inputs = {
        # Read last-one-wins, the identical second listing would hide the rebuilt first.
        "published-twice": "{" + rebuilt + ", " + sdist + "}",
        "published-list": "[]",
        "published-long": '{"personalclaw-0.3.0.tar.gz": ' + "9" * 5000 + "}",
        # A name no terminal can print, listed beside an identical sdist.
        "published-unprintable": "{" + sdist + ', "\\ud800": "' + _sha(b"x") + '"}',
        "index-long": _gateway_index().decode().replace('"size": 1', '"size": ' + "9" * 5000, 1),
    }
    for name, text in inputs.items():
        (tmp_path / f"{name}.json").write_text(text)
    (tmp_path / "into-staged").symlink_to(staged, target_is_directory=True)
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    return {
        "staged": staged,
        "index": index,
        "changed": changed,
        "published": published,
        "absent": absent,
        "builds": builds,
        "builds-short": tmp_path / "builds-short.json",
        "manifest": tmp_path / "manifest.json",
        "unreadable": _stage(tmp_path / "unreadable"),
        **{name: tmp_path / f"{name}.json" for name in inputs},
    }


def _run(
    script: list[str], args: list[str], cwd: Path, output: Path, summary: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
    env["GITHUB_OUTPUT"] = str(output)
    if summary is not None:
        env["GITHUB_STEP_SUMMARY"] = str(summary)
    return subprocess.run(
        [*script, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )


def test_the_commands_exit_0_on_a_match_1_on_a_difference_and_2_on_what_they_cannot_rely_on(
    tmp_path: Path, subtests: object
) -> None:
    """``python -m tools.ci release-manifest`` from the repository root, and the script alone in
    isolated mode (``python3 -I tools/ci/release_manifest.py``), the way a trusted job runs it."""
    paths = _cli_inputs(tmp_path)
    root = Path(rm.__file__).resolve().parents[2]
    module = [sys.executable, "-m", "tools.ci", "release-manifest"]
    output = tmp_path / "github-output"

    def write(
        staged: Path, out: Path | str, ref: str = REF, builds: Path | None = None
    ) -> list[str]:
        image = ["--image", f"{GATEWAY}={paths['index']}"]
        return ["write", "--staged", str(staged), *image, "--ref", ref, "--commit", COMMIT] + [
            "--builds",
            str(builds or paths["builds"]),
            "--out",
            str(out),
        ]

    written = _run(module, write(paths["staged"], paths["manifest"]), root, output)
    assert written.returncode == 0, written.stdout + written.stderr
    digest = rm.load(paths["manifest"].read_bytes())["digest"]
    assert f"digest={digest}" in output.read_text().splitlines()
    data = paths["manifest"].read_bytes()
    surrogate, long = tmp_path / "surrogate.json", tmp_path / "long.json"
    surrogate.write_bytes(data.replace(b'"release-notes.md"', b'"release-notes\\ud800.md"'))
    long.write_bytes(data.replace(b'"version":2', b'"version":' + b"2" * 5000))

    approved = ["--manifest", str(paths["manifest"]), "--digest", digest]
    image = ["--image", f"{GATEWAY}={paths['index']}"]
    staged = ["--staged", str(paths["staged"])]
    pypi = ["--select", "dist/personalclaw-*"]
    refused = tmp_path / "refused" / "manifest.json"  # no refused write may leave it

    def against(manifest: Path) -> list[str]:
        return ["verify", "--manifest", str(manifest), "--digest", digest, *staged, *image]

    def published(name: str) -> list[str]:
        return ["compare", *approved, *pypi, "--published", str(paths[name])]

    rows: list[tuple[str, list[str], int, str]] = [
        ("verify a match", ["verify", *approved, *staged, *image], 0, ""),
        (
            "verify a changed file",
            ["verify", *approved, "--staged", str(paths["changed"]), *image],
            1,
            "changed: release-notes.md",
        ),
        (
            "verify without the image",
            ["verify", *approved, *staged],
            1,
            f"missing: image {GATEWAY}",
        ),
        ("compare an absent target", published("absent"), 0, ""),
        ("compare a different target", published("published"), 1, "differs: personalclaw-0.3.0"),
        (
            "compare a target listing a name no terminal can print",
            published("published-unprintable"),
            1,
            "unexpected: \\ud800",
        ),
        (
            "another approved digest",
            [
                "verify",
                "--manifest",
                str(paths["manifest"]),
                "--digest",
                _sha(b"x"),
                *staged,
                *image,
            ],
            2,
            "not the approved manifest",
        ),
        (
            "an approved digest that is no digest",
            ["verify", "--manifest", str(paths["manifest"]), "--digest", digest.upper()]
            + [*staged, *image],
            2,
            "--digest: digest",
        ),
        ("an unreadable manifest", against(tmp_path / "none.json"), 2, "none.json"),
        ("a manifest with a lone surrogate", against(surrogate), 2, "UTF-8"),
        ("a manifest with an integer too long to read", against(long), 2, "not JSON"),
        (
            "an index with an integer too long to read",
            ["verify", *approved, *staged, "--image", f"{GATEWAY}={paths['index-long']}"],
            2,
            "not JSON",
        ),
        (
            "an image name that is not a repository",
            ["verify", *approved, *staged, "--image", f"{GATEWAY.upper()}={paths['index']}"],
            2,
            "not an image repository",
        ),
        (
            "an image with no index file",
            ["verify", *approved, *staged, "--image", GATEWAY],
            2,
            "NAME=INDEX_FILE",
        ),
        ("a target listing a name twice", published("published-twice"), 2, "twice"),
        ("a target that is a list", published("published-list"), 2, "not a JSON object"),
        ("a target with an integer too long to read", published("published-long"), 2, "not JSON"),
        ("a help flag on a match", ["verify", *approved, *staged, *image, "--help"], 2, "--help"),
        (
            "an abbreviated option",
            ["verify", "--manif", str(paths["manifest"]), "--digest", digest, *staged, *image],
            2,
            "--manifest",
        ),
        (
            "an option given twice",
            ["verify", *approved, "--digest", digest, *staged, *image],
            2,
            "twice",
        ),
        ("an image named twice", ["verify", *approved, *staged, *image, *image], 2, "twice"),
        (
            "a manifest written into its own folder",
            write(paths["staged"], paths["staged"] / "manifest.json"),
            2,
            "inside",
        ),
        (
            "a manifest written through a symlink into its own folder",
            write(paths["staged"], tmp_path / "into-staged" / "manifest.json"),
            2,
            "inside",
        ),
        (
            "a manifest written through a case variant of its own folder",
            write(paths["staged"], tmp_path / "STAGED" / "manifest.json"),
            2,
            "inside",
        ),
        (
            "a manifest written beside its folder through ..",
            write(paths["staged"], paths["staged"] / "dist" / ".." / ".." / "beside.json"),
            0,
            "### Release manifest sha256:",
        ),
        ("a manifest written over a folder", write(paths["staged"], tmp_path), 2, "cannot write"),
        ("a manifest written to no path", write(paths["staged"], ""), 2, "cannot write"),
        (
            "a manifest written under a file",
            write(paths["staged"], paths["absent"] / "manifest.json"),
            2,
            "cannot write",
        ),
        (
            "a manifest written through a symlink loop",
            write(paths["staged"], tmp_path / "loop" / "manifest.json"),
            2,
            "cannot write",
        ),
        (
            "a manifest of a short ref",
            write(paths["staged"], refused, ref="v0.3.0"),
            2,
            "not a full branch or tag ref",
        ),
        ("a staged file it cannot read", write(paths["unreadable"], refused), 2, "cannot read"),
        (
            "builds that are not a list",
            write(paths["staged"], refused, builds=paths["absent"]),
            2,
            "is not a JSON list of builds",
        ),
        (
            "builds that leave the gateway image to no job",
            write(paths["staged"], refused, builds=paths["builds-short"]),
            2,
            "personalclaw-gateway: built by no build",
        ),
        ("no verb", [], 2, "write"),
    ]
    locked: list[Path] = []

    def case_insensitive() -> None:
        if not (tmp_path / "STAGED").exists():
            pytest.skip("this disk tells STAGED from staged")

    setup = {
        "a manifest written through a case variant of its own folder": case_insensitive,
        "a staged file it cannot read": lambda: _unreadable(
            paths["unreadable"] / "release-notes.md", locked
        ),
    }
    assert len(rows) == 34, "a row was dropped"
    try:
        for name, args, code, says in rows:
            with row(subtests, case=name):
                setup.get(name, lambda: None)()
                output.write_text("")
                done = _run(module, args, root, output)
                assert (
                    done.returncode == code
                ), f"{name}: {done.returncode}\n{done.stdout}{done.stderr}"
                assert says in done.stdout + done.stderr, f"{name}: {done.stdout}{done.stderr}"
                assert "Traceback" not in done.stderr, f"{name}: {done.stderr}"
    finally:
        for path in locked:
            path.chmod(0o600)
    assert not refused.parent.exists(), "a refused write left a manifest"
    assert rm.staged(paths["staged"]).keys() == STAGED.keys(), "a manifest landed in its folder"

    # The step summary gets the findings, or the refusal, even one naming a path no encoding
    # can spell.
    summary = tmp_path / "summary.md"
    unspellable = os.fsdecode(os.fsencode(tmp_path) + b"/staged-\xff")
    summaries = [
        ("a match", ["verify", *approved, *staged, *image], 0, "### Matches release manifest"),
        (
            "a finding",
            ["verify", *approved, "--staged", str(paths["changed"]), *image],
            1,
            "- changed: release-notes.md",
        ),
        (
            "a refusal",
            ["verify", *approved, "--staged", unspellable, *image],
            2,
            "### Release manifest: refused",
        ),
    ]
    for name, args, code, says in summaries:
        with row(subtests, case=f"the step summary of {name}"):
            summary.write_text("")
            done = _run(module, args, root, output, summary)
            assert done.returncode == code, f"{name}: {done.returncode}\n{done.stdout}{done.stderr}"
            assert says in summary.read_text(encoding="utf-8"), f"{name}: {summary.read_text()}"

    output.write_text("")
    compared = _run(module, published("absent"), root, output)
    lines = output.read_text().splitlines()
    assert compared.returncode == 0 and "resume=[]" in lines, lines
    assert 'create=["personalclaw-0.3.0-py3-none-any.whl","personalclaw-0.3.0.tar.gz"]' in lines

    alone = _run(
        [sys.executable, "-I", rm.__file__],
        ["verify", *approved, *staged, *image],
        tmp_path,
        output,
    )
    assert alone.returncode == 0, alone.stdout + alone.stderr


def test_it_is_a_verb_of_python_m_tools_ci_and_imports_only_the_standard_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert ci.COMMANDS["release-manifest"] is rm.main
    # In the dispatcher's own process, a usage error is a refusal it returns, not a SystemExit
    # that escapes it.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert ci.main(["release-manifest", "verify", "--bogus"]) == 2
    tree = ast.parse(Path(rm.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    relative = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level]
    imported.discard("__future__")
    assert imported, "the scan read no imports"
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    assert not relative, "a trusted job runs the script alone, where a relative import fails"
