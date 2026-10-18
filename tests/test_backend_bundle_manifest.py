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
* every `personalclaw.sdk.*` submodule is a hidden import, enumerated from the directory, and
  so is every module core loads by its name (`tests/test_by_name_load_census.py` holds each
  load site to that list);
* the spec actually CONSUMES the derivation — a perfect manifest with no call site is the
  failure mode this repo keeps finding.

The four named regressions are pinned by name as well as by rule. A rule can be weakened by a
future edit that still looks reasonable; a name cannot be weakened without deleting an
assertion that says what it is for.
"""

from __future__ import annotations

import os
from pathlib import Path

import backend_bundle_manifest_shared
import pytest
from backend_bundle_manifest_shared import _MANIFEST_FILE, _REPO_ROOT

manifest = backend_bundle_manifest_shared.manifest

_SPEC_FILE = _REPO_ROOT / "personalclaw-backend.spec"

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


@pytest.fixture(scope="module")
def spec_text() -> str:
    return _SPEC_FILE.read_text(encoding="utf-8")


class TestDataFilesReachTheBundle:

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
        # `sdk/signatures.json` is the SDK's reviewed signature snapshot, read only by the
        # snapshot script, its test and the apps' contract check.
        assert set(manifest.UNSHIPPED_FILES) == {"trusted_keys/README.md", "sdk/signatures.json"}
        for path, why in manifest.UNSHIPPED_FILES.items():
            assert len(why) > 40, f"{path} is unshipped without a real reason"

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


class TestTheBundleDescribesWhatItCarries:
    """``importlib.metadata`` in the bundle describes exactly what the bundle can import.

    An app's declared packages are judged installed by it (``apps.app_python.unmet``), and the
    bundle used to carry the metadata of a dozen distributions while carrying the modules of some
    eighty: every model app's ``openai`` read as missing, so its install asked for a pip the
    bundle does not have. The metadata is derived from the analysed modules, so it can neither
    miss a bundled distribution nor claim one the bundle cannot import.
    """

    @staticmethod
    def _dests(entries) -> set[str]:
        return {dest for dest, _source, _kind in entries}

    def test_a_bundled_package_carries_its_distributions_metadata(self, manifest):
        import importlib.metadata

        entries = manifest.metadata_datas(["aiohttp", "aiohttp.web", "json"])
        record = {str(f) for f in importlib.metadata.distribution("aiohttp").files or []}
        info = {f for f in record if f.split("/", 1)[0].endswith(".dist-info")}
        assert info, "aiohttp's own metadata files are not listed — the check would be vacuous"
        assert info <= self._dests(entries)
        for dest, source, kind in entries:
            assert kind == "DATA" and Path(source).is_file(), (dest, source)
            assert dest.split("/", 1)[0].endswith(".dist-info"), dest

    def test_a_compiled_module_names_its_package_by_its_path(self, manifest):
        entries = manifest.metadata_datas(["aiohttp/_http_parser.cpython-313-darwin.so"])
        assert any(d.startswith("aiohttp-") for d in self._dests(entries)), entries

    def test_a_distribution_the_bundle_carries_no_module_of_carries_no_metadata(self, manifest):
        """pytest is installed here, and is not in the bundle: its metadata would read as an
        install the bundle cannot import."""
        dests = self._dests(manifest.metadata_datas(["aiohttp", "json", "os"]))
        assert not [d for d in dests if d.startswith(("pytest-", "pip-"))], sorted(dests)

    def test_the_spec_adds_it_to_what_the_analysis_collected(self, spec_text):
        assert "a.datas += manifest.metadata_datas(" in spec_text
        assert spec_text.index("a.datas += manifest.metadata_datas(") > spec_text.index(
            "a = Analysis("
        ), "the metadata is derived from the analysis, so it is added after it"


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

    def test_the_smoke_reads_what_core_loads_by_name(self):
        """A module core loads by its name and the bundle lacks often logs nothing at all: the
        template library is just empty, the document writers just absent. So the smoke reads each
        surface such a module serves and fails on its absence, and none may quietly go."""
        smoke = (_REPO_ROOT / "scripts" / "smoke_backend_bundle.sh").read_text(encoding="utf-8")
        for surface in (
            'not d["load_failures"]',
            "api /api/workflows",
            "api /api/skills",
            '"tool": "document_formats"',
            '"$BACKEND" mcp-core',
            '"$BACKEND" doctor --paths',
        ):
            assert surface in smoke, f"the smoke stopped reading: {surface}"

    def test_the_manifest_is_loadable_by_path_from_the_repo_root(self):
        """The spec resolves the manifest relative to `SPECPATH`; prove that path exists and
        executes, so a rename cannot silently fall back to the CWD branch."""
        assert _MANIFEST_FILE.is_file()
        assert os.path.relpath(_MANIFEST_FILE, _REPO_ROOT) == os.path.join(
            "scripts", "backend_bundle_manifest.py"
        )
