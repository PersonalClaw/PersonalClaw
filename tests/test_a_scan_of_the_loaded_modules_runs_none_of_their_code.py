"""Every scan core makes of the process's loaded modules reads them without running their code.

A library may put anything in ``sys.modules``, and some of what real libraries put there answers
an attribute read with code of its own: a namespace that builds a new namespace for any name
(truthy, not iterable), one that raises for every name, a proxy that resolves itself on first
touch, a spec whose ``origin`` is computed, a ``__path__`` that cannot be read, an import blocker
(``None``), and a lazy module that changes ``sys.modules`` itself when it is asked for something.
Each scan below can meet one of them in a running gateway: taking an app out (Deactivate,
Uninstall, an update), running one app's ``setup``/``doctor`` step after another's, the check after
an install of whether a restart is needed, the cache of an app's loaded provider module, and the
SDK's availability helper. The first of them met torch's, and no app could be switched off.

Each shape here is invented and modelled on one a real library uses. The fixture records every
name the odd modules were asked for, so "none of their code ran" is observed, not assumed.
"""

from __future__ import annotations

import gc
import sys
import textwrap
import threading
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from personalclaw import app_code
from personalclaw.apps import app_python
from personalclaw.apps.native_contract import load_bundle_module, namespaced_module_name

APP = "scan-probe"
OTHER = "scan-other"

#: Every attribute an odd module was asked for, by any scan. Must stay empty.
ASKED: list[str] = []


class _Namespace(types.ModuleType):
    """Any name it does not hold is another namespace: truthy, and not iterable."""

    def __getattr__(self, name: str) -> object:
        ASKED.append(f"{self.__name__}.{name}")
        return _Namespace(f"{self.__name__}.{name}")


class _RefusesEveryName(types.ModuleType):
    """Any name it does not hold raises, and not ``AttributeError``."""

    def __getattr__(self, name: str) -> object:
        ASKED.append(f"{self.__name__}.{name}")
        raise RuntimeError(f"{name} is not registered")


class _Proxy:
    """Not a module at all: every attribute resolves a target that cannot be resolved."""

    def __getattribute__(self, name: str) -> object:
        ASKED.append(f"proxy.{name}")
        raise LookupError("the proxied object is not available yet")


class _ComputedSpec:
    @property
    def origin(self) -> str:
        ASKED.append("spec.origin")
        raise RuntimeError("the origin is computed on demand")


class _UnreadablePath:
    def __iter__(self) -> Iterator[str]:
        ASKED.append("path.__iter__")
        raise RuntimeError("the search path is not ready")

    def __len__(self) -> int:
        ASKED.append("path.__len__")
        raise RuntimeError("the search path is not ready")


def _odd_modules() -> dict[str, object]:
    with_computed_spec = types.ModuleType("pc_probe_computed_spec")
    with_computed_spec.__spec__ = _ComputedSpec()  # type: ignore[assignment]
    with_unreadable_path = types.ModuleType("pc_probe_unreadable_path")
    with_unreadable_path.__path__ = _UnreadablePath()  # type: ignore[assignment]
    with_a_path_for_a_file = types.ModuleType("pc_probe_path_object_file")
    with_a_path_for_a_file.__file__ = Path("/nowhere/at/all.py")  # type: ignore[assignment]
    return {
        "pc_probe_namespaces": _Namespace("pc_probe_namespaces"),
        "pc_probe_refuses": _RefusesEveryName("pc_probe_refuses"),
        "pc_probe_proxy": _Proxy(),
        "pc_probe_computed_spec": with_computed_spec,
        "pc_probe_unreadable_path": with_unreadable_path,
        "pc_probe_path_object_file": with_a_path_for_a_file,
        "pc_probe_blocked": None,
    }


@pytest.fixture
def odd(monkeypatch) -> Iterator[dict[str, object]]:
    """The odd modules, in ``sys.modules`` for the test and gone after it."""
    modules = _odd_modules()
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    ASKED.clear()
    yield modules
    ASKED.clear()


