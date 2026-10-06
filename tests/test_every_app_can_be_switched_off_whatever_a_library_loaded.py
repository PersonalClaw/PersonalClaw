"""Every app can be deactivated, uninstalled and force-uninstalled, whatever a library has loaded.

Each of those controls ends by taking the app's code out of the gateway, which reads every module
the process has loaded to find the ones that are the app's. torch, which the Sentence Transformers
app loads, keeps a module whose attribute namespace answers any name with a new namespace, truthy
and not iterable, and that is the answer it gives for ``__path__``. Once it was loaded, reading it
raised ``TypeError`` for every app: Deactivate, Uninstall and Force uninstall each answered 400
"The request was malformed or carried an unusable parameter.", and the app stayed on, its
scheduled work with it. An app shipping a package folder with no ``__init__.py`` inside its own
package could never be switched off either: the scan read that folder's search path after taking
its parent package out, which raised ``KeyError``. And whatever else the scan meets that it
cannot get past, a library's import hook that fails when asked to drop its caches among them,
switches the app off all the same and says a restart will finish the job.

The library below is invented and shaped like torch's module. Two apps are installed through the
Store's routes, so their code is loaded and claimed as in the gateway: one whose code imports the
library, and one that has nothing to do with it. Every control is the request a click sends,
behind the request boundary the dashboard runs these routes behind.
"""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# Imported before any test patches `config_dir`: the probes import the SDK, and a module first
# imported under a patch keeps the mock bound.
import personalclaw.sdk.skill  # noqa: F401
from personalclaw import app_code
from personalclaw.apps import app_runtime, manager
from personalclaw.apps.native_contract import namespaced_module_name
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.dashboard.request_boundary import request_boundary_middleware
from personalclaw.providers import registry as registry_module

LIBRARY = "pc_probe_lazy_lib"
NAMESPACES = f"{LIBRARY}.classes"
LOADS_IT = "lazy-probe"  # its code imports the library, as Sentence Transformers imports torch
QUIET = "quiet-probe"  # has nothing to do with the library
PACKAGE = "pc_probe_quiet_tools"  # the quiet app's own package, with a folder that has no __init__

# A module whose attribute namespace answers every name it does not hold with a new namespace, and
# a namespace that refuses every name: the shape of torch's `torch.classes`. The library puts the
# first in `sys.modules` when it is imported.
_LIBRARY_PY = '''
"""An invented library whose attribute namespaces answer any name, as torch's do."""

import sys
import types


class _Namespace(types.ModuleType):
    def __init__(self, name):
        super().__init__(name)
        self.short = name

    def __getattr__(self, attr):
        raise RuntimeError(f"{self.short}.{attr} is not registered")


class _Namespaces(types.ModuleType):
    __file__ = "_namespaces.py"

    def __getattr__(self, name):
        namespace = _Namespace(name)
        setattr(self, name, namespace)
        return namespace


classes = _Namespaces(__name__ + ".classes")
sys.modules[classes.__name__] = classes
'''

_PROVIDER_PY = '''
"""The probe app's provider: a skills catalogue that offers nothing."""

{imports}

def create_provider(config=None):
    return None
'''


