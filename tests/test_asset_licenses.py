"""The bundled-asset licence record: ``ASSET_LICENSES.json`` and its checker.

Every font, image, binary and fixture the repository ships has a recorded source, version,
licence and notice. The tree tests read the real record; the rest drive the checker over a
throwaway git repository, so each rule is shown refusing the thing it exists to refuse.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import check_asset_licenses as assets

REPO_ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
FONT = b"wOF2\0\1font-bytes"
UPSTREAM = "Copyright 2020 The Upstream Project Authors"
_FONT_SUFFIXES = (".woff2", ".woff", ".ttf", ".otf", ".eot")
_CHECK = re.compile(r"^(?:uv run )?python3? scripts/check_asset_licenses\.py( --built-web)?$")


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _project(*paths: str, group_id: str = "artwork") -> dict:
    return {
        "id": group_id,
        "source": "Project-authored test artwork",
        "version": "repository-revision",
        "license": "MIT",
        "ownership": "project-owned",
        "required_notice": "LICENSE",
        "files": list(paths),
    }


def _third_party(files: dict[str, bytes], *, group_id: str = "upstream-font") -> dict:
    return {
        "id": group_id,
        "source": "https://example.com/upstream/tree/v1.0.0",
        "version": "1.0.0",
        "license": "OFL-1.1",
        "ownership": "third-party",
        "required_notice": "NOTICES.txt",
        "copyright": [UPSTREAM],
        "files": {path: _sha(blob) for path, blob in files.items()},
    }


def _manifest(*groups: dict, built: list[dict] | None = None) -> dict:
    manifest: dict = {"schema_version": 1, "groups": list(groups)}
    if built is not None:
        manifest["built_web_groups"] = built
    return manifest


def _seed_repo(root: Path, files: dict[str, bytes | str]) -> Path:
    base: dict[str, bytes | str] = {
        "LICENSE": "MIT test licence\n",
        "NOTICES.txt": f"{UPSTREAM}\nThe upstream licence text.\n",
    }
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, content in {**base, **files}.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
    return root


def _emit(root: Path, files: dict[str, bytes]) -> None:
    """Stand in for `npm run build`: write files under web/dist without tracking them."""
    for rel, blob in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)


def _group_of(manifest: dict, path: str) -> dict:
    for group in manifest["groups"]:
        if path in group["files"]:
            return group
    raise AssertionError(f"{path} is in no group")


# ── The real record ────────────────────────────────────────────────────────────────────────


def test_every_tracked_asset_has_a_licence_record():
    assert assets.violations() == []


def test_the_asset_census_is_not_vacuous():
    paths = assets.asset_paths()
    assert len(paths) > 200, f"only {len(paths)} assets found; the census is broken"
    for expected in (
        "desktop/icon.png",
        "src/personalclaw/tests_fixtures/empty/fixture.yaml",
        "tests/fixtures/word_authored.docx",
        "web/public/claw.svg",
        "web/public/fonts/dm-sans.woff2",
    ):
        assert expected in paths


def test_every_bundled_font_is_a_pinned_third_party_record_with_a_notice_beside_it():
    manifest = assets.load_manifest()
    fonts = [path for path in assets.asset_paths() if path.endswith(_FONT_SUFFIXES)]
    assert fonts, "no tracked font found; the census is broken"
    for path in fonts:
        group = _group_of(manifest, path)
        assert group["ownership"] == "third-party", path
        assert group["files"][path] == _sha((REPO_ROOT / path).read_bytes()), path
        # Vite copies web/public into web/dist verbatim, and the wheel carries web/dist, so a
        # notice in web/public ships everywhere the font does.
        assert group["required_notice"].startswith("web/public/"), path


def test_fonts_css_serves_only_recorded_fonts():
    css = (REPO_ROOT / "web/src/design/fonts.css").read_text(encoding="utf-8")
    served = re.findall(r"""url\(["']?/fonts/([^"')]+)["']?\)""", css)
    assert served, "fonts.css declares no bundled face; the pattern is broken"
    manifest = assets.load_manifest()
    for name in served:
        group = _group_of(manifest, f"web/public/fonts/{name}")
        assert group["ownership"] == "third-party", name


# ── Coverage ───────────────────────────────────────────────────────────────────────────────


def test_an_unrecorded_asset_fails(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG, "web/public/new.png": PNG})
    found = assets.violations(root, _manifest(_project("web/public/mark.png")))
    assert found == ["asset web/public/new.png: missing from ASSET_LICENSES.json"]


def test_every_file_under_a_fixture_root_is_censused_even_as_text(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"tests/fixtures/golden.jsonl": '{"a": 1}\n'})
    assert assets.asset_paths(root) == ["tests/fixtures/golden.jsonl"]


def test_one_asset_cannot_be_claimed_by_two_groups(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    manifest = _manifest(
        _project("web/public/mark.png"), _project("web/public/mark.png", group_id="second")
    )
    found = assets.violations(root, manifest)
    assert "asset web/public/mark.png: mapped by both artwork and second" in found


def test_a_listed_path_that_is_not_tracked_fails(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    found = assets.violations(
        root, _manifest(_project("web/public/mark.png", "web/public/gone.png"))
    )
    assert "asset web/public/gone.png: listed in ASSET_LICENSES.json but not tracked" in found


# ── Digests: third-party pinned, project-owned not ─────────────────────────────────────────


def test_a_changed_third_party_file_fails_its_pin(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/fonts/face.woff2": FONT + b"swapped"})
    found = assets.violations(root, _manifest(_third_party({"web/public/fonts/face.woff2": FONT})))
    assert len(found) == 1
    assert found[0].startswith("asset web/public/fonts/face.woff2: sha256 mismatch")


def test_a_project_owned_file_changes_without_touching_the_record(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG + b"recaptured"})
    assert assets.violations(root, _manifest(_project("web/public/mark.png"))) == []


def test_a_project_owned_group_cannot_pin_digests(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    group = _project()
    group["files"] = {"web/public/mark.png": _sha(PNG)}
    found = assets.violations(root, _manifest(group))
    assert "group artwork: project-owned files must be a list of paths" in found


def test_a_project_owned_group_is_versioned_by_git(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    group = _project("web/public/mark.png")
    group["version"] = "Word for Mac 16.111"
    found = assets.violations(root, _manifest(group))
    assert any("project-owned assets are versioned by git" in line for line in found)


# ── Third-party metadata and notices ───────────────────────────────────────────────────────


def test_a_third_party_record_needs_a_versioned_https_source_copyright_and_its_own_notice(
    tmp_path,
):
    root = _seed_repo(tmp_path / "repo", {"web/public/fonts/face.woff2": FONT})
    group = _third_party({"web/public/fonts/face.woff2": FONT})
    group.update(source="copied from a blog post", version="repository-revision")
    group.update(required_notice="LICENSE", copyright=[])
    found = assets.violations(root, _manifest(group))
    for fragment in (
        "third-party source must be an https URL",
        "third-party assets need an external source version",
        "third-party assets need their own tracked notice",
        "third-party copyright must list the upstream copyright lines",
    ):
        assert any(fragment in line for line in found), fragment


def test_a_notice_that_drops_a_copyright_line_fails(tmp_path):
    root = _seed_repo(
        tmp_path / "repo",
        {"web/public/fonts/face.woff2": FONT, "NOTICES.txt": "The upstream licence text.\n"},
    )
    found = assets.violations(root, _manifest(_third_party({"web/public/fonts/face.woff2": FONT})))
    assert found == [
        f"group upstream-font: NOTICES.txt does not carry the copyright line {UPSTREAM!r}"
    ]


def test_an_untracked_notice_fails(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/fonts/face.woff2": FONT})
    group = _third_party({"web/public/fonts/face.woff2": FONT})
    group["required_notice"] = "THIRD_PARTY.txt"
    found = assets.violations(root, _manifest(group))
    assert "group upstream-font: its notice is not tracked: THIRD_PARTY.txt" in found


def test_a_misspelled_field_is_refused_rather_than_ignored(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    group = _project("web/public/mark.png")
    group["licence"] = group.pop("license")
    found = assets.violations(root, _manifest(group))
    assert "group artwork: unknown field 'licence'" in found
    assert "group artwork: license must be a non-empty string" in found


# ── The web build ──────────────────────────────────────────────────────────────────────────


UNRECORDED = "matches no file recorded in ASSET_LICENSES.json"
KATEX = "node_modules/katex/dist/fonts/Math.woff2"


def test_an_emitted_copy_of_a_recorded_file_is_covered_whatever_vite_names_it(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/fonts/face.woff2": FONT})
    manifest = _manifest(_third_party({"web/public/fonts/face.woff2": FONT}))
    _emit(root, {"web/dist/fonts/face.woff2": FONT, "web/dist/assets/face-1a2b3c4d.woff2": FONT})
    assert assets.violations(root, manifest, include_built_web=True) == []

    _emit(root, {"web/dist/assets/face-1a2b3c4d.woff2": FONT + b"rewritten by a plugin"})
    assert assets.violations(root, manifest, include_built_web=True) == [
        f"built web asset web/dist/assets/face-1a2b3c4d.woff2: {UNRECORDED}"
    ]


def test_a_font_hashed_out_of_a_dependency_is_covered_only_by_that_file_s_record(tmp_path):
    math = b"\0\1math-font"
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    _emit(
        root,
        {
            KATEX: math,
            "web/dist/assets/Math-9f8e7d6c.woff2": math,
            "web/dist/assets/Other-0a1b2c3d.woff2": b"\0\1from a package nobody recorded",
        },
    )
    manifest = _manifest(
        _project("web/public/mark.png"), built=[_third_party({KATEX: math}, group_id="katex")]
    )
    assert assets.violations(root, manifest, include_built_web=True) == [
        f"built web asset web/dist/assets/Other-0a1b2c3d.woff2: {UNRECORDED}"
    ]


def test_a_dependency_upgrade_fails_until_its_record_is_reviewed(tmp_path):
    math = b"\0\1math-font"
    upgraded = math + b" v2"
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    _emit(root, {KATEX: upgraded, "web/dist/assets/Math-5e4d3c2b.woff2": upgraded})
    manifest = _manifest(
        _project("web/public/mark.png"), built=[_third_party({KATEX: math}, group_id="katex")]
    )
    found = assets.violations(root, manifest, include_built_web=True)
    assert len(found) == 2, found
    assert found[0] == f"built web asset web/dist/assets/Math-5e4d3c2b.woff2: {UNRECORDED}"
    assert found[1].startswith(f"dependency file {KATEX}: sha256 mismatch")


def test_a_missing_dependency_install_is_named(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    _emit(root, {"web/dist/mark.png": PNG})
    manifest = _manifest(
        _project("web/public/mark.png"), built=[_third_party({KATEX: FONT}, group_id="katex")]
    )
    assert assets.violations(root, manifest, include_built_web=True) == [
        f"dependency file {KATEX}: not installed; run `npm ci` first"
    ]


def test_a_built_web_record_names_a_third_party_dependency_file(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    manifest = _manifest(
        _project("web/public/mark.png"), built=[_project("web/dist/mark.png", group_id="art")]
    )
    found = assets.violations(root, manifest)
    assert (
        "group art: web/dist/mark.png is not an installed dependency file under node_modules/"
        in found
    )
    assert any("a built web group records third-party dependency files" in line for line in found)


def test_the_built_web_check_refuses_a_missing_build(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    found = assets.violations(
        root, _manifest(_project("web/public/mark.png")), include_built_web=True
    )
    assert found == ["the built web census is empty; run `npm run build` before --built-web"]


# ── The npm packages' notices (web/scripts/thirdPartyNotices.mjs) ──────────────────────────
#
# The build writes THIRD_PARTY_NOTICES_NPM.txt and the census it was written from. These tests
# stand in for that build with a hand-written one in the same format, and show the checker
# holding it to the files built, to package-lock.json and to the licence files installed.

RULE = "=" * 78


def _mit(holder: str) -> str:
    return (
        f"MIT License\n\nCopyright (c) {holder}\n\nPermission is hereby granted, free of charge.\n"
    )


def _npm_repo(tmp_path: Path, installed: dict[str, dict], records: dict | None = None) -> Path:
    """A repository whose package-lock.json installed ``installed`` (lockfile key → package)."""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    lock: dict[str, dict] = {"": {"name": "fixture"}}
    for key, spec in installed.items():
        name = key.rsplit("node_modules/", 1)[1]
        package_dir = root / key
        package_dir.mkdir(parents=True)
        (package_dir / "package.json").write_text(
            json.dumps({"name": name, "version": spec["version"]}), encoding="utf-8"
        )
        for file_name, body in spec.get("files", {"LICENSE": _mit(f"{name} authors")}).items():
            (package_dir / file_name).write_text(body, encoding="utf-8")
        lock[key] = {"version": spec["version"]}
        if spec.get("license"):
            lock[key]["license"] = spec["license"]
    (root / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": lock}), encoding="utf-8"
    )
    if records is not None:
        (root / "web").mkdir(exist_ok=True)
        (root / assets.NPM_RECORDS).write_text(
            json.dumps({"schema_version": 1, "packages": records}), encoding="utf-8"
        )
    return root


def _entry(path: str, version: str | None, licence: str = "MIT", **extra) -> dict:
    name = extra.pop("name", path.rsplit("node_modules/", 1)[1])
    return {
        "path": path,
        "name": name,
        "version": version,
        "license": licence,
        "license_files": extra.pop("license_files", ["LICENSE"]),
        **extra,
    }


def _build(
    root: Path,
    packages: list[dict],
    files: dict[str, list[str]],
    *,
    omit: tuple[str, ...] = (),
    texts: dict[str, str] | None = None,
) -> None:
    """Stand in for `npm run build`: the built files, the census, and the notices it writes.

    Each section carries the full text of the entry's licence files as installed, unless
    ``texts`` gives a path its own text; a path in ``omit`` gets no section.
    """
    dist = root / assets.BUILT_WEB_ROOT
    for rel in files:
        (dist / rel).parent.mkdir(parents=True, exist_ok=True)
        (dist / rel).write_text("/* built */", encoding="utf-8")
    census = {"schema_version": 1, "packages": packages, "files": files}
    (dist / assets.NPM_CENSUS).write_text(json.dumps(census), encoding="utf-8")
    sections = ["Third-party notices for the PersonalClaw dashboard's code\n"]
    for entry in packages:
        if entry["path"] in omit:
            continue
        head = [RULE, f"{entry['name']} {entry['version']}"]
        head += [f"Licence: {entry['license']}", f"Path:    {entry['path']}", ""]
        source = root / (entry.get("license_from") or entry["path"])
        body = (texts or {}).get(entry["path"]) or "\n\n".join(
            assets.normalize_licence_text((source / name).read_text(encoding="utf-8"))
            for name in entry["license_files"]
        )
        sections.append("\n".join(head) + "\n" + body)
    (dist / assets.NPM_NOTICES).write_text("\n".join(sections) + "\n", encoding="utf-8")


REACT = "node_modules/react"
WIDGET = "node_modules/@scope/widget"


def _two_package_build(tmp_path: Path, **build) -> Path:
    root = _npm_repo(
        tmp_path,
        {
            REACT: {"version": "19.3.0", "license": "MIT"},
            WIDGET: {"version": "2.0.1", "license": "ISC"},
        },
    )
    packages = [_entry(WIDGET, "2.0.1", "ISC"), _entry(REACT, "19.3.0")]
    files = {"assets/index-a1.js": [WIDGET, REACT], "assets/index-c1.css": [], "sw.js": []}
    _build(root, packages, files, **build)
    return root


def test_npm_notices_that_cover_the_build_pass(tmp_path):
    assert assets.npm_notice_errors(_two_package_build(tmp_path)) == []


def test_a_bundled_package_missing_from_the_notices_fails(tmp_path):
    root = _two_package_build(tmp_path, omit=(REACT,))
    assert assets.npm_notice_errors(root) == [
        "node_modules/react (react 19.3.0): THIRD_PARTY_NOTICES_NPM.txt has no section for it"
    ]


def test_a_licence_text_cut_short_fails(tmp_path):
    root = _two_package_build(tmp_path, texts={REACT: "MIT License\n\nCopyright (c) react authors"})
    assert assets.npm_notice_errors(root) == [
        "node_modules/react (react 19.3.0): THIRD_PARTY_NOTICES_NPM.txt does not carry the full "
        "text of LICENSE"
    ]


def test_a_built_file_the_census_does_not_cover_fails(tmp_path):
    """A web worker built without the notices plugin: its packages are unknown."""
    root = _two_package_build(tmp_path)
    (root / assets.BUILT_WEB_ROOT / "assets/ts.worker-9f.js").write_text("/* built */")
    assert assets.npm_notice_errors(root) == [
        "built web file assets/ts.worker-9f.js: in no row of THIRD_PARTY_NOTICES_NPM.json, so "
        "the packages in it are unknown"
    ]


def test_each_package_is_the_one_the_lockfile_installed(tmp_path):
    root = _npm_repo(tmp_path, {REACT: {"version": "19.3.0", "license": "MIT"}})
    packages = [
        _entry(REACT, "19.2.0", "Apache-2.0"),
        _entry("node_modules/never-installed", "1.0.0", license_files=[]),
    ]
    _build(
        root,
        packages,
        {"assets/index.js": [REACT, "node_modules/never-installed"]},
        texts={"node_modules/never-installed": "x"},
    )
    assert assets.npm_notice_errors(root) == [
        "node_modules/never-installed (never-installed 1.0.0): node_modules/never-installed is "
        "not a package package-lock.json installs",
        "node_modules/react (react 19.2.0): package-lock.json installed 19.3.0 at "
        "node_modules/react, not 19.2.0",
        "node_modules/react (react 19.2.0): package-lock.json records the licence 'MIT', not "
        "'Apache-2.0'",
    ]


def test_the_census_and_the_notices_list_nothing_the_build_did_not_bundle(tmp_path):
    root = _npm_repo(
        tmp_path,
        {
            REACT: {"version": "19.3.0", "license": "MIT"},
            WIDGET: {"version": "2.0.1", "license": "ISC"},
        },
    )
    _build(root, [_entry(REACT, "19.3.0"), _entry(WIDGET, "2.0.1", "ISC")], {"sw.js": [REACT]})
    notices = root / assets.BUILT_WEB_ROOT / assets.NPM_NOTICES
    notices.write_text(
        notices.read_text()
        + f"{RULE}\nleft-pad 1.3.0\nLicence: MIT\nPath:    node_modules/left-pad\n"
    )
    assert assets.npm_notice_errors(root) == [
        "THIRD_PARTY_NOTICES_NPM.json lists node_modules/@scope/widget, which no built file "
        "bundles",
        "THIRD_PARTY_NOTICES_NPM.txt has a section for node_modules/left-pad, which no built file "
        "bundles",
    ]


def test_a_package_whose_archive_ships_no_licence_is_covered_by_its_reviewed_record(tmp_path):
    recorded = "(The MIT License)\r\n\r\nCopyright (c) 2017 Junyoung Choi  \r\n"
    record = {"license_text": recorded, "note": "The repository's licence at the 6.0.0 tag."}
    installed = {"node_modules/remark-math": {"version": "6.0.0", "license": "MIT", "files": {}}}
    entry = _entry(
        "node_modules/remark-math", "6.0.0", license_files=[], record="remark-math@6.0.0"
    )
    files = {"assets/index.js": ["node_modules/remark-math"]}

    root = _npm_repo(tmp_path / "ok", installed, records={"remark-math@6.0.0": record})
    _build(
        root,
        [entry],
        files,
        texts={"node_modules/remark-math": "(The MIT License)\n\nCopyright (c) 2017 Junyoung Choi"},
    )
    assert assets.npm_notice_errors(root) == []

    no_text = _npm_repo(tmp_path / "no-text", installed, records={"remark-math@6.0.0": record})
    _build(no_text, [entry], files, texts={"node_modules/remark-math": "MIT"})
    assert assets.npm_notice_errors(no_text) == [
        "node_modules/remark-math (remark-math 6.0.0): THIRD_PARTY_NOTICES_NPM.txt does not carry "
        "its recorded licence text"
    ]

    stale = _npm_repo(
        tmp_path / "stale",
        installed,
        records={"remark-math@6.0.0": record, "remark-math@5.1.1": record},
    )
    _build(
        stale,
        [entry],
        files,
        texts={"node_modules/remark-math": "(The MIT License)\n\nCopyright (c) 2017 Junyoung Choi"},
    )
    assert assets.npm_notice_errors(stale) == [
        "web/npm-license-records.json: remark-math@5.1.1 is a record no bundled package uses"
    ]


def test_a_licence_the_lockfile_does_not_record_must_come_from_a_record(tmp_path):
    """khroma's package.json declares no licence, so package-lock.json records none."""
    installed = {
        "node_modules/khroma": {"version": "2.1.0", "files": {"license": _mit("Fabio Spampinato")}}
    }
    entry = _entry("node_modules/khroma", "2.1.0", license_files=["license"])
    root = _npm_repo(tmp_path, installed)
    _build(root, [entry], {"assets/index.js": ["node_modules/khroma"]})
    assert assets.npm_notice_errors(root) == [
        "node_modules/khroma (khroma 2.1.0): package-lock.json records no licence for "
        "node_modules/khroma, and no record in web/npm-license-records.json supplies 'MIT'"
    ]


