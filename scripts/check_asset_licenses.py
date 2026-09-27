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

``--built-web`` checks the npm packages' notices as well (:func:`npm_notice_errors`). The
build's minifier strips every licence comment, so ``web/scripts/thirdPartyNotices.mjs``
writes ``THIRD_PARTY_NOTICES_NPM.txt`` into ``web/dist``, with the census it was written
from, read off the bundler's own module graph. Every script and stylesheet in ``web/dist``
must have a census row, every package must be the one ``package-lock.json`` installed at
that path, version and licence, and the notices must carry each package's section with the
full text of every licence file it ships, read here from ``node_modules``, or of its reviewed
record in ``web/npm-license-records.json``.

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
LOCKFILE = "package-lock.json"

#: What web/scripts/thirdPartyNotices.mjs writes into web/dist, and the reviewed records it reads.
NPM_NOTICES = "THIRD_PARTY_NOTICES_NPM.txt"
NPM_CENSUS = "THIRD_PARTY_NOTICES_NPM.json"
NPM_RECORDS = "web/npm-license-records.json"

#: The files a bundled package's code can land in.
_CODE_SUFFIXES = frozenset({".cjs", ".css", ".js", ".mjs"})

#: A section of the npm notices opens with a 78-character rule, its title, then ``Licence:``.
#: Keyed on all three, because a licence text can hold a rule of its own.
_NOTICE_SECTION = re.compile(r"\n={78}\n(?=[^\n]*\nLicence: )")

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


def normalize_licence_text(text: str) -> str:
    """A licence text in the form the notices carry it (``normalizeText`` in
    thirdPartyNotices.mjs): no byte-order mark, LF line endings, no trailing blanks on a line,
    no trailing blank lines."""
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[ \t]+$", "", text, flags=re.M).rstrip("\n")


def built_web_code_paths(root: Path | None = None) -> list[str]:
    """Every script and stylesheet the web build emitted, relative to ``web/dist``."""
    dist = (root or REPO_ROOT) / BUILT_WEB_ROOT
    if not dist.is_dir():
        return []
    return sorted(
        path.relative_to(dist).as_posix()
        for path in dist.rglob("*")
        if path.is_file() and path.suffix.lower() in _CODE_SUFFIXES
    )


def _notice_sections(text: str) -> tuple[dict[str, str], list[str]]:
    """Each section of the npm notices keyed by its ``Path:``, and any path given twice."""
    sections: dict[str, str] = {}
    twice: list[str] = []
    for section in _NOTICE_SECTION.split(text)[1:]:
        match = re.search(r"^Path: +(\S+)$", section, re.M)
        if match is None:
            continue
        if match.group(1) in sections:
            twice.append(match.group(1))
        sections[match.group(1)] = section
    return sections, twice


def _read_licence(path: Path) -> str | None:
    if not path.is_file():
        return None
    return normalize_licence_text(path.read_text(encoding="utf-8", errors="replace"))


def _npm_entry_errors(
    entry: dict[str, Any],
    section: str | None,
    lock: dict[str, Any],
    records: dict[str, Any],
    root: Path,
) -> list[str]:
    """One census package: installed as recorded, and its full licence text in its section."""
    path = entry.get("path")
    name, version, licence = entry.get("name"), entry.get("version"), entry.get("license")
    copied_into = entry.get("copied_into")
    label = f"{path} ({name} {version or 'copied into ' + str(copied_into)})"
    errors: list[str] = []
    if not all(isinstance(v, str) and v for v in (path, name, licence)):
        return [f"{NPM_CENSUS}: an entry needs a path, a name and a licence: {entry!r}"]
    licence_dir = entry.get("license_from") if copied_into else path
    installed = [licence_dir] + ([copied_into] if copied_into else [])
    for key in installed:
        if not isinstance(key, str) or not isinstance(lock.get(key), dict):
            errors.append(f"{label}: {key} is not a package package-lock.json installs")
    if errors:
        return errors
    if not copied_into and lock[path].get("version") != version:
        errors.append(
            f"{label}: package-lock.json installed {lock[path].get('version')} at {path}, "
            f"not {version}"
        )

    record_key = entry.get("record")
    record = records.get(record_key) if isinstance(record_key, str) else None
    if record_key is not None and not isinstance(record, dict):
        errors.append(f"{label}: its record {record_key!r} is not in {NPM_RECORDS}")
        record = None
    locked_licence = lock[licence_dir].get("license")
    if locked_licence is None:
        if not record or record.get("license") != licence:
            errors.append(
                f"{label}: package-lock.json records no licence for {licence_dir}, and no "
                f"record in {NPM_RECORDS} supplies {licence!r}"
            )
    elif locked_licence != licence:
        errors.append(
            f"{label}: package-lock.json records the licence {locked_licence!r}, "
            f"not {licence!r}"
        )

    if section is None:
        return errors + [f"{label}: {NPM_NOTICES} has no section for it"]
    if f"\nLicence: {licence}\n" not in section:
        errors.append(f"{label}: its section in {NPM_NOTICES} does not name the licence {licence}")
    files = entry.get("license_files")
    if not isinstance(files, list):
        return errors + [f"{label}: license_files must be a list"]
    for file_name in files:
        text = _read_licence(root / licence_dir / file_name)
        if text is None:
            errors.append(f"{label}: {licence_dir}/{file_name} is not installed")
        elif text not in section:
            errors.append(f"{label}: {NPM_NOTICES} does not carry the full text of {file_name}")
    if not files:
        supplied = record.get("license_text") if record else None
        if not isinstance(supplied, str) or not supplied.strip():
            errors.append(f"{label}: ships no licence file, and no record supplies its text")
        elif normalize_licence_text(supplied) not in section:
            errors.append(f"{label}: {NPM_NOTICES} does not carry its recorded licence text")
    return errors