@pytest.fixture
def root(tmp_path, monkeypatch) -> Iterator[Path]:
    """An app directory and a clean app-code ledger."""
    monkeypatch.setattr(app_code, "_roots", {})
    monkeypatch.setattr(app_code, "_undo", {})
    monkeypatch.setattr(app_code, "_parked", {})
    monkeypatch.setattr(app_code, "_released", set())
    monkeypatch.setattr(app_code, "_loaded", ())
    d = tmp_path / "apps" / APP
    d.mkdir(parents=True)
    before = set(sys.modules)
    yield d
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return path


def _app_with_packages(root: Path) -> set[str]:
    """Load an app whose code spans a module, a package, and a folder with no ``__init__.py``
    inside that package; return the names its modules are loaded under."""
    _write(root / "scanprobe_pkg" / "__init__.py", "")
    _write(root / "scanprobe_pkg" / "loose" / "part.py", "PART = 1\n")
    _write(root / "provider.py", "import scanprobe_pkg.loose.part  # noqa: F401\n")
    load_bundle_module(root, APP, "provider")
    loaded = {
        namespaced_module_name(APP, "provider"),
        "scanprobe_pkg",
        "scanprobe_pkg.loose",
        "scanprobe_pkg.loose.part",
    }
    assert loaded <= set(sys.modules), "vacuity floor: the app's code is loaded"
    return loaded


# ── taking an app out: Deactivate, each Uninstall, the start of an update ────────────────────


def test_releasing_an_app_reads_no_odd_module_and_unloads_all_of_its_own(root, odd):
    loaded = _app_with_packages(root)

    released = app_code.release(APP)

    assert loaded <= set(released.modules)
    assert not loaded & set(sys.modules), "the app's code is still loaded"
    assert released.left_running == []
    assert ASKED == [], f"the scan ran the odd modules' code: {ASKED}"
    for name, module in odd.items():
        assert sys.modules.get(name, "gone") is module, f"{name} was taken out with the app"


def test_releasing_an_app_with_a_module_that_changes_sys_modules_when_asked(root):
    """A lazy module that, asked for anything, takes other modules out of ``sys.modules``: what
    another thread can do at any moment of the scan. It was loaded before the app, so a scan that
    asks it comes to it before the app's own modules."""
    loaded: set[str] = set()

    class _Shuffles(types.ModuleType):
        def __getattr__(self, name: str) -> object:
            ASKED.append(f"shuffles.{name}")
            for gone in loaded:
                sys.modules.pop(gone, None)
            raise AttributeError(name)

    sys.modules["pc_probe_shuffles"] = _Shuffles("pc_probe_shuffles")
    try:
        loaded |= _app_with_packages(root)
        ASKED.clear()
        released = app_code.release(APP)
    finally:
        sys.modules.pop("pc_probe_shuffles", None)
    assert ASKED == [], f"the scan ran the lazy module's code: {ASKED}"
    assert loaded <= set(released.modules)
    assert not loaded & set(sys.modules)


def test_a_module_removed_or_replaced_since_the_scans_copy_is_left_as_it_is_now(monkeypatch):
    """The scan reads one copy of ``sys.modules``; another thread may since have removed a module
    it is about to take out, or imported a new one under its name, which is not the scan's."""
    from personalclaw import loaded_modules

    old = types.ModuleType("pc_probe_swapped")
    new = types.ModuleType("pc_probe_swapped")
    monkeypatch.setitem(sys.modules, "pc_probe_swapped", new)

    loaded_modules.forget("pc_probe_swapped", old)
    assert sys.modules["pc_probe_swapped"] is new

    loaded_modules.forget("pc_probe_never_loaded", old)
    assert "pc_probe_never_loaded" not in sys.modules


def test_one_apps_cli_step_runs_alone_whatever_the_process_has_loaded(root, odd, tmp_path):
    """``personalclaw setup`` and ``doctor`` run each app's step with no other app's modules."""
    other = tmp_path / "apps" / OTHER
    _write(other / "provider.py", "WHO = 'other'\n")
    load_bundle_module(other, OTHER, "provider")
    others_module = namespaced_module_name(OTHER, "provider")
    assert others_module in sys.modules

    with app_code.alone(APP):
        assert others_module not in sys.modules, "another app's module is in the step's way"
    assert others_module in sys.modules, "another app's module was not put back"
    assert ASKED == [], f"the step's scan ran the odd modules' code: {ASKED}"