def test_a_copied_library_is_checked_against_the_package_it_takes_its_licence_from(tmp_path):
    installed = {
        "node_modules/@antv/layout": {"version": "2.0.0", "license": "MIT"},
        "node_modules/lodash": {
            "version": "4.18.1",
            "license": "MIT",
            "files": {"LICENSE": _mit("OpenJS")},
        },
    }
    copy = _entry(
        "node_modules/@antv/layout/lib/node_modules/lodash",
        None,
        name="lodash",
        license_from="node_modules/lodash",
        copied_into="node_modules/@antv/layout",
    )
    files = {"assets/index.js": ["node_modules/@antv/layout/lib/node_modules/lodash"]}
    root = _npm_repo(tmp_path / "ok", installed)
    _build(root, [copy], files)
    assert assets.npm_notice_errors(root) == []

    wrong = _npm_repo(tmp_path / "wrong", installed)
    _build(
        wrong,
        [{**copy, "license_from": "node_modules/lodash-es"}],
        files,
        texts={copy["path"]: _mit("OpenJS")},
    )
    assert assets.npm_notice_errors(wrong) == [
        "node_modules/@antv/layout/lib/node_modules/lodash (lodash copied into "
        "node_modules/@antv/layout): node_modules/lodash-es is not a package package-lock.json "
        "installs"
    ]