def npm_notice_errors(root: Path | None = None) -> list[str]:
    """The npm notices the web build wrote cover every package whose code it emitted.

    The census, ``THIRD_PARTY_NOTICES_NPM.json``, is the bundler's module graph as
    ``web/scripts/thirdPartyNotices.mjs`` read it. This holds it to what does not come from that
    script: the scripts and stylesheets actually in ``web/dist`` (one the census does not cover
    was built without it, so its packages are unknown), ``package-lock.json`` (each package is
    installed at that path, at that version, under that licence), the licence files in
    ``node_modules`` (each one's full text is in the package's section of the notices), and the
    reviewed records (each record is still in use).
    """
    root = root or REPO_ROOT
    dist = root / BUILT_WEB_ROOT
    emitted = set(built_web_code_paths(root))
    missing = [name for name in (NPM_CENSUS, NPM_NOTICES) if not (dist / name).is_file()]
    if not emitted and len(missing) == 2:
        # No script and no stylesheet: no package's code was built, so there is nothing to notice.
        return []
    if missing:
        return [
            f"the built web has no {' or '.join(missing)}; `npm run build` writes them "
            "(web/scripts/thirdPartyNotices.mjs)"
        ]
    try:
        census = json.loads((dist / NPM_CENSUS).read_text(encoding="utf-8"))
        notices = (dist / NPM_NOTICES).read_text(encoding="utf-8")
        lock = json.loads((root / LOCKFILE).read_text(encoding="utf-8")).get("packages") or {}
        records_path = root / NPM_RECORDS
        records = (
            json.loads(records_path.read_text(encoding="utf-8")).get("packages") or {}
            if records_path.is_file()
            else {}
        )
    except (OSError, ValueError) as exc:
        return [
            f"npm notices: cannot read the census, the notices, the lockfile or the records ({exc})"
        ]

    packages, built = census.get("packages"), census.get("files")
    if census.get("schema_version") != 1 or not isinstance(packages, list):
        return [f"{NPM_CENSUS}: schema_version must be 1 and packages a list"]
    if not isinstance(built, dict):
        return [f"{NPM_CENSUS}: files must map each built file to its packages"]
    if built and not packages:
        return [
            f"{NPM_CENSUS} attributes no package to any of its {len(built)} built files; "
            "the module graph was not read"
        ]

    errors: list[str] = []
    for path in sorted(emitted - set(built)):
        errors.append(
            f"built web file {path}: in no row of {NPM_CENSUS}, so the packages in it are unknown"
        )
    for path in sorted(set(built) - emitted):
        errors.append(f"{NPM_CENSUS} lists {path}, which is not in {BUILT_WEB_ROOT}")

    entries: dict[str, dict[str, Any]] = {}
    for entry in packages:
        key = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(key, str):
            errors.append(f"{NPM_CENSUS}: a package entry has no path: {entry!r}")
        elif key in entries:
            errors.append(f"{NPM_CENSUS}: {key} is listed twice")
        else:
            entries[key] = entry
    referenced: set[str] = set()
    for path, listed in sorted(built.items()):
        for key in listed if isinstance(listed, list) else []:
            referenced.add(key)
            if key not in entries:
                errors.append(
                    f"built web file {path} bundles {key}, which has no entry in {NPM_CENSUS}"
                )
    for key in sorted(set(entries) - referenced):
        errors.append(f"{NPM_CENSUS} lists {key}, which no built file bundles")

    sections, twice = _notice_sections(notices)
    for key in twice:
        errors.append(f"{NPM_NOTICES} has two sections for {key}")
    for key in sorted(set(sections) - set(entries)):
        errors.append(f"{NPM_NOTICES} has a section for {key}, which no built file bundles")
    for key, entry in sorted(entries.items()):
        errors.extend(_npm_entry_errors(entry, sections.get(key), lock, records, root))

    used = {entry.get("record") for entry in entries.values()}
    for record_key in sorted(set(records) - used):
        errors.append(f"{NPM_RECORDS}: {record_key} is a record no bundled package uses")
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
        errors.extend(npm_notice_errors(root))
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
        built = ""
        if args.built_web:
            census = json.loads(
                (REPO_ROOT / BUILT_WEB_ROOT / NPM_CENSUS).read_text(encoding="utf-8")
            )
            built = (
                f", {len(built_web_asset_paths())} built web assets, and "
                f"{len(census['packages'])} npm packages in {len(census['files'])} built files "
                f"with their notices in {NPM_NOTICES}"
            )
        print(f"asset-licenses: PASS ({count} tracked assets{built}, every one recorded)")
        return 0
    print(f"asset-licenses: FAIL ({len(found)} violation(s), {count} tracked assets)")
    for line in found:
        print(f"  - {line}")
    npm_found = set(npm_notice_errors()) & set(found) if args.built_web else set()
    if set(found) - npm_found:
        print(
            f"Record a new file in {MANIFEST_NAME}: list its path in a project-owned group, or "
            "add it with its sha256 to a third-party group (--refresh re-pins listed paths)."
        )
    if npm_found:
        print(
            f"{NPM_NOTICES} is written by `npm run build` (web/scripts/thirdPartyNotices.mjs) from "
            "the bundler's module graph: rebuild rather than edit it. A package whose archive "
            f"leaves out its licence or its licence text needs a reviewed record in {NPM_RECORDS}."
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