def test_a_proxy_anywhere_in_the_process_does_not_stop_the_look_for_left_running_tasks(root):
    """The release looks for coroutines of the app's code still suspended, among every object
    the process holds; a proxy among them answers ``__class__`` with code of its own."""

    class _ProxyObject:
        @property  # type: ignore[misc]
        def __class__(self):  # noqa: D401 — the shape a lazy object proxy has
            ASKED.append("proxy.__class__")
            raise LookupError("the proxied object is not available yet")

    kept = [_ProxyObject()]  # gc-tracked while the release runs
    gc.collect()
    _write(root / "provider.py", "WHO = 'probe'\n")
    load_bundle_module(root, APP, "provider")
    ASKED.clear()

    released = app_code.release(APP)

    assert released.left_running == []
    assert ASKED == [], f"the look ran the proxy's code: {ASKED}"
    assert kept


def test_a_thread_whose_name_cannot_be_read_does_not_stop_the_look_for_left_running_threads(
    root,
):
    _write(
        root / "provider.py",
        """
        def wait(event):
            event.wait()
        """,
    )
    module = load_bundle_module(root, APP, "provider")

    class _Unnamed(threading.Thread):
        @property  # type: ignore[override]
        def name(self) -> str:
            raise RuntimeError("this thread's name is computed and not ready")

        @name.setter
        def name(self, value: str) -> None:
            pass

    release = threading.Event()
    waiting = _Unnamed(target=module.wait, args=(release,), daemon=True)
    waiting.start()
    try:
        left = app_code.release(APP).left_running
    finally:
        release.set()
        waiting.join(5)
    assert len(left) == 1 and left[0].startswith(
        "a thread its previous version started is still running (thread "
    ), left


# ── the other scans in the family ─────────────────────────────────────────────────────────────


def test_whether_an_install_needs_a_restart_reads_no_odd_module(odd, tmp_path):
    """After an install, whether the process had loaded anything from the app packages."""
    packages = tmp_path / "app-python"
    _write(packages / "pc_probe_installed_dep.py", "VALUE = 1\n")
    assert not app_python._loaded_from(packages)

    sys.path.insert(0, str(packages))
    try:
        import pc_probe_installed_dep  # noqa: F401
    finally:
        sys.path.remove(str(packages))
    try:
        assert app_python._loaded_from(packages)
    finally:
        sys.modules.pop("pc_probe_installed_dep", None)
    assert ASKED == [], f"the check ran the odd modules' code: {ASKED}"


def test_an_apps_provider_module_cached_under_its_name_is_read_without_running_it(root):
    """The loader asks the module cached under the app's namespaced name where it came from;
    an object put there by anything else is read, not run, and the files on disk load."""
    _write(root / "provider.py", "WHO = 'disk'\n")
    name = namespaced_module_name(APP, "provider")
    sys.modules[name] = _RefusesEveryName(name)
    ASKED.clear()

    module = load_bundle_module(root, APP, "provider")

    assert module.WHO == "disk"
    assert ASKED == [], f"the cache check ran the odd module's code: {ASKED}"


@pytest.mark.parametrize(
    ("name", "installed"),
    [
        ("pc_probe_namespaces", True),
        ("pc_probe_refuses", True),
        ("pc_probe_proxy", True),
        ("pc_probe_computed_spec", True),
        ("pc_probe_blocked", False),  # None in sys.modules: `import` refuses it
    ],
)
def test_the_sdks_availability_helper_answers_for_a_loaded_module_without_reading_it(
    odd, name, installed
):
    from personalclaw.sdk.availability import missing_modules, modules_installed

    assert modules_installed(name) is installed
    assert missing_modules(name) == ([] if installed else [name])
    assert ASKED == [], f"the helper ran the odd module's code: {ASKED}"