def test_a_build_with_scripts_but_no_npm_notices_is_refused(tmp_path):
    root = _npm_repo(tmp_path, {})
    (root / "web/dist/assets").mkdir(parents=True)
    (root / "web/dist/assets/index.js").write_text("/* built */")
    assert assets.npm_notice_errors(root) == [
        "the built web has no THIRD_PARTY_NOTICES_NPM.json or THIRD_PARTY_NOTICES_NPM.txt; "
        "`npm run build` writes them (web/scripts/thirdPartyNotices.mjs)"
    ]
    _build(root, [], {"assets/index.js": []})
    assert assets.npm_notice_errors(root) == [
        "THIRD_PARTY_NOTICES_NPM.json attributes no package to any of its 1 built files; the "
        "module graph was not read"
    ]


def test_the_built_web_check_holds_the_npm_notices(tmp_path):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    _emit(root, {"web/dist/mark.png": PNG, "web/dist/assets/index.js": b"/* built */"})
    found = assets.violations(
        root, _manifest(_project("web/public/mark.png")), include_built_web=True
    )
    assert found == [
        "the built web has no THIRD_PARTY_NOTICES_NPM.json or THIRD_PARTY_NOTICES_NPM.txt; "
        "`npm run build` writes them (web/scripts/thirdPartyNotices.mjs)"
    ]