def _source(
    home: Path, name: str, *, imports: str = "", files: dict[str, str] | None = None
) -> Path:
    """An app's source folder, for the Store to install."""
    d = home / "src" / name
    d.mkdir(parents=True)
    (d / "provider.py").write_text(textwrap.dedent(_PROVIDER_PY).format(imports=imports))
    for rel, body in (files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name.replace("-", " ").title(),
        "description": "A probe app whose code the gateway loads.",
        "provider": {
            "type": "skills",
            "implementation": "provider:create_provider",
            "capabilities": ["search"],
        },
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


@pytest.fixture
def home(tmp_path, monkeypatch) -> Iterator[Path]:
    """An isolated home with fresh app-code and provider ledgers, and the library on the path."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    for attr, empty in (("_roots", {}), ("_undo", {}), ("_parked", {}), ("_released", set())):
        monkeypatch.setattr(app_code, attr, empty)
    monkeypatch.setattr(app_code, "_loaded", ())
    monkeypatch.setattr(app_runtime, "_restart", {})
    site = tmp_path / "site"
    site.mkdir()
    (site / f"{LIBRARY}.py").write_text(textwrap.dedent(_LIBRARY_PY))
    monkeypatch.syspath_prepend(str(site))
    registry_module.reset_provider_registry()
    yield tmp_path
    registry = registry_module.get_provider_registry()
    for name in list(registry._extensions):
        registry.disable(name)
    registry_module.reset_provider_registry()
    for name in (LIBRARY, NAMESPACES):
        sys.modules.pop(name, None)
    for name, module in list(sys.modules.items()):  # the probes' own modules
        if _file_of(module).startswith(str(tmp_path)):
            sys.modules.pop(name, None)


def _file_of(module: object) -> str:
    """The file a loaded module came from, read from its own namespace (none of its code runs)."""
    try:
        return str(object.__getattribute__(module, "__dict__").get("__file__") or "")
    except Exception:  # noqa: BLE001 — anything else in sys.modules is no probe's
        return ""


@asynccontextmanager
async def _store() -> AsyncIterator[TestClient]:
    app = web.Application(middlewares=[request_boundary_middleware()])
    register_app_routes(app)
    async with TestClient(TestServer(app)) as client:
        yield client


async def _install(client: TestClient, source: Path) -> None:
    review = await client.post("/api/apps/preview", json={"source": str(source)})
    assert review.status == 200, await review.text()
    consent = (await review.json())["consent"]
    done = await client.post("/api/apps", json={"source": str(source), "consent": consent})
    assert done.status == 201, await done.text()


_CONTROLS = {
    "deactivate": ("POST", "/api/apps/{name}/disable"),
    "uninstall": ("DELETE", "/api/apps/{name}"),  # switched off, files kept
    "uninstall-keep-data": ("DELETE", "/api/apps/{name}?remove=1"),
    "force-uninstall": ("DELETE", "/api/apps/{name}?force=1"),
}


async def _switch_off(client: TestClient, control: str, name: str) -> None:
    """Press *control* for *name*, and check it did what it says."""
    method, path = _CONTROLS[control]
    resp = await client.request(method, path.format(name=name))
    assert resp.status == 200, f"{control} {name} answered {resp.status}: {await resp.text()}"
    after = await client.get(f"/api/apps/{name}")
    if control in ("deactivate", "uninstall"):
        assert after.status == 200, await after.text()
        assert (await after.json())["installed"]["enabled"] is False, f"{name} is still on"
    else:
        assert after.status == 404, f"{name} is still installed: {await after.text()}"
    assert namespaced_module_name(name, "provider") not in sys.modules, "its code is still loaded"


@pytest.mark.asyncio
@pytest.mark.parametrize("control", list(_CONTROLS))
@pytest.mark.parametrize("target", [QUIET, LOADS_IT], ids=["another-app", "the-app-that-loaded-it"])
async def test_an_app_switches_off_with_the_librarys_namespaces_loaded(home, control, target):
    async with _store() as client:
        await _install(client, _source(home, LOADS_IT, imports=f"import {LIBRARY}  # noqa: F401"))
        await _install(client, _source(home, QUIET))
        odd = sys.modules.get(NAMESPACES)
        assert odd is not None, "vacuity floor: the app's code loaded the library"
        assert getattr(odd, "__path__"), "vacuity floor: its __path__ is a truthy namespace"

        await _switch_off(client, control, target)


@pytest.mark.asyncio
@pytest.mark.parametrize("control", list(_CONTROLS))
async def test_an_app_with_a_package_folder_that_has_no_init_switches_off(home, control):
    """A folder with no ``__init__.py`` inside the app's own package is a namespace package, whose
    search path the scan used to recalculate from its parent after it had taken the parent out."""
    files = {f"{PACKAGE}/__init__.py": "", f"{PACKAGE}/parts/clip.py": "CLIP = 1\n"}
    source = _source(home, QUIET, imports=f"import {PACKAGE}.parts.clip  # noqa: F401", files=files)
    loaded = {PACKAGE, f"{PACKAGE}.parts", f"{PACKAGE}.parts.clip"}
    async with _store() as client:
        await _install(client, source)
        assert loaded <= set(sys.modules), "vacuity floor: the app loaded its namespace package"

        await _switch_off(client, control, QUIET)
        assert not loaded & set(sys.modules), "the app's package is still loaded"


class _ImportHookThatCannotDropItsCaches:
    """A library's import hook: it finds nothing, and fails when asked to drop its caches."""

    def find_spec(self, fullname, path=None, target=None):
        return None

    def invalidate_caches(self):
        raise RuntimeError("this hook keeps no caches it can drop")


@pytest.mark.asyncio
@pytest.mark.parametrize("control", list(_CONTROLS))
async def test_an_app_switches_off_past_what_the_scan_cannot_get_past(home, control, monkeypatch):
    async with _store() as client:
        await _install(client, _source(home, QUIET))
        monkeypatch.setattr(
            sys, "meta_path", [*sys.meta_path, _ImportHookThatCannotDropItsCaches()]
        )

        await _switch_off(client, control, QUIET)
    assert app_runtime.restart_reason(QUIET) == (
        "its code could not all be taken out of the gateway, and the gateway log says why"
    )
