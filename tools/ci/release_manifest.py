"""The release manifest: every artifact a release built and smoked, in one digest-bound record.

A release builds and smokes every artifact before anything publishes, and records them in one
manifest (#3911): each staged file's sha256 and size, each image's per-platform digests and the
digest of its multi-platform index, the ref and commit the release was built from, what built
each artifact as it ran (``builds``, from ``tools/ci/build_identity.py``), and the manifest's own
digest, the sha256 of its canonical body (every other field, sorted keys, no
spaces, UTF-8). The approval names that digest, and every later step reads the manifest only
under it (:func:`load`): a manifest whose bytes are not canonical, that does not hash to its own
digest, or that is not the approved one is refused.

Three verbs of ``python -m tools.ci release-manifest`` (or ``python3 -I
tools/ci/release_manifest.py``, the way a trusted job runs the default branch's copy):

- ``write`` stages a folder, the images' raw index bytes and the builds into a manifest
  (:func:`staged`, :func:`image_from_index`, :func:`build`). Each build is one job's: the runner
  image its set-up log reported and the one the image itself names, every action it downloaded
  with its SHA, the base images that ran with their digests, the Python, uv, Node and npm
  versions it used, and the artifacts it built.
  Every file and image is one build's, and a build that lacks any of these is refused: an image's
  names its bases and may name no host tool, and a file's names all four tools. The folder holds
  only regular files and folders with plain names: a symlink, a special file, a file or folder it
  cannot read, an empty folder, a hidden name or a name with a space or a control character is
  refused, never skipped. It refuses to write the manifest inside that folder, by the folder's
  identity rather than the spelling of its path. An image's index digest is the sha256 of the index
  bytes the registry stores, and each platform's digest is read out of the index; the attestation
  manifests a BuildKit build adds beside each platform's are covered by the index digest and are not
  platforms.
- ``verify`` holds a folder and the images against the approved manifest, and names every
  missing, extra and changed artifact (:func:`verify`). It exits 1 on any.
- ``compare`` holds an already-published target against the approved manifest before anything is
  written to it (:func:`compare`). The target is what a registry lists: each object's name (a
  file's basename, or an image's name) and digest. An object the manifest selects and the target
  lacks is created, an identical one is resumed, and a different one, or one the target holds that
  the manifest does not select, fails, exit 1, with nothing to create or resume. A moving tag
  (``:latest``, ``:X.Y``) names an earlier release's digest by design; compare the tag that names
  this release only.

Every verb exits 2 on input it cannot rely on, a file it cannot read, JSON it cannot parse and a
path it cannot write included, and every usage error is that input: there is no ``-h``, since a
help page would exit 0 having checked nothing, and an option given twice is refused rather than
the last one winning. Outputs go to ``$GITHUB_OUTPUT`` and the findings to
``$GITHUB_STEP_SUMMARY`` when they are set.

Standard library only, with no relative imports, so a trusted job can run it as a script.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

VERSION = 2

KEYS = frozenset({"version", "source", "files", "images", "builds", "digest"})
FILE_KEYS = frozenset({"digest", "size"})
IMAGE_KEYS = frozenset({"index", "platforms"})
SOURCE_KEYS = frozenset({"ref", "commit"})
BUILD_KEYS = frozenset({"job", "runner", "actions", "toolchain", "bases", "artifacts"})
RUNNER_KEYS = frozenset({"image", "version", "image_os", "image_version"})
ACTION_KEYS = frozenset({"action", "ref", "sha"})
BASE_KEYS = frozenset({"image", "digest"})
#: The tools each build names: every one for a file's build; an image's build is its bases.
TOOLS = ("python", "uv", "node", "npm")

#: The image index media types a registry stores a multi-platform image under.
INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
#: The media types of the one-platform manifests an index may name.
MANIFEST_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
#: The annotation BuildKit marks an attestation manifest with, beside the platform it attests.
ATTESTATION = ("vnd.docker.reference.type", "attestation-manifest")

#: A digest as every field of the manifest spells it. Matched with ``fullmatch``.
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HEX = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_SHA1 = re.compile(r"[0-9a-f]{40}")
#: A branch or a tag, in full: a release is cut from one of the two.
_REF = re.compile(r"refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/+-]*")
#: One path component of a staged file: no hidden file, no space or control character, and none
#: that reads as an option.
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]*")
#: An image's repository, with its registry and no tag or digest.
_PART = r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
_IMAGE = re.compile(rf"{_PART}(?::[0-9]+)?(?:/{_PART})+")
_PLATFORM = re.compile(r"[a-z0-9]+/[a-z0-9_]+(?:/[a-z0-9]+)?")


class Refused(ValueError):
    """Input that cannot be relied on: not a manifest, not the approved one, or not stageable."""


@dataclass(frozen=True)
class Image:
    """One multi-platform image: its index digest, and each platform's manifest digest."""

    name: str
    index: str
    platforms: Mapping[str, str]