def test_a_rule_inside_a_licence_text_does_not_split_its_section(tmp_path):
    """Only a rule followed by a title and ``Licence:`` opens a section."""
    ruled = f"{_mit('Monaco')}\n{RULE}\nThird-party component\n\n{RULE}\nMore text\n"
    root = _npm_repo(
        tmp_path, {REACT: {"version": "19.3.0", "license": "MIT", "files": {"LICENSE": ruled}}}
    )
    _build(root, [_entry(REACT, "19.3.0")], {"assets/index.js": [REACT]})
    assert assets.npm_notice_errors(root) == []


def test_licence_texts_are_compared_in_the_form_the_web_build_writes():
    """The same input and output as web/src/app/thirdPartyNotices.test.ts's normalizeText case."""
    assert (
        assets.normalize_licence_text("\ufeffMIT License  \r\n\r\nCopyright (c) X\t\r\n\n\n")
        == "MIT License\n\nCopyright (c) X"
    )


def test_every_npm_licence_record_is_reviewed_and_names_an_installed_version():
    """A record the lockfile cannot match is stale before any build runs."""
    lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    installed = {
        f"{key.rsplit('node_modules/', 1)[1]}@{entry.get('version')}"
        for key, entry in lock.items()
        if "node_modules/" in key
    }
    records = json.loads((REPO_ROOT / assets.NPM_RECORDS).read_text(encoding="utf-8"))["packages"]
    assert records, "web/npm-license-records.json holds no record; the census is broken"
    for label, record in records.items():
        assert label in installed, f"{label} is not a version package-lock.json installs"
        assert record.get(
            "note", ""
        ).strip(), f"{label} has no note saying where its facts come from"
        assert set(record) <= {"license", "license_text", "license_text_source", "note"}, label
        if "license_text" in record:
            assert record.get("license_text_source", "").startswith("https://"), label


