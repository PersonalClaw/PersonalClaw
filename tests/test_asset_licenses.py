"""The bundled-asset licence record: ``ASSET_LICENSES.json`` and its checker.

Every font, image, binary and fixture the repository ships has a recorded source, version,
licence and notice. The tree tests read the real record; the rest drive the checker over a
throwaway git repository, so each rule is shown refusing the thing it exists to refuse.
"""

from __future__ import annotations

import hashlib
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
    import json

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