@dataclass(frozen=True)
class Finding:
    """One artifact that does not match the manifest: ``missing``, ``extra``, ``changed``,
    ``differs`` or ``unexpected``."""

    kind: str
    artifact: str
    detail: str


@dataclass(frozen=True)
class Comparison:
    """A target against the manifest: the objects to create, those already there to resume,
    and what refuses the whole target. When anything refuses, nothing is created or resumed."""

    create: tuple[str, ...]
    resume: tuple[str, ...]
    refused: tuple[Finding, ...]


# ── the byte form ─────────────────────────────────────────────────────────────────────────────


def canonical(value: Mapping[str, Any]) -> bytes:
    """The one byte form: sorted keys, no spaces, UTF-8."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    try:
        return text.encode()
    except UnicodeEncodeError:
        # JSON can escape a lone surrogate, which UTF-8 cannot encode: no byte form holds it.
        raise Refused("a string UTF-8 cannot encode (a lone surrogate)") from None


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _no_twice(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # JSON keeps only the last of a repeated key, so a second entry could hide the first.
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise Refused(f"key {key!r} twice")
        out[key] = value
    return out


def _json(data: bytes, what: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_twice)
    except Refused:
        raise
    except RecursionError:
        raise Refused(f"{what} is nested too deeply") from None
    except ValueError as exc:
        # Bytes that are not UTF-8, text that is not JSON, and an integer longer than the
        # interpreter converts.
        raise Refused(f"{what} is not JSON it can read: {exc}") from None


def _exact(value: Any, keys: frozenset[str], what: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise Refused(f"{what} is not an object")
    if set(value) != keys:
        unknown, missing = sorted(set(value) - keys), sorted(keys - set(value))
        raise Refused(f"{what}: unknown key(s) {unknown}, missing key(s) {missing}")
    return value


def _digest(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise Refused(f"{what}: digest {value!r} is not sha256:<64 lowercase hex>")
    return value


def _path(value: str) -> str:
    if not all(_COMPONENT.fullmatch(part) for part in value.split("/")):
        raise Refused(f"{value!r}: not a name the manifest can hold")
    return value


def _image_name(value: str) -> str:
    if not _IMAGE.fullmatch(value):
        raise Refused(f"{value!r}: not an image repository (a registry and path, no tag or digest)")
    return value


# ── writing ───────────────────────────────────────────────────────────────────────────────────


def staged(folder: Path) -> dict[str, dict[str, Any]]:
    """Every file under ``folder``, by its path relative to it: its sha256 and size.

    Only regular files are staged, and every file and folder has a plain name. Anything else is
    refused by name, never skipped: a release publishes what the folder holds, so nothing in it
    may go unrecorded, and an empty folder is nothing a manifest of files can record.
    """
    if folder.is_symlink() or not folder.is_dir():
        raise Refused(f"{folder}: not a folder")
    out: dict[str, dict[str, Any]] = {}

    def refuse(error: OSError) -> NoReturn:
        raise Refused(f"cannot read {error.filename}: {error.strerror}")

    for top, dirs, names in os.walk(folder, onerror=refuse):
        dirs.sort()
        if not dirs and not names and Path(top) != folder:
            raise Refused(f"{Path(top).relative_to(folder).as_posix()}: an empty folder")
        for name in dirs:
            rel = _path(Path(top, name).relative_to(folder).as_posix())
            if os.path.islink(os.path.join(top, name)):
                raise Refused(f"{rel}: not a regular file")
        for name in sorted(names):
            path = os.path.join(top, name)
            rel = _path(Path(path).relative_to(folder).as_posix())
            try:
                mode = os.lstat(path).st_mode
            except OSError as exc:
                raise Refused(f"cannot read {rel}: {exc.strerror}") from None
            if not stat.S_ISREG(mode):
                raise Refused(f"{rel}: not a regular file")
            out[rel] = _file_entry(path, rel)
    if not out:
        raise Refused(f"{folder}: no files to stage")
    return out


def _file_entry(path: str, rel: str) -> dict[str, Any]:
    # O_NOFOLLOW and the fstat close the gap between the lstat above and this read; O_NONBLOCK
    # keeps a special file swapped in meanwhile from blocking the open. A symlink swapped in
    # fails the open (ELOOP), and that, like a file it may not read, is a refusal.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise Refused(f"{rel}: not a regular file")
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise Refused(f"cannot read {rel}: {exc.strerror}") from None
    return {"digest": "sha256:" + digest, "size": info.st_size}


def image_from_index(name: str, data: bytes) -> Image:
    """The image whose multi-platform index is ``data``, the bytes the registry stores."""
    _image_name(name)
    index = _json(data, f"{name}'s index")
    if not isinstance(index, dict):
        raise Refused(f"{name}: the index is not an object")
    media_type = index.get("mediaType")
    if media_type not in INDEX_TYPES:
        raise Refused(f"{name}: not an image index (media type {media_type!r})")
    if index.get("schemaVersion") != 2:
        raise Refused(f"{name}: schema version {index.get('schemaVersion')!r}, not 2")
    manifests = index.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise Refused(f"{name}: the index names no manifests")
    platforms: dict[str, str] = {}
    for descriptor in manifests:
        if not isinstance(descriptor, dict):
            raise Refused(f"{name}: a manifest descriptor is not an object")
        digest = _digest(descriptor.get("digest"), f"{name}'s manifest")
        if descriptor.get("mediaType") not in MANIFEST_TYPES:
            raise Refused(f"{name}: {digest} is not an image manifest")
        annotations = descriptor.get("annotations")
        if isinstance(annotations, dict) and annotations.get(ATTESTATION[0]) == ATTESTATION[1]:
            continue
        platform = descriptor.get("platform")
        if not isinstance(platform, dict):
            raise Refused(f"{name}: {digest} has no platform")
        parts = [platform.get("os"), platform.get("architecture")]
        if platform.get("variant") is not None:
            parts.append(platform.get("variant"))
        strings = [part for part in parts if isinstance(part, str)]
        if len(strings) != len(parts):
            raise Refused(f"{name}: {digest} names platform {platform!r}")
        key = "/".join(strings)
        if key == "unknown/unknown":
            raise Refused(f"{name}: {digest} is for unknown/unknown and attests nothing")
        if not _PLATFORM.fullmatch(key):
            raise Refused(f"{name}: {digest} names platform {key!r}")
        if key in platforms:
            raise Refused(f"{name}: platform {key} twice")
        platforms[key] = digest
    if not platforms:
        raise Refused(f"{name}: the index names no platform image")
    return Image(name, sha256(data), platforms)


def build(
    files: Mapping[str, Mapping[str, Any]],
    images: Mapping[str, Image],
    *,
    ref: str,
    commit: str,
    builds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The manifest of ``files`` (:func:`staged`) and ``images``, built from ``ref`` at
    ``commit`` by ``builds``, with its own digest."""
    body = {
        "version": VERSION,
        "source": {"ref": ref, "commit": commit},
        "files": {path: dict(entry) for path, entry in files.items()},
        "images": {
            name: {"index": image.index, "platforms": dict(image.platforms)}
            for name, image in images.items()
        },
        "builds": [json.loads(json.dumps(b)) for b in builds],
    }
    _check(body)
    return {**body, "digest": sha256(canonical(body))}