# ── --refresh ──────────────────────────────────────────────────────────────────────────────


def test_refresh_repins_listed_paths_and_never_adopts_a_new_one(tmp_path):
    new = FONT + b"upgraded"
    root = _seed_repo(
        tmp_path / "repo",
        {"web/public/fonts/face.woff2": new, "web/public/fonts/other.woff2": FONT},
    )
    manifest = _manifest(_third_party({"web/public/fonts/face.woff2": FONT}))

    assets.refresh_digests(manifest, root)

    refreshed = assets.load_manifest(root)
    assert refreshed["groups"][0]["files"] == {"web/public/fonts/face.woff2": _sha(new)}
    assert assets.violations(root, refreshed) == [
        "asset web/public/fonts/other.woff2: missing from ASSET_LICENSES.json"
    ]


def test_refresh_repins_a_dependency_file_and_never_adopts_an_unreviewed_built_asset(tmp_path):
    old, new = b"\0\1old-font", b"\0\1new-font"
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    _emit(
        root,
        {
            KATEX: new,
            "web/dist/assets/Math-5e4d3c2b.woff2": new,
            "web/dist/assets/unreviewed.woff2": old,
        },
    )
    manifest = _manifest(
        _project("web/public/mark.png"), built=[_third_party({KATEX: old}, group_id="katex")]
    )

    assets.refresh_digests(manifest, root, include_built_web=True)

    assert manifest["built_web_groups"][0]["files"] == {KATEX: _sha(new)}
    assert assets.violations(root, manifest, include_built_web=True) == [
        f"built web asset web/dist/assets/unreviewed.woff2: {UNRECORDED}"
    ]


