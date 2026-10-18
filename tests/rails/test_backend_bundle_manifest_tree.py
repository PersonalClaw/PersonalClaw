"""The source-tree checks split from
``tests/test_backend_bundle_manifest.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import glob
import os
import tomllib
from pathlib import Path

import backend_bundle_manifest_shared
from backend_bundle_manifest_shared import _REPO_ROOT

manifest = backend_bundle_manifest_shared.manifest


class TestDataFilesReachTheBundle:
    def test_every_package_data_glob_is_carried(self, manifest):
        """Set EQUALITY against an independent re-glob of pyproject, not a spot check.

        Recomputed here from `pyproject.toml` rather than asked of the manifest, so a
        derivation that silently narrowed (a filter, an exclusion, a reverted loop) reds
        instead of agreeing with itself.
        """
        doc = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared: set[str] = set()
        for pattern in doc["tool"]["setuptools"]["package-data"]["personalclaw"]:
            for hit in glob.glob(
                str(_REPO_ROOT / "src" / "personalclaw" / pattern), recursive=True
            ):
                path = Path(hit)
                if path.is_file():
                    declared.add(path.relative_to(_REPO_ROOT).as_posix())

        carried = {src for src, _dest in manifest.package_data_datas(_REPO_ROOT)}
        assert declared, "pyproject declares package data that matches nothing at all"
        missing = sorted(declared - carried)
        assert not missing, f"declared for the wheel but not carried into the bundle: {missing}"
        assert not sorted(carried - declared), "the bundle carries package data pyproject omits"


class TestTheSpecActuallyUsesTheDerivation:
    """A manifest nothing calls is a manifest that proves nothing.

    Asserted against the spec's SOURCE because a spec cannot be imported — it is `exec`'d by
    PyInstaller with injected globals (`SPECPATH`, `Analysis`, `EXE`, `COLLECT`) that do not
    exist in a test process.
    """

    def test_the_release_smoke_is_wired_into_both_desktop_jobs(self):
        """The EXECUTION half of the rail, and its call sites.

        The source rails above prove the derivation is complete; they cannot prove the built
        artefact works. `release.yml`'s two desktop jobs are where that is proven — and until
        2026-09-23 their only executed assertion was `"$BACKEND" --version`, which is argparse
        and exits before the runner catalogue is read, before any `sdk` submodule is imported
        and before an MCP server is resolved. So the broken dmg passed.

        Note these jobs run on TAGS ONLY (`on: push: tags: ["v*"]`), which is why the source
        rails exist as well: nothing in this file's other classes is redundant with the smoke,
        because the smoke does not run on a pull request.
        """
        import yaml

        smoke = _REPO_ROOT / "scripts" / "smoke_backend_bundle.sh"
        assert smoke.is_file(), "the release smoke script is missing"
        assert os.access(smoke, os.X_OK), "the release smoke script is not executable"

        workflow_path = _REPO_ROOT / ".github" / "workflows" / "release.yml"
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        # Parsed, not grepped: the step COMMENTS name the script too, so a text count reads 4
        # and would pass while a job ran nothing. `run:` blocks are the only call sites.
        for job in ("desktop-mac", "desktop-linux"):
            runs = [str(step.get("run", "")) for step in workflow["jobs"][job]["steps"]]
            calls = [r for r in runs if "scripts/smoke_backend_bundle.sh" in r]
            assert len(calls) == 1, f"{job} runs the smoke {len(calls)} times, expected exactly 1"
            # Also parsed rather than grepped, and for the same reason in reverse: the step
            # COMMENT quotes this literal to explain why it was insufficient, so a raw-text
            # check fires on the documentation of the fix.
            assert not [r for r in runs if '"$BACKEND" --version' in r], (
                f"{job} is back to a bare `--version` as its artefact assertion; that exits "
                "before the runner catalogue, the sdk imports and the MCP resolution"
            )