# ── reading ───────────────────────────────────────────────────────────────────────────────────


def load(data: bytes, expected: str | None = None) -> dict[str, Any]:
    """The manifest ``data`` holds, if its bytes are canonical, it hashes to its own digest, it
    is a version 2 manifest and, when ``expected`` is given, it is the approved one."""
    manifest = _json(data, "the manifest")
    if not isinstance(manifest, dict):
        raise Refused("the manifest is not an object")
    if data != canonical(manifest):
        raise Refused("the manifest is not in its canonical form")
    own = _digest(manifest.get("digest"), "the manifest")
    body = {k: v for k, v in manifest.items() if k != "digest"}
    if sha256(canonical(body)) != own:
        raise Refused(f"the manifest does not hash to its own digest {own}")
    _check(body)
    if expected is not None and own != expected:
        raise Refused(f"the manifest {own} is not the approved manifest {expected}")
    return manifest


def _check(body: Mapping[str, Any]) -> None:
    if type(body.get("version")) is not int or body["version"] != VERSION:
        raise Refused(f"not a version {VERSION} release manifest")
    _exact(body, KEYS - {"digest"}, "the manifest")
    source = _exact(body["source"], SOURCE_KEYS, "the source")
    if not isinstance(source["ref"], str) or not _REF.fullmatch(source["ref"]):
        raise Refused(f"the source ref {source['ref']!r} is not a full branch or tag ref")
    if not isinstance(source["commit"], str) or not _COMMIT.fullmatch(source["commit"]):
        raise Refused(f"the source commit {source['commit']!r} is not a full commit id")
    files = body["files"]
    if not isinstance(files, dict):
        raise Refused("the manifest's files are not an object")
    if not files:
        raise Refused("the manifest names no files")
    for path, entry in files.items():
        _path(path)
        entry = _exact(entry, FILE_KEYS, path)
        _digest(entry["digest"], path)
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise Refused(f"{path}: size {entry['size']!r} is not a byte count")
    images = body["images"]
    if not isinstance(images, dict):
        raise Refused("the manifest's images are not an object")
    for name, entry in images.items():
        _image_name(name)
        entry = _exact(entry, IMAGE_KEYS, name)
        _digest(entry["index"], name)
        platforms = entry["platforms"]
        if not isinstance(platforms, dict) or not platforms:
            raise Refused(f"{name}: names no platforms")
        for key, digest in platforms.items():
            if not _PLATFORM.fullmatch(key):
                raise Refused(f"{name}: platform {key!r}")
            _digest(digest, f"{name} {key}")
    _check_builds(body["builds"], set(files), set(images))


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{what} is missing")
    return value