# ── The command line ───────────────────────────────────────────────────────────────────────


def test_the_command_passes_a_whole_record_and_names_the_fix_for_a_broken_one(
    tmp_path, monkeypatch, capsys
):
    root = _seed_repo(tmp_path / "repo", {"web/public/mark.png": PNG})
    (root / assets.MANIFEST_NAME).write_text(
        json.dumps(_manifest(_project("web/public/mark.png"))), encoding="utf-8"
    )
    monkeypatch.setattr(assets, "REPO_ROOT", root)
    assert assets.main([]) == 0
    assert "asset-licenses: PASS (1 tracked assets" in capsys.readouterr().out

    (root / "web/public/new.png").write_bytes(PNG)
    subprocess.run(["git", "add", "web/public/new.png"], cwd=root, check=True)
    assert assets.main([]) == 1
    out = capsys.readouterr().out
    assert "asset web/public/new.png: missing from ASSET_LICENSES.json" in out
    assert "list its path in a project-owned group" in out


# ── CI wiring ──────────────────────────────────────────────────────────────────────────────


def _asset_checks(workflow_text: str) -> list[tuple[bool, bool]]:
    """``(built_web, after_a_web_build)`` for every EXECUTED asset-licence check."""
    workflow = yaml.safe_load(workflow_text)
    checks = []
    for job in (workflow.get("jobs") or {}).values():
        built_at = None
        for index, step in enumerate(job.get("steps") or []):
            run = step.get("run") if isinstance(step, dict) else None
            if not isinstance(run, str):
                continue
            for raw in run.splitlines():
                line = raw.strip()
                if line == "npm run build" and built_at is None:
                    built_at = index
                match = _CHECK.match(line)
                if match:
                    checks.append((bool(match.group(1)), built_at is not None and built_at < index))
    return checks


@pytest.mark.parametrize("workflow", [".github/workflows/ci.yml", ".github/workflows/release.yml"])
def test_each_workflow_checks_the_tree_and_the_web_build_it_made(workflow):
    checks = _asset_checks((REPO_ROOT / workflow).read_text(encoding="utf-8"))
    assert (False, False) in checks or (False, True) in checks, f"{workflow}: no tree check"
    built = [after_build for built_web, after_build in checks if built_web]
    assert built, f"{workflow}: no --built-web check"
    assert all(built), f"{workflow}: --built-web runs before `npm run build`"


def test_a_commented_out_check_does_not_count():
    workflow = """
name: comment-only
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: npm run build
      - name: no asset check here
        run: |
          echo ready
          # python3 scripts/check_asset_licenses.py
          # python3 scripts/check_asset_licenses.py --built-web
"""
    assert "scripts/check_asset_licenses.py" in workflow
    assert _asset_checks(workflow) == []
