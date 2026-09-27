#!/usr/bin/env python3
"""Every bundled asset has a recorded source and licence (``ASSET_LICENSES.json``).

The manifest is an explicit allowlist, not a generated inventory: a new media or font file,
any other binary, or any file under the named fixture and copied-static roots below fails
until a reviewer assigns it to a group. A group says where its files come from, at which
version, under which licence, who owns them, and which tracked notice travels with them.

* A **project-owned** group is MIT under ``LICENSE`` and lists its paths. Git already
  versions those bytes, so they carry no digest: a pin would change every time a screenshot
  is re-captured and prove nothing git does not.
* A **third-party** group names an ``https`` source, an upstream version, the upstream
  copyright lines and its own notice file, and pins every file's sha256. The pin is the
  point: it is what says these are the exact bytes of that upstream version, so an upgrade
  or a swap fails here until the record is updated. The notice must carry every copyright
  line verbatim, so the record and the text that ships beside the files cannot drift.

``--built-web`` also checks what ``npm run build`` emitted into ``web/dist``, by content
rather than by name, because Vite renames what it copies. Every font and binary there must
be byte-identical to a recorded file: a tracked asset (Vite copies ``web/public`` verbatim)
or an installed dependency file listed under ``built_web_groups`` (the KaTeX and editor
icon fonts Vite hashes out of ``node_modules``). ``--refresh`` rewrites the digests of
paths already listed; it never adds a path or a group.

Run from the repository root:

    python3 scripts/check_asset_licenses.py
    python3 scripts/check_asset_licenses.py --built-web     # after `npm run build`
    python3 scripts/check_asset_licenses.py --refresh --built-web

Standard library only, so it runs on a bare CI runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "ASSET_LICENSES.json"
BUILT_WEB_ROOT = "web/dist"
DEPENDENCY_ROOT = "node_modules"

_ASSET_SUFFIXES = frozenset(
    {
        ".aab",
        ".apk",
        ".appimage",
        ".avif",
        ".bin",
        ".bmp",
        ".db",
        ".deb",
        ".dmg",
        ".doc",
        ".docx",
        ".eot",
        ".exe",
        ".gif",
        ".gz",
        ".icns",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".msi",
        ".otf",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".rpm",
        ".sqlite",
        ".sqlite3",
        ".svg",
        ".tar",
        ".ttf",
        ".wasm",
        ".wav",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".xls",
        ".xlsx",
        ".zip",
    }
)

#: Every tracked file in these roots is part of a test fixture — textual inputs, expected
#: outputs and fixture-local notices included — so every one of them needs an owner.
_FIXTURE_PREFIXES = (
    "src/personalclaw/tests_fixtures/",
    "staged-repos/registry/fixtures/",
    "tests/fixtures/",
    "web/e2e/fixtures/",
)

#: Trees a product surface copies or serves verbatim.
_STATIC_PREFIXES = (
    "mobile/assets/",
    "mobile/store/",
    "mobile/www/",
    "web/e2e/__screenshots__/",
    "web/public/",
)

_EXACT_STATIC_FILES = frozenset(
    {
        "desktop/connectDialog.html",
        "desktop/icon.png",
        "desktop/loading.html",
    }
)

#: The notice files themselves: policy text, not assets.
_POLICY_FILES = frozenset({"web/public/THIRD_PARTY_NOTICES.txt"})

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_OWNERSHIP = frozenset({"project-owned", "third-party"})
_REPOSITORY_REVISION = "repository-revision"
_MANIFEST_FIELDS = frozenset({"schema_version", "about", "groups", "built_web_groups"})
_GROUP_FIELDS = frozenset(
    {
        "id",
        "source",
        "version",
        "license",
        "ownership",
        "required_notice",
        "copyright",
        "note",
        "files",
    }
)


def manifest_path(root: Path | None = None) -> Path:
    return (root or REPO_ROOT) / MANIFEST_NAME


def load_manifest(root: Path | None = None) -> dict[str, Any]:
    """The tracked policy. An unreadable policy is a hard failure, never an empty one."""
    return json.loads(manifest_path(root).read_text(encoding="utf-8"))


def tracked_files(root: Path | None = None) -> list[str]:
    """Exactly the paths a clone receives."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root or REPO_ROOT, capture_output=True, check=True
    ).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def _looks_binary(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as handle:
        return b"\0" in handle.read(8192)


def is_asset_path(path: str, root: Path | None = None) -> bool:
    """Whether a tracked path belongs to the population the manifest must cover."""
    if path in _POLICY_FILES:
        return False
    if PurePosixPath(path).suffix.lower() in _ASSET_SUFFIXES:
        return True
    if path in _EXACT_STATIC_FILES:
        return True
    if path.startswith(_FIXTURE_PREFIXES) or path.startswith(_STATIC_PREFIXES):
        return True
    return _looks_binary((root or REPO_ROOT) / path)


def asset_paths(root: Path | None = None, paths: list[str] | None = None) -> list[str]:
    """The sorted population the manifest must cover exactly."""
    root = root or REPO_ROOT
    paths = paths if paths is not None else tracked_files(root)
    return sorted(path for path in paths if is_asset_path(path, root))


def built_web_asset_paths(root: Path | None = None) -> list[str]:
    """Media, font and other binary files the web build emitted into ``web/dist``."""
    root = root or REPO_ROOT
    dist = root / BUILT_WEB_ROOT
    if not dist.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in dist.rglob("*")
        if path.is_file() and (path.suffix.lower() in _ASSET_SUFFIXES or _looks_binary(path))
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_relative_path(value: str) -> bool:
    parsed = PurePosixPath(value)
    return (
        bool(value)
        and not parsed.is_absolute()
        and "." not in parsed.parts
        and ".." not in parsed.parts
        and str(parsed) == value
    )


def _group_errors(group: dict[str, Any], group_id: str) -> list[str]:
    """What is wrong with one group's metadata, independent of the files on disk."""
    errors = []
    for field in sorted(set(group) - _GROUP_FIELDS):
        errors.append(f"group {group_id}: unknown field {field!r}")
    for field in ("source", "version", "license", "ownership", "required_notice"):
        if not isinstance(group.get(field), str) or not group[field].strip():
            errors.append(f"group {group_id}: {field} must be a non-empty string")
    if "note" in group and (not isinstance(group["note"], str) or not group["note"].strip()):
        errors.append(f"group {group_id}: note, when present, must be a non-empty string")
    ownership = group.get("ownership")
    notice = group.get("required_notice")
    if ownership not in _OWNERSHIP:
        errors.append(f"group {group_id}: ownership must be one of {sorted(_OWNERSHIP)}")
    if isinstance(notice, str) and notice and not _valid_relative_path(notice):
        errors.append(f"group {group_id}: required_notice is not a safe relative path")
    if ownership == "project-owned":
        if group.get("license") != "MIT":
            errors.append(f"group {group_id}: project-owned assets must use MIT")
        if notice != "LICENSE":
            errors.append(f"group {group_id}: project-owned assets must name LICENSE as notice")
        if group.get("version") != _REPOSITORY_REVISION:
            errors.append(
                f"group {group_id}: project-owned assets are versioned by git, "
                f"so their version is {_REPOSITORY_REVISION!r}"
            )
        if "copyright" in group:
            errors.append(f"group {group_id}: project-owned assets carry LICENSE's copyright")
        if not isinstance(group.get("files"), list):
            errors.append(f"group {group_id}: project-owned files must be a list of paths")
    elif ownership == "third-party":
        if not str(group.get("source", "")).startswith("https://"):
            errors.append(f"group {group_id}: third-party source must be an https URL")
        if group.get("version") == _REPOSITORY_REVISION:
            errors.append(f"group {group_id}: third-party assets need an external source version")
        if notice == "LICENSE":
            errors.append(f"group {group_id}: third-party assets need their own tracked notice")
        lines = group.get("copyright")
        if (
            not isinstance(lines, list)
            or not lines
            or not all(isinstance(line, str) and line.strip() for line in lines)
        ):
            errors.append(
                f"group {group_id}: third-party copyright must list the upstream copyright lines"
            )
        if not isinstance(group.get("files"), dict):
            errors.append(f"group {group_id}: third-party files must map each path to its sha256")
    return errors


def _flatten(
    manifest: dict[str, Any], group_field: str = "groups"
) -> tuple[dict[str, tuple[str | None, str]], list[str]]:
    """``path -> (pinned sha256 or None, group id)``, plus every shape violation.

    ``groups`` must hold at least one group. ``built_web_groups`` may be absent or empty: a
    build that emits nothing but copies of tracked assets has nothing to add.
    """
    found: dict[str, tuple[str | None, str]] = {}
    errors: list[str] = []
    groups = manifest.get(group_field, [] if group_field == "built_web_groups" else None)
    if not isinstance(groups, list):
        return {}, [f"manifest: {group_field} must be a list"]
    if not groups and group_field == "groups":
        return {}, ["manifest: groups must be a non-empty list"]

    seen_ids: set[str] = set()
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            errors.append(f"{group_field}[{index}]: must be an object")
            continue
        group_id = group.get("id")
        if not isinstance(group_id, str) or not group_id:
            errors.append(f"{group_field}[{index}]: id must be a non-empty string")
            group_id = f"{group_field}[{index}]"
        elif group_id in seen_ids:
            errors.append(f"group {group_id}: duplicate id")
        seen_ids.add(group_id)
        errors.extend(_group_errors(group, group_id))
        if group_field == "built_web_groups" and group.get("ownership") != "third-party":
            errors.append(
                f"group {group_id}: a built web group records third-party dependency files; "
                "a project-owned file is already covered by its tracked copy"
            )

        files = group.get("files")
        if isinstance(files, dict):
            entries: list[tuple[Any, Any]] = list(files.items())
        elif isinstance(files, list):
            entries = [(path, None) for path in files]
        else:
            continue
        if not entries:
            errors.append(f"group {group_id}: files must not be empty")
        for path, expected in entries:
            if not isinstance(path, str) or not _valid_relative_path(path):
                errors.append(f"group {group_id}: invalid asset path {path!r}")
                continue
            if group_field == "built_web_groups" and not path.startswith(f"{DEPENDENCY_ROOT}/"):
                errors.append(
                    f"group {group_id}: {path} is not an installed dependency file under "
                    f"{DEPENDENCY_ROOT}/"
                )
                continue
            if path in found:
                errors.append(f"asset {path}: mapped by both {found[path][1]} and {group_id}")
                continue
            if isinstance(files, dict) and (
                not isinstance(expected, str) or not _DIGEST_RE.fullmatch(expected)
            ):
                errors.append(f"asset {path}: sha256 must be 64 lowercase hex characters")
                continue
            found[path] = (expected, group_id)
    return found, errors


def _notice_errors(group: dict[str, Any], tracked: set[str], root: Path) -> list[str]:
    """The group's notice must be tracked, and must carry every upstream copyright line."""
    group_id = group.get("id", "<unnamed>")
    notice = group.get("required_notice")
    if not isinstance(notice, str) or not notice or not _valid_relative_path(notice):
        return []
    if notice not in tracked:
        return [f"group {group_id}: its notice is not tracked: {notice}"]
    lines = group.get("copyright")
    if not isinstance(lines, list):
        return []
    text = (root / notice).read_text(encoding="utf-8")
    return [
        f"group {group_id}: {notice} does not carry the copyright line {line!r}"
        for line in lines
        if isinstance(line, str) and line.strip() and line not in text
    ]


def _check_file(label: str, full: Path, pinned: str | None) -> list[str]:
    if full.is_symlink():
        return [f"{label}: a symlink is not an asset — commit the file it points at"]
    if not full.is_file():
        return [f"{label}: is not a regular file"]
    if pinned is not None and digest(full) != pinned:
        return [f"{label}: sha256 mismatch (manifest {pinned}, actual {digest(full)})"]
    return []


def _built_web_errors(
    root: Path,
    mapped: dict[str, tuple[str | None, str]],
    built_mapped: dict[str, tuple[str | None, str]],
) -> list[str]:
    """Every emitted font and binary is byte-identical to a file the record covers."""
    emitted = built_web_asset_paths(root)
    if not emitted:
        return ["the built web census is empty; run `npm run build` before --built-web"]
    errors: list[str] = []
    recorded: set[str] = set()
    for path in mapped:
        full = root / path
        if full.is_file() and not full.is_symlink():
            recorded.add(digest(full))
    for path, (pinned, _group_id) in built_mapped.items():
        full = root / path
        if not full.exists() and not full.is_symlink():
            errors.append(f"dependency file {path}: not installed; run `npm ci` first")
            continue
        found = _check_file(f"dependency file {path}", full, pinned)
        errors.extend(found)
        if not found:
            recorded.add(digest(full))
    for path in emitted:
        if digest(root / path) not in recorded:
            errors.append(f"built web asset {path}: matches no file recorded in {MANIFEST_NAME}")
    return errors


def violations(
    root: Path | None = None,
    manifest: dict[str, Any] | None = None,
    *,
    include_built_web: bool = False,
) -> list[str]:
    """Every coverage, metadata and digest failure, sorted. Empty means the record is whole."""
    root = root or REPO_ROOT
    manifest = manifest if manifest is not None else load_manifest(root)
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("manifest: schema_version must be 1")
    for field in sorted(set(manifest) - _MANIFEST_FIELDS):
        errors.append(f"manifest: unknown field {field!r}")

    mapped, shape_errors = _flatten(manifest)
    errors.extend(shape_errors)
    built_mapped, built_shape_errors = _flatten(manifest, "built_web_groups")
    errors.extend(built_shape_errors)

    tracked = set(tracked_files(root))
    expected = set(asset_paths(root, sorted(tracked)))

    for group_field in ("groups", "built_web_groups"):
        for group in manifest.get(group_field) or []:
            if isinstance(group, dict):
                errors.extend(_notice_errors(group, tracked, root))

    for path, (pinned, _group_id) in mapped.items():
        if path not in tracked:
            errors.append(f"asset {path}: listed in {MANIFEST_NAME} but not tracked")
            continue
        if path not in expected:
            errors.append(f"asset {path}: listed, but outside the asset census")
        errors.extend(_check_file(f"asset {path}", root / path, pinned))
    for path in sorted(expected - set(mapped)):
        errors.append(f"asset {path}: missing from {MANIFEST_NAME}")

    if include_built_web:
        errors.extend(_built_web_errors(root, mapped, built_mapped))
    return sorted(set(errors))


def refresh_digests(
    manifest: dict[str, Any], root: Path | None = None, *, include_built_web: bool = False
) -> None:
    """Re-pin the digests of paths already listed. Never adds a path or a group."""
    root = root or REPO_ROOT
    for group_field in ["groups", "built_web_groups"] if include_built_web else ["groups"]:
        for group in manifest.get(group_field) or []:
            files = group.get("files")
            if not isinstance(files, dict):
                continue
            for path in files:
                full = root / path
                if not full.is_file() or full.is_symlink():
                    raise ValueError(f"cannot refresh a non-file asset: {path}")
                files[path] = digest(full)
    manifest_path(root).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-pin digests of paths already listed; never discovers a new file",
    )
    parser.add_argument(
        "--built-web",
        action="store_true",
        help="also check the fonts and binaries `npm run build` emitted into web/dist",
    )
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest()
        if args.refresh:
            refresh_digests(manifest, include_built_web=args.built_web)
            manifest = load_manifest()
        found = violations(manifest=manifest, include_built_web=args.built_web)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"asset-licenses: FAIL ({exc})")
        return 1

    count = len(asset_paths())
    if not found:
        built = f", {len(built_web_asset_paths())} built web assets" if args.built_web else ""
        print(f"asset-licenses: PASS ({count} tracked assets{built}, every one recorded)")
        return 0
    print(f"asset-licenses: FAIL ({len(found)} violation(s), {count} tracked assets)")
    for line in found:
        print(f"  - {line}")
    print(
        f"Record a new file in {MANIFEST_NAME}: list its path in a project-owned group, or "
        "add it with its sha256 to a third-party group (--refresh re-pins listed paths)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