def _check_builds(builds: Any, files: set[str], images: set[str]) -> None:
    """Every build names what ran, and every file and image is exactly one build's."""
    if not isinstance(builds, list):
        raise Refused("the manifest's builds are not a list")
    if not builds:
        raise Refused("the manifest names no builds: what built its artifacts")
    claimed: dict[str, list[str]] = {}
    for entry in builds:
        if not isinstance(entry, dict):
            raise Refused(f"a build is not an object: {entry!r}")
        job = _text(entry.get("job"), "a build's job")
        entry = _exact(entry, BUILD_KEYS, job)
        runner = _exact(entry["runner"], RUNNER_KEYS, f"{job}'s runner")
        _text(runner["image"], f"{job}: its runner image")
        _text(runner["version"], f"{job}: its runner image version")
        _text(runner["image_os"], f"{job}: its runner's own image")
        _text(runner["image_version"], f"{job}: its runner's own image version")
        actions = entry["actions"]
        if not isinstance(actions, list) or not actions:
            raise Refused(f"{job}: names no action its set-up downloaded")
        for action in actions:
            action = _exact(action, ACTION_KEYS, f"{job}'s action")
            named = f"{action['action']}@{action['ref']}"
            if not isinstance(action["sha"], str) or not _SHA1.fullmatch(action["sha"]):
                raise Refused(f"{job}: {named} has no SHA it resolved to")
        artifacts = entry["artifacts"]
        if not isinstance(artifacts, list) or not artifacts:
            raise Refused(f"{job}: names no artifact it built")
        for artifact in artifacts:
            if artifact not in files and artifact not in images:
                raise Refused(f"{job}: {artifact!r} is not an artifact of this manifest")
            claimed.setdefault(artifact, []).append(job)
        bases = entry["bases"]
        if not isinstance(bases, list):
            raise Refused(f"{job}: its bases are not a list")
        for base in bases:
            base = _exact(base, BASE_KEYS, f"{job}'s base")
            _text(base["image"], f"{job}: a base's image")
            _digest(base["digest"], f"{job}: {base['image']}")
        tools = _exact(entry["toolchain"], frozenset(TOOLS), f"{job}'s toolchain")
        if any(a in images for a in artifacts):
            if not bases:
                raise Refused(f"{job} builds an image and names no base image it ran")
        for tool in TOOLS:
            version = tools[tool]
            if version is None and not any(a in images for a in artifacts):
                raise Refused(f"{job}: names no {tool} version")
            if version is not None:
                _text(version, f"{job}: its {tool} version")
    for artifact in sorted(files | images):
        jobs = claimed.get(artifact, [])
        if len(jobs) != 1:
            say = f"claimed by {len(jobs)} builds" if jobs else "built by no build"
            raise Refused(f"{artifact}: {say}; each artifact is one job's")


