"""The frozen bundle must carry everything the wheel declares — asserted WITHOUT PyInstaller.

Every finding in the 2026-09-23 desktop set had the same shape: **the source tree was fine and
the artefact was broken.** The agent-runner catalogue, the builtin tool-projection rule pack and
all four SDK submodules an extension imports were present in every checkout, read from the
checkout by every test, and absent from `/Applications/PersonalClaw.app`. A suite that only ever
runs against `src/` cannot see any of it, which is why none of it was caught for three releases.

This file is the rail that closes that blind spot. It does NOT run PyInstaller — a bundle build
is minutes and CI installs PyInstaller only in `release.yml`'s two desktop jobs — it asserts the
*derivation* the spec builds from, in `scripts/backend_bundle_manifest.py`, is complete against
independently recomputed truth:

* every `[tool.setuptools.package-data]` glob's files reach the bundle (the wheel and the
  frozen binary can no longer disagree, because one declaration feeds both);
* no declared glob is VACUOUS — the check the old spec's `os.path.exists` filter replaced with
  silence, which is how three of its patterns came to name paths that do not exist;
* no non-Python file under the package tree is undeclared — the rail that fires on the *next*
  asset somebody drops in without a glob, rather than on the two this set happened to name;
* every `personalclaw.sdk.*` submodule is a hidden import, enumerated from the directory;
* the spec actually CONSUMES the derivation — a perfect manifest with no call site is the
  failure mode this repo keeps finding.

The four named regressions are pinned by name as well as by rule. A rule can be weakened by a
future edit that still looks reasonable; a name cannot be weakened without deleting an
assertion that says what it is for.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import sys
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC_FILE = _REPO_ROOT / "personalclaw-backend.spec"
_MANIFEST_FILE = _REPO_ROOT / "scripts" / "backend_bundle_manifest.py"

#: The two data files the owner's 2026-09-23 install logged `FileNotFoundError` for,
#: eight times in one browsing session.
_OBSERVED_MISSING_DATA = (
    "src/personalclaw/agents/runner_catalog.json",
    "src/personalclaw/tool_providers/rules_builtin.json",
)

#: The four SDK submodules whose absence made every extension fail to enable, named in the
#: exact `ModuleNotFoundError` the packaged app logged.
_OBSERVED_MISSING_SDK = (
    "personalclaw.sdk.search",
    "personalclaw.sdk.tts",
    "personalclaw.sdk.channel",
    "personalclaw.sdk.trigger_source",
)


def _load_manifest():
    spec = importlib.util.spec_from_file_location("backend_bundle_manifest", _MANIFEST_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest():
    return _load_manifest()


@pytest.fixture(scope="module")
def spec_text() -> str:
    return _SPEC_FILE.read_text(encoding="utf-8")


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

    @pytest.mark.parametrize("source", _OBSERVED_MISSING_DATA)
    def test_the_observed_missing_files_are_carried(self, manifest, source):
        """The named regression. `runner_catalog.json` absent means Settings -> Agents has no
        rows and no adapter to gate an unattended spawn on; `rules_builtin.json` absent means
        the builtin tool-projection rule pack is skipped."""
        carried = dict(manifest.package_data_datas(_REPO_ROOT))
        assert source in carried, f"{source} would be missing from the bundle again"

    def test_destinations_mirror_the_import_package(self, manifest):
        """A frozen read must resolve data where the installed wheel puts it.

        `src/personalclaw/agents/runner_catalog.json` has to land at `personalclaw/agents/`,
        not `src/personalclaw/agents/` — the bundled path the traceback named.
        """
        for source, dest in manifest.package_data_datas(_REPO_ROOT):
            expected = (
                Path("personalclaw") / Path(source).relative_to("src/personalclaw").parent
            ).as_posix()
            assert dest == expected, f"{source} lands at {dest}, not {expected}"

    def test_no_declared_glob_is_vacuous(self, manifest):
        """A pattern matching nothing is indistinguishable from one that works.

        The old spec carried three: `eval/scenarios` (the directory is `evals/`),
        `slack-manifest.yaml` and `scripts` — all filtered out by an `os.path.exists` guard
        that reported nothing, so the shipped bundle silently lacked whatever they meant to
        name. The one deliberate exception is documented on
        `manifest.FORWARD_DECLARED_GLOBS` and asserted below.
        """
        assert manifest.vacuous_globs(_REPO_ROOT) == []

    def test_the_forward_declared_exception_is_still_only_the_trust_store(self, manifest):
        """The exemption list is an exemption list, not a parking lot.

        Pinned by value: widening it is the cheapest way to make the vacuity rail pass
        without shipping the file, so widening it has to be a visible edit here.
        """
        assert set(manifest.FORWARD_DECLARED_GLOBS) == {"trusted_keys/*.pub"}
        assert set(manifest.UNSHIPPED_DOCS) == {"trusted_keys/README.md"}

    def test_no_undeclared_asset_under_the_package_tree(self, manifest):
        """Fires on the NEXT undeclared asset, not the ones this finding set named.

        Found four the first time it ran: `native-knowledge`'s `app.json` names four
        `prompts/*.yaml` files that no package-data glob carried, so `pip install personalclaw`
        shipped that app pointing at prompts the wheel did not contain. The frozen bundle had
        them only by the accident of copying a whole directory — the accident that hid it.
        """
        assert manifest.undeclared_package_files(_REPO_ROOT) == []

    def test_the_spa_is_carried_when_built(self, manifest):
        """`web/dist` is the one entry not derived from pyproject, so it needs its own rail."""
        assert manifest.EXTRA_DATAS == (("web/dist", "personalclaw/static/dist"),)
        if (_REPO_ROOT / "web" / "dist").exists():
            assert ("web/dist", "personalclaw/static/dist") in manifest.bundle_datas(_REPO_ROOT)


class TestDynamicImportsReachTheBundle:
    def test_every_sdk_submodule_is_enumerated(self, manifest):
        """Independently re-listed from disk: an app imports core ONLY via the SDK, and every
        one of those imports is resolved at enable time by importlib, so static analysis sees
        none of them."""
        on_disk = {
            f"personalclaw.sdk.{p.stem}"
            for p in (_REPO_ROOT / "src" / "personalclaw" / "sdk").glob("*.py")
            if p.stem != "__init__"
        }
        collected = set(manifest.sdk_submodules(_REPO_ROOT))
        assert on_disk, "no SDK submodules found — the enumeration would pass vacuously"
        assert on_disk <= collected, f"SDK submodules not collected: {sorted(on_disk - collected)}"
        assert "personalclaw.sdk" in collected, "the SDK package itself must ship too"

    @pytest.mark.parametrize("module", _OBSERVED_MISSING_SDK)
    def test_the_observed_missing_sdk_modules_are_collected(self, manifest, module):
        """The named regression: brave-search, voice-clone-tts, telegram-channel and
        discord-channel each failed to enable on exactly one of these."""
        assert module in manifest.sdk_submodules(_REPO_ROOT)


class TestTheSpecActuallyUsesTheDerivation:
    """A manifest nothing calls is a manifest that proves nothing.

    Asserted against the spec's SOURCE because a spec cannot be imported — it is `exec`'d by
    PyInstaller with injected globals (`SPECPATH`, `Analysis`, `EXE`, `COLLECT`) that do not
    exist in a test process.
    """

    def test_the_spec_loads_the_manifest(self, spec_text):
        assert "backend_bundle_manifest.py" in spec_text

    def test_datas_come_from_the_manifest(self, spec_text):
        assert "datas = manifest.bundle_datas()" in spec_text

    def test_sdk_submodules_come_from_the_manifest(self, spec_text):
        assert "manifest.sdk_submodules()" in spec_text

    def test_the_hand_transcribed_list_is_gone(self, spec_text):
        """Clean break: the old `_backend_data()` must not survive beside the derivation.

        Two sources for one list is how the eleven-glob drift happened in the first place.
        """
        assert "_backend_data" not in spec_text
        assert "Replicate the package-data globs" not in spec_text

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

    def test_the_smoke_fails_on_every_signature_it_names(self):
        """The signature list is the assertion, so it must not quietly shrink.

        Each entry was observed in a real `gateway.log`. Pinned here because deleting one is
        the cheapest way to make a red release go green, and it would look like tidying.
        """
        smoke = (_REPO_ROOT / "scripts" / "smoke_backend_bundle.sh").read_text(encoding="utf-8")
        for signature in (
            "ModuleNotFoundError",
            "Failed to enable extension",
            "FileNotFoundError",
            "Dropping MCP server",
            "Could not resolve personalclaw binary",
            "runner catalog: shipped",
            "projection rule pack unreadable",
            "not a git repository",
            "unrecognized top-level keys",
        ):
            assert f'"{signature}"' in smoke, f"the smoke stopped failing on: {signature}"
        # A clean log only means something if the gateway actually came up and answered.
        assert "never became ready" in smoke, "a crashed gateway would pass with an empty log"
        assert "the probe proved nothing" in smoke, "an unreachable probe would pass vacuously"

    def test_the_manifest_is_loadable_by_path_from_the_repo_root(self):
        """The spec resolves the manifest relative to `SPECPATH`; prove that path exists and
        executes, so a rename cannot silently fall back to the CWD branch."""
        assert _MANIFEST_FILE.is_file()
        assert os.path.relpath(_MANIFEST_FILE, _REPO_ROOT) == os.path.join(
            "scripts", "backend_bundle_manifest.py"
        )
