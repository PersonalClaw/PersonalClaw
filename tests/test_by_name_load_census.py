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

import ast
import importlib.util
import json
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src" / "personalclaw"
_SPEC = _REPO / "personalclaw-backend.spec"

#: Every function that loads a module by its name, as the dotted name a call reaches it by.
_LOADERS = frozenset(
    {
        "importlib.import_module",
        "builtins.__import__",
        "importlib.util.find_spec",
        "importlib.resources.files",
        "runpy.run_module",
        "importlib.metadata.entry_points",
    }
)

#: The modules the desktop app's bundle left out although core loads them, so its agent had no
#: memory, automation, prompt or subagent tools, wrote no document and listed no template.
_LOST_IN_THE_DESKTOP_APP = (
    "personalclaw.mcp_memory",
    "personalclaw.mcp_automation",
    "personalclaw.mcp_prompts",
    "personalclaw.mcp_subagents",
    "personalclaw.documents.writers.docx_writer",
    "personalclaw.documents.writers.pdf_writer",
    "personalclaw.workflows.bundled",
    "personalclaw.workflows.bundled.shared",
    "personalclaw.skills.bundled",
    "personalclaw.reference",
)


def _load_manifest():
    spec = importlib.util.spec_from_file_location(
        "backend_bundle_manifest", _REPO / "scripts" / "backend_bundle_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest():
    return _load_manifest()


# ── The census ───────────────────────────────────────────────────────────────


class Site:
    """One call that loads a module by its name: where it is, and the names the walk followed
    (``None`` when it could not follow them)."""

    def __init__(self, key: str, loader: str, names: set[str] | None) -> None:
        self.key = key
        self.loader = loader
        self.names = names


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Every name an import in *tree* binds (at any depth), and the dotted name it stands for."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name
                else:
                    top = alias.name.split(".", 1)[0]
                    out[top] = top
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return out


def _dotted(node: ast.AST, aliases: dict[str, str]) -> str:
    """The dotted name a call's function is reached by, its first part read through *aliases*."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    head = aliases.get(node.id, "builtins.__import__" if node.id == "__import__" else node.id)
    return ".".join([head, *reversed(parts)])


def _texts(value: object) -> list[str]:
    """Every string in a literal value (a dict's values, not its keys)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for item in value.values() for t in _texts(item)]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [t for item in value for t in _texts(item)]
    return []


def _literal(node: ast.AST | None) -> object:
    try:
        return ast.literal_eval(node) if node is not None else None
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _module_constants(tree: ast.Module) -> dict[str, object]:
    """The module-level names bound to a literal value."""
    out: dict[str, object] = {}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        value = _literal(getattr(node, "value", None))
        if value is not None:
            out.update({t.id: value for t in targets if isinstance(t, ast.Name)})
    return out


class _Scope:
    """What a name in one function can be followed to: the module's constants, the function's
    loops and comprehensions over them, and its assignments of a string or of a lookup in one."""

    def __init__(self, constants: dict[str, object], functions: list[ast.AST]) -> None:
        self.constants = constants
        self.functions = functions

    def _sequence(self, node: ast.AST) -> list[object] | None:
        if isinstance(node, ast.Name) and node.id in self.constants:
            value = self.constants[node.id]
        else:
            value = _literal(node)
        if isinstance(value, dict):
            return list(value.values())
        if isinstance(value, (list, tuple, set, frozenset)):
            return list(value)
        return None

    def name(self, name: str) -> set[str] | None:
        for function in reversed(self.functions):
            for node in ast.walk(function):
                if isinstance(node, (ast.For, ast.comprehension)):
                    found = self._loop(node.target, node.iter, name)
                    if found is not None:
                        return found
                if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == name for t in node.targets
                ):
                    found = self._assigned(node.value)
                    if found is not None:
                        return found
        if name in self.constants:
            return set(_texts(self.constants[name])) or None
        return None

    def _loop(self, target: ast.AST, iterable: ast.AST, name: str) -> set[str] | None:
        items = self._sequence(iterable)
        if items is None:
            return None
        if isinstance(target, ast.Name) and target.id == name:
            return {t for item in items for t in _texts(item)} or None
        if isinstance(target, ast.Tuple):
            for i, element in enumerate(target.elts):
                if isinstance(element, ast.Name) and element.id == name:
                    return {
                        item[i] for item in items if isinstance(item, tuple) and len(item) > i
                    } or None
        return None

    def _assigned(self, value: ast.AST) -> set[str] | None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return {value.value}
        holder = None
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            if value.func.attr == "get":
                holder = value.func.value
        elif isinstance(value, ast.Subscript):
            holder = value.value
        if isinstance(holder, ast.Name) and isinstance(self.constants.get(holder.id), dict):
            return set(_texts(self.constants[holder.id])) or None
        return None


def _follow(
    node: ast.AST | None, scope: _Scope, modules: dict[str, bool], manifest
) -> set[str] | None:
    """The names a loader's argument can be followed to, or ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.JoinedStr):
        package = manifest.formatted_package(node, modules)
        return manifest.package_family(package, modules) if package else None
    if isinstance(node, ast.Name):
        return scope.name(node.id)
    return None


def _census(src: Path, manifest) -> dict[str, list[Site]]:
    """Every call under *src* that loads a module by its name, by ``file::qualname``."""
    modules = manifest.package_modules(src.parents[1])
    sites: dict[str, list[Site]] = {}
    for path in sorted(src.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _aliases(tree)
        constants = _module_constants(tree)
        rel = path.relative_to(src).as_posix()
        stack: list[ast.AST] = []
        names: list[str] = []

        class _Visitor(ast.NodeVisitor):
            def _scoped(self, node: ast.AST, name: str) -> None:
                stack.append(node)
                names.append(name)
                self.generic_visit(node)
                stack.pop()
                names.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._scoped(node, node.name)

            def visit_Call(self, node: ast.Call) -> None:
                loader = _dotted(node.func, aliases)
                if loader in _LOADERS:
                    argument = node.args[0] if node.args else None
                    if argument is None:
                        argument = next(
                            (k.value for k in node.keywords if k.arg in ("name", "anchor")), None
                        )
                    scope = _Scope(constants, [n for n in stack if not isinstance(n, ast.ClassDef)])
                    key = f"{rel}::{'.'.join(names)}"
                    found = _follow(argument, scope, modules, manifest)
                    sites.setdefault(key, []).append(Site(key, loader, found))
                self.generic_visit(node)

        _Visitor().visit(tree)
    return sites


# ── How the names of a site the walk cannot follow are found ─────────────────


def _calls(name: str, where: Path = _SRC) -> list[ast.Call]:
    """Every call of a function or class called *name* under *where*."""
    found: list[ast.Call] = []
    for path in sorted(where.rglob("*.py")) if where.is_dir() else [where]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _dotted(node.func, {}).split(".")[-1] == name:
                found.append(node)
    return found


def _first_strings(name: str, where: Path = _SRC) -> set[str]:
    """The string every call of *name* under *where* is given first."""
    return {
        c.args[0].value
        for c in _calls(name, where)
        if c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str)
    }


def _constant(rel: str, name: str) -> set[str]:
    """The strings of a module-level constant in ``src/personalclaw/<rel>``, a ``module:attr``
    reference read as its module."""
    tree = ast.parse((_SRC / rel).read_text(encoding="utf-8"))
    return {t.partition(":")[0] for t in _texts(_module_constants(tree).get(name))}


def _in_process_tool_modules() -> set[str]:
    """Every ``module=`` an ``InProcessMcpToolProvider`` is built with, and its default."""
    names = {
        k.value.value
        for c in _calls("InProcessMcpToolProvider")
        for k in c.keywords
        if k.arg == "module" and isinstance(k.value, ast.Constant)
    }
    tree = ast.parse((_SRC / "agents" / "native" / "tools.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "InProcessMcpToolProvider":
            init = next(n for n in node.body if getattr(n, "name", "") == "__init__")
            args = init.args
            for arg, default in zip(args.kwonlyargs, args.kw_defaults):
                if arg.arg == "module" and isinstance(default, ast.Constant):
                    names.add(default.value)
    return names


def _native_factories() -> set[str]:
    """The module of every native app's factory that is not in the app's own folder."""
    modules: set[str] = set()
    for manifest_path in sorted((_SRC / "apps" / "native").glob("*/app.json")):
        for text in _texts(json.loads(manifest_path.read_text(encoding="utf-8"))):
            module, sep, attr = text.partition(":")
            if sep and attr.isidentifier() and module:
                if not (manifest_path.parent / (module.replace(".", "/") + ".py")).is_file():
                    modules.add(module)
    return modules


def _entry_points() -> set[str]:
    project = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {
        str(target).partition(":")[0]
        for group in project.get("entry-points", {}).values()
        for target in group.values()
    }


def _sdk_modules() -> set[str]:
    return {"personalclaw.sdk"} | {
        f"personalclaw.sdk.{p.stem}" for p in (_SRC / "sdk").glob("*.py") if p.stem != "__init__"
    }


def _required_deps() -> set[str]:
    """The modules the gateway's own dependency check asks for (``Gateway._REQUIRED_DEPS``)."""
    tree = ast.parse((_SRC / "gateway.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_REQUIRED_DEPS" for t in node.targets
        ):
            return {str(row[0]) for row in ast.literal_eval(node.value)}
    raise LookupError("gateway.py declares no _REQUIRED_DEPS")


#: Each load site whose names the walk cannot follow: a function that finds them, or the reason
#: the bundle need not carry what it loads.
_CLASSIFIED: dict[str, Callable[[], set[str]] | str] = {
    "agents/native/tools.py::InProcessMcpToolProvider._import_module": _in_process_tool_modules,
    "_frozen_child.py::run": lambda: _constant("_frozen_child.py", "CHILD_MODULES"),
    "evals/gate.py::_render_candidate": lambda: _constant("evals/gate.py", "_CANDIDATE_RENDERERS"),
    "providers/loader.py::_load_ext_module": _native_factories,
    "apps/app_manager.py::_core_factory_is_gone": _native_factories,
    "provider_registry.py::discover_providers": _entry_points,
    "cli_app_new.py::_members_for": _sdk_modules,
    "cli_app_new.py::resolve_kit": _sdk_modules,
    "knowledge/readers.py::_library": lambda: _first_strings(
        "_library", _SRC / "knowledge" / "readers.py"
    ),
    "_sdk_deps.py::require_sdk": lambda: _first_strings("require_sdk"),
    "gateway.py::GatewayOrchestrator._check_missing_deps": _required_deps,
    "sdk/availability.py::missing_modules": (
        "the modules an app declares it needs, asked of to say whether the app can run here: "
        "the app's to bring, never the package's"
    ),
    "net/libraries.py::_hub_http": (
        "a module of the model hub library, which the desktop app does not carry: the apps that "
        "use it run a Python interpreter of their own, and this reads the library as absent"
    ),
    "_installer.py::_have_pip": (
        "pip, which the desktop app does not have: it installs no Python packages, and this is "
        "the check that says so"
    ),
    "triggers/disposition.py::missing_surfaces": (
        "the disposition table's modules, asked of by a test; nothing in the product imports "
        "this module"
    ),
    "evals/voice_engine_bakeoff.py::measure_fixture_rtf": (
        "a voice engine's own module, asked of by the engine bake-off, which runs from a "
        "checkout; nothing in the product imports this module"
    ),
}


@pytest.fixture(scope="module")
def census(manifest) -> dict[str, list[Site]]:
    return _census(_SRC, manifest)


def _resolved(census: dict[str, list[Site]]) -> dict[str, set[str]]:
    """Every site the bundle must carry the names of, and those names."""
    out: dict[str, set[str]] = {}
    for key, sites in census.items():
        how = _CLASSIFIED.get(key)
        if isinstance(how, str):
            continue
        names: set[str] = set()
        for site in sites:
            if site.names is not None:
                names |= site.names
        if callable(how):
            names |= how()
        out[key] = names
    return out


# ── The rail ─────────────────────────────────────────────────────────────────


def test_the_census_is_not_vacuous(census):
    """A floor: the walk finds the loads it exists for, the agent's tool modules among them."""
    for key in (
        "mcp_core.py::_aggregated_list_tools",
        "mcp_core.py::_aggregated_call_tool",
        "agents/native/tools.py::InProcessMcpToolProvider._import_module",
        "documents/registry.py::_ensure_registered",
        "workflows/bundled_defs.py::bundled_root",
    ):
        assert key in census, sorted(census)
    assert len(census) >= 25, sorted(census)


@pytest.mark.parametrize("module", _LOST_IN_THE_DESKTOP_APP)
def test_the_census_finds_what_the_desktop_app_lost(census, module):
    """The positive control: each module the bundle left out is one a site is found loading."""
    loaded = {name for names in _resolved(census).values() for name in names}
    assert module in loaded


def test_every_load_site_is_followed_or_classified(census):
    unfollowed = sorted(
        key
        for key, sites in census.items()
        if any(site.names is None for site in sites) and key not in _CLASSIFIED
    )
    assert not unfollowed, (
        "a module is loaded here by a name the census cannot follow. Name it as a string, a "
        "module constant or a package prefix, or classify the site in _CLASSIFIED with how its "
        f"names are found or why the bundle need not carry them: {unfollowed}"
    )
    stale = sorted(set(_CLASSIFIED) - set(census))
    assert not stale, f"classified sites that load nothing any more: {stale}"


def test_every_package_module_a_site_loads_is_carried(census, manifest):
    """The rail the desktop app needed: a module the package loads by name is in the bundle."""
    carried = set(manifest.by_name_modules(_REPO)) | set(manifest.sdk_submodules(_REPO))
    missing = sorted(
        f"{name} (loaded at {key})"
        for key, names in _resolved(census).items()
        for name in names
        if name.split(".", 1)[0] == "personalclaw" and name not in carried
    )
    assert not missing, f"the bundle does not carry what these sites load: {missing}"


def _spec_strings(call: str) -> set[str]:
    """The strings the spec passes to *call* (``collect_submodules``) or lists into ``hidden``,
    or names in its ``excludes``."""
    tree = ast.parse(_SPEC.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if call == "excludes" and isinstance(node, ast.keyword) and node.arg == "excludes":
            found |= set(_texts(_literal(node.value)))
        elif isinstance(node, ast.Call) and _dotted(node.func, {}) == call and node.args:
            found |= set(_texts(_literal(node.args[0])))
        elif (
            call == "collect_submodules"
            and isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "hidden"
        ):
            found |= set(_texts(_literal(node.value)))
    return found


def _imported_in_the_package(skip: Path | None = None) -> set[str]:
    """The top-level name of every module an import statement in the package names (outside
    *skip*)."""
    tops: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if skip is not None and path.is_relative_to(skip):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                tops |= {a.name.split(".", 1)[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                tops.add(node.module.split(".", 1)[0])
    return tops


def test_every_other_module_a_site_loads_is_carried_or_left_out_on_purpose(census):
    """A library loaded by its name reaches the bundle only if the analysis sees it elsewhere
    (an import statement in the package), the spec collects it, or the spec excludes it, the
    code reading it as absent."""
    known = (
        set(sys.stdlib_module_names)
        | _imported_in_the_package()
        | _spec_strings("collect_submodules")
        | _spec_strings("excludes")
    )
    missing = sorted(
        f"{name} (loaded at {key})"
        for key, names in _resolved(census).items()
        for name in names
        if name.split(".", 1)[0] != "personalclaw" and name.split(".", 1)[0] not in known
    )
    assert not missing, f"the bundle carries none of these, nor excludes them: {missing}"


def test_the_spec_collects_what_the_sdk_guard_loads():
    """``require_sdk`` loads a model SDK by its name, so the spec collects each one whole."""
    named = _first_strings("require_sdk")
    assert named, "no require_sdk call found: the check would be vacuous"
    assert named <= _spec_strings("collect_submodules"), sorted(named)


class TestTheNativeAppsReachTheBundle:
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

    def test_what_their_own_files_import_is_carried(self, manifest):
        """The loader reads them by their path, so the analysis never does: what they import is
        named to the bundle, and the standard library's is a module the package imports too."""
        files = manifest.native_app_files(_REPO)
        assert len(files) >= 4, files
        carried = set(manifest.path_loaded_imports(_REPO))
        assert {"numpy", "httpx", "aiohttp", "personalclaw.sdk.model"} <= carried
        native = _SRC / "apps" / "native"
        imported = _imported_in_the_package(skip=native)
        for path in files:
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".", 1)[0]
                    if top in sys.stdlib_module_names:
                        assert top in imported, f"{path.parent.name}: {name}"
                        assert name not in carried, "a standard-library name is the analysis's"
                    else:
                        assert name in carried, f"{path.parent.name}: {name}"


class TestTheBundleNamesOnlyWhatExists:
    def test_every_name_is_a_module_of_the_package(self, manifest):
        """The build looks for each name, so a name that is no module warns on every build."""
        modules = manifest.package_modules(_REPO)
        names = manifest.by_name_modules(_REPO)
        assert set(_LOST_IN_THE_DESKTOP_APP) <= set(names)
        assert not [n for n in names if n not in modules], "a name that is no module"
        assert "personalclaw.inbox_providers.slack_source" not in names
        assert "provider" not in names, "an app's own factory module is data, not a module"

    def test_the_spec_names_no_first_party_module_by_hand(self):
        """Every first-party module the bundle is told about is derived; the spec names none."""
        tree = ast.parse(_SPEC.read_text(encoding="utf-8"))
        named = sorted(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("personalclaw.")
        )
        assert named == [], named

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


def test_the_census_reports_a_name_it_cannot_follow(manifest, tmp_path):
    """The defect arm: a load whose name the walk cannot follow is a site to classify."""
    src = tmp_path / "src" / "personalclaw"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "late.py").write_text(
        "import importlib\n"
        "TOOLS = ('personalclaw.followed',)\n"
        "def by_table():\n"
        "    for name in TOOLS:\n"
        "        importlib.import_module(name)\n"
        "def by_whatever(row):\n"
        "    importlib.import_module(row.module)\n",
        encoding="utf-8",
    )
    sites = _census(src, manifest)
    assert [s.names for s in sites["late.py::by_table"]] == [{"personalclaw.followed"}]
    assert [s.names for s in sites["late.py::by_whatever"]] == [None]