# ── verifying a folder ────────────────────────────────────────────────────────────────────────


def verify(
    manifest: Mapping[str, Any],
    files: Mapping[str, Mapping[str, Any]],
    images: Mapping[str, Image],
) -> list[Finding]:
    """Every staged file and image that does not match ``manifest``, files first, each by name.

    Empty when the folder (:func:`staged`) and the images are exactly the manifest's.
    """
    found: list[Finding] = []
    want_files = manifest["files"]
    for path in sorted(set(want_files) | set(files)):
        want, got = want_files.get(path), files.get(path)
        if got is None:
            found.append(Finding("missing", path, "in the manifest, not in the folder"))
        elif want is None:
            found.append(
                Finding("extra", path, f"in the folder, not in the manifest ({got['digest']})")
            )
        elif dict(got) != dict(want):
            found.append(
                Finding(
                    "changed",
                    path,
                    f"{got['digest']}, {got['size']} bytes, where the manifest has "
                    f"{want['digest']}, {want['size']} bytes",
                )
            )
    want_images = manifest["images"]
    for name in sorted(set(want_images) | set(images)):
        artifact = f"image {name}"
        want_image, got_image = want_images.get(name), images.get(name)
        if got_image is None:
            found.append(Finding("missing", artifact, "in the manifest, not given"))
        elif want_image is None:
            found.append(
                Finding("extra", artifact, f"given, not in the manifest ({got_image.index})")
            )
        else:
            changes = _image_changes(want_image, got_image)
            if changes:
                found.append(Finding("changed", artifact, "; ".join(changes)))
    return found


def _image_changes(want: Mapping[str, Any], got: Image) -> list[str]:
    changes = []
    if got.index != want["index"]:
        changes.append(f"index {got.index} where the manifest has {want['index']}")
    for key in sorted(set(want["platforms"]) | set(got.platforms)):
        have, should = got.platforms.get(key), want["platforms"].get(key)
        if have != should:
            changes.append(f"{key} {have or 'absent'} where the manifest has {should or 'none'}")
    return changes


# ── comparing a published target ──────────────────────────────────────────────────────────────


