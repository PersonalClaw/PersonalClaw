"""Every module core loads by its NAME is one the desktop app's bundle carries.

The desktop app runs the gateway from a PyInstaller bundle, whose analysis follows import
statements. Core reaches some modules by their name instead, and the bundle left each of them out:

* the agent's memory, automation, prompt and subagent tools, loaded with
  ``importlib.import_module`` from a name in a table (``mcp_core._AGGREGATED_CATEGORY_MODULES``)
  or a keyword argument (``InProcessMcpToolProvider(module=…)``): the desktop app's agent had none
  of them, and the tool server agent CLIs run exited at its first tool listing;
* the document writers, loaded from a name formatted from their package's prefix: no document,
  sheet, deck or PDF could be written;
* the bundled skills, the workflow template library and its shared blocks, and the route
  reference, read through ``importlib.resources.files`` of a package named by a constant: an
  empty library, and a path that names no folder.

The bundle's list is derived from the tree (``scripts/backend_bundle_manifest.by_name_modules``);
this rail holds every LOAD SITE to it. The census is an AST walk over every call that loads a
module by its name, by the ``file::qualname`` it sits in. Each site's names are resolved where the
walk can follow them: a string, a module constant, a loop or a lookup over one, a name formatted
from a package's prefix. A site whose names it cannot follow is classified in :data:`_CLASSIFIED`,
with how its names are found, or why the bundle need not carry them. Then every module of the
package a site loads must be one the bundle carries, and every other module the standard
library's, imported somewhere in the package (so the analysis sees it), collected by the spec, or
excluded by it on purpose. A new load site whose names cannot be followed reds
:func:`test_every_load_site_is_followed_or_classified` until its author says how they are found,
and a name the bundle does not carry reds until it is carried.
"""

from __future__ import annotations

import json
from pathlib import Path

import by_name_load_census_shared
import pytest
from by_name_load_census_shared import _LOST_IN_THE_DESKTOP_APP, _REPO, _SPEC, _texts

manifest = by_name_load_census_shared.manifest


# ── The census ───────────────────────────────────────────────────────────────


# ── How the names of a site the walk cannot follow are found ─────────────────


# ── The rail ─────────────────────────────────────────────────────────────────


class TestTheNativeAppsReachTheBundle:
    @pytest.mark.timeout(40)
    def test_every_factory_is_carried_or_ships_as_package_data(self, manifest):
        """A factory in core is a module the bundle carries; one in the app's own folder is a
        file the bundle carries as package data, read by its path."""
        carried = set(manifest.by_name_modules(_REPO))
        data = {src for src, _dest in manifest.package_data_datas(_REPO)}
        own = 0
        for manifest_path in manifest.native_manifests(_REPO):
            for text in _texts(json.loads(manifest_path.read_text(encoding="utf-8"))):
                module, sep, attr = text.partition(":")
                if not (sep and attr.isidentifier() and module):
                    continue
                file = manifest_path.parent / (module.replace(".", "/") + ".py")
                if file.is_file():
                    own += 1
                    assert file.relative_to(_REPO).as_posix() in data, file
                    assert module not in carried, f"{module} is the app's own, not a module"
                else:
                    assert module in carried, f"{manifest_path.parent.name}: {module}"
        assert own, "no native app ships its own factory: the data half would be vacuous"


class TestTheBundleNamesOnlyWhatExists:
    @pytest.mark.timeout(40)
    def test_every_name_is_a_module_of_the_package(self, manifest):
        """The build looks for each name, so a name that is no module warns on every build."""
        modules = manifest.package_modules(_REPO)
        names = manifest.by_name_modules(_REPO)
        assert set(_LOST_IN_THE_DESKTOP_APP) <= set(names)
        assert not [n for n in names if n not in modules], "a name that is no module"
        assert "personalclaw.inbox_providers.slack_source" not in names
        assert "provider" not in names, "an app's own factory module is data, not a module"

    def test_the_spec_reads_the_derivation(self):
        text = _SPEC.read_text(encoding="utf-8")
        for call in (
            "manifest.by_name_modules()",
            "manifest.path_loaded_imports()",
            "manifest.sdk_submodules()",
        ):
            assert call in text, call


class TestTheDerivation:
    """The derivation on a tree of its own, so what it carries is decided by this test alone."""

    @staticmethod
    def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "personalclaw"\n'
            '[project.entry-points."personalclaw.sources"]\n'
            'drop = "personalclaw.sources.drop"\n',
            encoding="utf-8",
        )
        for rel, text in files.items():
            path = tmp_path / "src" / "personalclaw" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return tmp_path

    def test_a_name_wherever_a_load_takes_it_from(self, manifest, tmp_path):
        root = self._tree(
            tmp_path,
            {
                "__init__.py": "",
                "tools.py": "Provider(module='personalclaw.memory_tools')\n",
                "memory_tools.py": "",
                "table.py": "ROWS = ('personalclaw.automation_tools:list_tools',)\n",
                "automation_tools.py": "",
                "unnamed.py": "",
                "words.py": "TEXT = 'personalclaw.not_a_module'\n",
                "sources/__init__.py": "",
                "sources/drop.py": "",
            },
        )
        names = manifest.by_name_modules(root)
        assert {
            "personalclaw.memory_tools",
            "personalclaw.automation_tools",
            "personalclaw.sources.drop",
        } <= set(names)
        assert "personalclaw.unnamed" not in names
        assert "personalclaw.not_a_module" not in names

    def test_a_name_formatted_from_a_package_prefix_carries_the_package(self, manifest, tmp_path):
        root = self._tree(
            tmp_path,
            {
                "__init__.py": "",
                "registry.py": "def load(m):\n    __import__(f'personalclaw.writers.{m}')\n",
                "writers/__init__.py": "",
                "writers/pdf_writer.py": "",
                "writers/sub/__init__.py": "",
                "writers/sub/deep.py": "",
                "other.py": "",
            },
        )
        names = set(manifest.by_name_modules(root))
        assert {
            "personalclaw.writers",
            "personalclaw.writers.pdf_writer",
            "personalclaw.writers.sub.deep",
        } <= names
        assert "personalclaw.other" not in names

    def test_a_native_factory_in_core_is_carried_and_one_of_the_apps_own_is_data(
        self, manifest, tmp_path
    ):
        root = self._tree(
            tmp_path,
            {
                "__init__.py": "",
                "core_factory.py": "",
                "apps/__init__.py": "",
                "apps/native/in-core/app.json": json.dumps(
                    {"provider": {"implementation": "personalclaw.core_factory:create"}}
                ),
                "apps/native/own/app.json": json.dumps(
                    {"provider": {"implementation": "provider:create"}}
                ),
                "apps/native/own/provider.py": (
                    "import helper\nimport rare_library.sub\nfrom personalclaw.sdk import tool\n"
                    "from collections.abc import Callable\n"
                ),
                "apps/native/own/helper.py": "from another_library import thing\n",
            },
        )
        names = manifest.by_name_modules(root)
        assert "personalclaw.core_factory" in names
        assert "provider" not in names
        imports = manifest.path_loaded_imports(root)
        assert {"rare_library.sub", "personalclaw.sdk", "another_library"} <= set(imports)
        assert "helper" not in imports, "the app's own module ships beside it as data"
        assert "collections.abc" not in imports, "an alias no file holds; the analysis's own"
