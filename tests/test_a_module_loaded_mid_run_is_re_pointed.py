"""A module that binds the home resolver is re-pointed at the next test, however it got loaded.

``_isolate_real_home_writers`` re-points every loaded ``personalclaw`` module that bound
``resolve_config_dir`` at import, and ``_restore_provider_registry`` does the same
for the registry accessor. Both find those modules through ``conftest._LoadedModules``, which looks
again only when ``sys.modules`` changed. These are the shapes a weaker sense of "changed" misses,
each run in a session of its own, serially, in order, with this suite's conftest as the plugin:

* a module imported between two tests is re-pointed (a cache that never looks again misses it);
* one module popped and another imported keeps ``len(sys.modules)`` the same, and the new one is
  still re-pointed (a cache keyed on the count misses it);
* a module popped and imported again keeps every NAME the same, and the new copy is still
  re-pointed (a cache keyed on the names misses it, and this is what
  ``_forget_apps_registered_elsewhere`` and many tests do);
* a module reloaded in place keeps ``sys.modules`` exactly the same, and when its test's patches
  are undone its binding is the loader's own again: it is re-pointed at the next test all the
  same (a cache that remembered which modules it had already re-pointed misses it). This is the
  shape of ``tests/test_ssl_certs.py``, which reloads ``personalclaw.cli``, a module that binds
  ``resolve_config_dir`` at import.

The registry guard keeps, on the same record, which app modules were loaded as each test was set
up, and forgets only the apps a test itself first loaded. The last three tests hold that: an app an
earlier test loaded, whose type a later test leaves unregistered, is still loaded after that later
test ends (a record of the apps that is never brought up to date would count it as the later
test's own, and forget it).

Each test that depends on a premise about ``sys.modules`` asserts the premise first, so a run in
which the premise did not hold fails rather than passing for the wrong reason.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent

_RELOADED = """
from personalclaw.config.loader import resolve_config_dir  # noqa: F401
"""

_ORDER = """
import importlib
import sys
import types

import personalclaw.config.loader as loader
import personalclaw_probe_reloaded
from personalclaw.config.loader import resolve_config_dir as ORIGINAL

_SEEN = {}


def _plant(name, *, binds):
    module = types.ModuleType(name)
    if binds:
        module.resolve_config_dir = ORIGINAL
    sys.modules[name] = module


def _re_pointed(name):
    bound = sys.modules[name].resolve_config_dir
    return bound is loader.resolve_config_dir and bound is not ORIGINAL


def test_00_every_lazy_import_happens_first():
    _plant("personalclaw_probe_filler", binds=False)
    _plant("personalclaw_probe_same", binds=False)


def test_01_settles():
    pass


def test_02_imports_a_module_that_binds_the_resolver():
    _plant("personalclaw_probe_new", binds=True)


def test_03_a_new_module_is_re_pointed():
    assert _re_pointed("personalclaw_probe_new")


def test_04_pops_one_module_and_imports_another():
    sys.modules.pop("personalclaw_probe_filler")
    _plant("personalclaw_probe_other", binds=True)
    _SEEN["length"] = len(sys.modules)


def test_05_the_same_count_still_re_points_the_new_module():
    assert len(sys.modules) == _SEEN["length"], "premise: the count did not change"
    assert _re_pointed("personalclaw_probe_other")


def test_06_pops_a_module_and_imports_it_again():
    sys.modules.pop("personalclaw_probe_same")
    _plant("personalclaw_probe_same", binds=True)
    _SEEN["names"] = set(sys.modules)


def test_07_the_same_names_still_re_point_the_new_copy():
    assert set(sys.modules) == _SEEN["names"], "premise: no name changed"
    assert _re_pointed("personalclaw_probe_same")


def test_08_reloads_a_module_in_place():
    module = personalclaw_probe_reloaded
    assert _re_pointed("personalclaw_probe_reloaded")
    assert importlib.reload(module) is module
    _SEEN["modules"] = dict(sys.modules)


def test_09_a_reloaded_module_is_re_pointed_again():
    assert sys.modules == _SEEN["modules"], "premise: sys.modules did not change"
    assert _re_pointed("personalclaw_probe_reloaded")


_APP = "_pclaw_app_held__provider"
_HELD_TYPE = "held-probe-type"


def test_10_an_earlier_test_loads_an_app_whose_type_is_registered():
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import get_default_registry

    capability = ProviderCapability(
        type=_HELD_TYPE,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=False,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=0,
    )
    get_default_registry().register_type(capability, lambda **_kw: None)
    module = types.ModuleType(_APP)
    module.CAPABILITY = capability
    sys.modules[_APP] = module


def test_11_a_later_test_leaves_that_type_unregistered():
    from personalclaw.llm.registry import get_default_registry

    assert _APP in sys.modules, "premise: the app was kept as its own test ended"
    get_default_registry()._capabilities.pop(_HELD_TYPE)


def test_12_an_app_loaded_before_the_test_is_not_forgotten_by_it():
    assert _APP in sys.modules
"""


def test_each_way_a_module_arrives_mid_run_is_re_pointed(tmp_path: Path) -> None:
    (tmp_path / "personalclaw_probe_reloaded.py").write_text(textwrap.dedent(_RELOADED))
    (tmp_path / "test_order.py").write_text(textwrap.dedent(_ORDER))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "PERSONALCLAW_"))}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(_TESTS_DIR), str(tmp_path), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    # This suite's own conftest, as a plugin, over one file, in order, on one process.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "-q",
            "--color=no",
            "test_order.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "13 passed" in proc.stdout, proc.stdout[-4000:] + proc.stderr[-2000:]