def compare(
    manifest: Mapping[str, Any],
    select: Sequence[str],
    published: Mapping[str, str],
) -> Comparison:
    """``published`` (object name to digest) against the manifest's artifacts that ``select``
    matches: files by path, named on the target by their basename; images by name, whose
    published digest is their index's.

    A selection that matches nothing, or that names two artifacts by one object name, is
    refused: it could compare nothing, or one object with two artifacts.
    """
    expected: dict[str, str] = {}

    def add(name: str, digest: str) -> None:
        if name in expected:
            raise Refused(f"{name} twice in the selection: the target cannot tell them apart")
        expected[name] = digest

    for path, entry in sorted(manifest["files"].items()):
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in select):
            add(path.rsplit("/", 1)[-1], entry["digest"])
    for name, entry in sorted(manifest["images"].items()):
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in select):
            add(name, entry["index"])
    if not expected:
        raise Refused(f"the selection {list(select)} selects nothing in the manifest")
    observed = {name: _published_digest(name, value) for name, value in published.items()}

    create, resume, refused = [], [], []
    for name, digest in sorted(expected.items()):
        there = observed.get(name)
        if there is None:
            create.append(name)
        elif there == digest:
            resume.append(name)
        else:
            refused.append(
                Finding("differs", name, f"published {there} where the manifest has {digest}")
            )
    for name in sorted(set(observed) - set(expected)):
        refused.append(
            Finding("unexpected", name, f"published {observed[name]}, not in the selection")
        )
    if refused:
        return Comparison((), (), tuple(refused))
    return Comparison(tuple(create), tuple(resume), ())


def _published_digest(name: str, value: Any) -> str:
    # A registry spells a digest its own way: PyPI's JSON names a file's sha256 in bare hex.
    if isinstance(value, str) and _HEX.fullmatch(value):
        return "sha256:" + value
    return _digest(value, f"published {name}")


# ── the command ───────────────────────────────────────────────────────────────────────────────


class _Parser(argparse.ArgumentParser):
    # A usage error is input this cannot rely on (exit 2), not a SystemExit that escapes
    # ``python -m tools.ci``'s dispatcher. Built with add_help=False, so no action exits 0.
    def error(self, message: str) -> NoReturn:
        raise Refused(message)


class _Once(argparse.Action):
    """A single-valued option. Given twice, it is refused rather than the last one winning."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[object] | None,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            raise argparse.ArgumentError(self, "given twice")
        setattr(namespace, self.dest, values)


def _parser(verb: str) -> _Parser:
    return _Parser(prog=f"release-manifest {verb}", add_help=False, allow_abbrev=False)


def _read(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise Refused(f"cannot read {path}: {exc.strerror or exc}") from None


def _images(pairs: Sequence[str]) -> dict[str, Image]:
    out: dict[str, Image] = {}
    for pair in pairs:
        name, sep, path = pair.partition("=")
        if not sep or not path:
            raise Refused(f"an image is NAME=INDEX_FILE, got {pair!r}")
        if name in out:
            raise Refused(f"image {name} named twice")
        out[name] = image_from_index(name, _read(path))
    return out


def _approved(args: argparse.Namespace) -> dict[str, Any]:
    _digest(args.digest, "--digest")
    return load(_read(args.manifest), expected=args.digest)


def _printable(text: str) -> str:
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _report(text: str) -> None:
    text = _printable(text)
    print(text, end="")
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)


def _output(**values: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.writelines(f"{key}={value}\n" for key, value in values.items())


def _findings(title: str, found: Sequence[Finding]) -> str:
    lines = [f"### {title}", ""] + [f"- {f.kind}: {f.artifact} ({f.detail})" for f in found]
    return "\n".join(lines) + "\n"


def _inside(path: Path, folder: Path) -> bool:
    # By identity, not by the spelling of the path: a symlink, or a case variant on a disk that
    # ignores case, names the folder by another path.
    want, resolved = folder.stat(), path.resolve()
    for parent in (resolved, *resolved.parents):
        try:
            info = parent.stat()
        except OSError:
            continue  # not there yet: the write creates it
        if (info.st_dev, info.st_ino) == (want.st_dev, want.st_ino):
            return True
    return False


def _write(argv: list[str]) -> int:
    parser = _parser("write")
    parser.add_argument("--staged", action=_Once, required=True)
    parser.add_argument("--image", action="append", default=[], help="NAME=INDEX_FILE")
    parser.add_argument("--ref", action=_Once, required=True, help="the ref built, in full")
    parser.add_argument("--commit", action=_Once, required=True)
    parser.add_argument("--builds", action=_Once, required=True, help="what built each artifact")
    parser.add_argument("--out", action=_Once, required=True)
    args = parser.parse_args(argv)
    folder, out = Path(args.staged), Path(args.out)
    files, images = staged(folder), _images(args.image)
    builds = _json(_read(args.builds), args.builds)
    if not isinstance(builds, list):
        raise Refused(f"{args.builds} is not a JSON list of builds")
    try:
        if _inside(out, folder):
            raise Refused(f"{args.out} is inside {args.staged}, the folder it describes")
        manifest = build(files, images, ref=args.ref, commit=args.commit, builds=builds)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(canonical(manifest))
    except (OSError, RuntimeError) as exc:  # RuntimeError: a symlink loop, before Python 3.13
        raise Refused(
            f"cannot write {args.out!r}: {getattr(exc, 'strerror', None) or exc}"
        ) from None
    _output(digest=manifest["digest"])
    _report(
        f"### Release manifest {manifest['digest']}\n\n"
        f"{len(manifest['files'])} file(s) and {len(manifest['images'])} image(s), built from "
        f"{args.ref} at {args.commit} by {len(manifest['builds'])} job(s).\n"
    )
    return 0


def _verify(argv: list[str]) -> int:
    parser = _parser("verify")
    parser.add_argument("--manifest", action=_Once, required=True)
    parser.add_argument("--digest", action=_Once, required=True, help="the approved digest")
    parser.add_argument("--staged", action=_Once, required=True)
    parser.add_argument("--image", action="append", default=[], help="NAME=INDEX_FILE")
    args = parser.parse_args(argv)
    manifest = _approved(args)
    found = verify(manifest, staged(Path(args.staged)), _images(args.image))
    if found:
        _report(_findings(f"Does not match release manifest {args.digest}", found))
        return 1
    _report(
        f"### Matches release manifest {args.digest}\n\n{len(manifest['files'])} file(s) and "
        f"{len(manifest['images'])} image(s).\n"
    )
    return 0


def _compare(argv: list[str]) -> int:
    parser = _parser("compare")
    parser.add_argument("--manifest", action=_Once, required=True)
    parser.add_argument("--digest", action=_Once, required=True, help="the approved digest")
    parser.add_argument("--select", action="append", default=[], help="a path or image glob")
    parser.add_argument("--published", action=_Once, required=True, help="a JSON name: digest")
    args = parser.parse_args(argv)
    manifest = _approved(args)
    published = _json(_read(args.published), args.published)
    if not isinstance(published, dict):
        raise Refused(f"{args.published} is not a JSON object of name to digest")
    got = compare(manifest, args.select, published)
    if got.refused:
        _report(_findings(f"The target differs from release manifest {args.digest}", got.refused))
        return 1
    _output(
        create=json.dumps(list(got.create), separators=(",", ":")),
        resume=json.dumps(list(got.resume), separators=(",", ":")),
    )
    _report(
        f"### The target matches release manifest {args.digest}\n\n"
        + "".join(f"- create: {name}\n" for name in got.create)
        + "".join(f"- resume: {name}\n" for name in got.resume)
    )
    return 0


VERBS = {"compare": _compare, "verify": _verify, "write": _write}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in VERBS:
        print(f"usage: release-manifest {{{','.join(sorted(VERBS))}}} ...", file=sys.stderr)
        return 2
    try:
        return VERBS[argv[0]](argv[1:])
    except Refused as exc:
        reason = _printable(str(exc))
        print(f"release-manifest {argv[0]}: refused: {reason}", file=sys.stderr)
        target = os.environ.get("GITHUB_STEP_SUMMARY")
        if target:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(f"### Release manifest: refused\n\n{reason}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
