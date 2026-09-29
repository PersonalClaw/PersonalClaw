"""Importing PersonalClaw creates and opens nothing; a command that needs no home makes none; the
offline reference renders with no home.

Importing a module is not running the product. A test collecting, a docs generator, an editor's
language server, ``python -c "import personalclaw.x"`` — none of them asked for a home, and none
of them should find one created, read or written because they imported something.

Measured on ``main`` before this rail existed, in a child with ``HOME`` and ``PERSONALCLAW_HOME``
pointed at empty directories:

* seven module bodies resolved the home while being imported — ``agent`` (``_USER_DIR`` and
  ``_DEFAULT_HOOKS_DIR``), ``agents.marketplace`` (the ``local`` registry it registers),
  ``skills.marketplace`` (``SKILL_DISCOVERY_PATHS``), ``skills.native`` (the ``installed``
  registry), ``dashboard.handlers.hooks`` (``_HOOK_STORE_PATH``) and the bundled-chat app — and
  the first of them to run CREATED the home, because ``config_dir()`` creates what it resolves;
* ``python -m personalclaw.manifest_reference`` — a generator of checked-in markdown — created the
  home, copied fifteen bundled skills into it, and created ``memory.db`` with its WAL, a FAISS
  index and an ids file, because rendering the tool list enabled every bundled provider.

Why a CHILD process: the suite's own interpreter imported ``personalclaw`` long before any test ran
(``conftest.py`` does it on line one), so an in-process check could only ever see modules nobody had
imported yet. The child installs an audit hook before its first ``personalclaw`` import and records
every ``open``, directory creation or listing, ``sqlite3.connect``, rename and delete aimed under
the scratch directory — so an access to a directory that already exists (``mkdir(exist_ok=True)``
on an existing home) is seen too, which a before/after listing alone would miss.

The native app bundles under ``apps/native/`` are deliberately outside "every module": they have no
``__init__.py`` and hyphenated names, so they are not importable as ``personalclaw.*`` at all. The
app platform LOADS them (``apps.native_contract.load_bundle_module``) at boot, into an established
home, and loading one is what registers it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"

#: Runs in the child. ``argv``: the scratch root, then ``import`` or ``reference``.
_CHILD = r"""
import importlib
import json
import os
import pkgutil
import sys
import traceback
import types

ROOT = os.path.realpath(sys.argv[1])
MODE = sys.argv[2]
PCLAW_HOME = os.path.realpath(os.environ["PERSONALCLAW_HOME"])
PATH_ARGS = {
    "open": (0,), "sqlite3.connect": (0,), "os.mkdir": (0,), "os.rmdir": (0,),
    "os.remove": (0,), "os.rename": (0, 1), "os.link": (0, 1), "os.symlink": (1,),
    "os.truncate": (0,), "os.utime": (0,), "os.chmod": (0,), "os.listdir": (0,),
    "os.scandir": (0,), "shutil.rmtree": (0,), "shutil.copytree": (0, 1),
    "shutil.copyfile": (0, 1),
}
touches = []
state = {"busy": False, "module": "<import personalclaw>"}


def under(path, root):
    if isinstance(path, (bytes, os.PathLike)):
        try:
            path = os.fsdecode(os.fspath(path))
        except (TypeError, ValueError):
            return None
    if not isinstance(path, str) or not path:
        return None
    if path.startswith("file:"):
        path = path[5:].split("?", 1)[0]
    if not os.path.isabs(path):
        return None
    path = os.path.normpath(path)
    return path if path == root or path.startswith(root + os.sep) else None


def hook(event, args):
    if state["busy"] or event not in PATH_ARGS:
        return
    state["busy"] = True
    try:
        for index in PATH_ARGS[event]:
            if index < len(args):
                hit = under(args[index], ROOT)
                if hit is not None:
                    frames = [
                        f"{f.filename.rpartition('/personalclaw/')[2]}:{f.lineno} {f.name}"
                        for f in traceback.extract_stack()[:-1]
                        if "/personalclaw/" in f.filename
                    ]
                    touches.append({"while": state["module"], "event": event,
                                    "path": os.path.relpath(hit, ROOT), "stack": frames[-6:]})
    finally:
        state["busy"] = False


sys.addaudithook(hook)
import personalclaw  # noqa: E402

failed = {}
done = 0
if MODE == "import":
    state["module"] = "pkgutil.walk_packages (imports each package it lists)"
    names = sorted({
        info.name
        for info in pkgutil.walk_packages(
            personalclaw.__path__,
            "personalclaw.",
            onerror=lambda name: failed.setdefault(name, "walk"),
        )
    })
    for name in names:
        if name.rpartition(".")[2] == "__main__":
            continue  # running the CLI is not importing it
        state["module"] = name
        try:
            importlib.import_module(name)
            done += 1
        except Exception as exc:  # noqa: BLE001 - recorded, not this rail's verdict
            failed[name] = f"{type(exc).__name__}: {exc}"[:160]
else:
    state["module"] = "render_reference()"
    from personalclaw.manifest_reference import render_reference

    done = len(render_reference())
state["busy"] = True  # from here the child's own reads are not the code under test

# A path frozen at import touches nothing, so the hook cannot see it: look for one held anywhere in
# a module's globals, a few references deep (a registered instance's attribute counts).
frozen = []
seen = set()
SKIP = (type, types.ModuleType, types.FunctionType, types.BuiltinFunctionType, types.MethodType,
        type(os.environ))


def scan(obj, where, depth):
    if depth > 5 or id(obj) in seen:
        return
    seen.add(id(obj))
    if isinstance(obj, (str, os.PathLike)):
        if under(obj, PCLAW_HOME) is not None:
            frozen.append(f"{where} = {os.fspath(obj)!s}")
        return
    if isinstance(obj, SKIP) or isinstance(obj, (int, float, bytes, bool)) or obj is None:
        return
    if isinstance(obj, dict):
        for key, value in list(obj.items())[:500]:
            scan(value, f"{where}[{key!r}]", depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for i, value in enumerate(list(obj)[:500]):
            scan(value, f"{where}[{i}]", depth + 1)
    else:
        attrs = getattr(obj, "__dict__", None)
        if isinstance(attrs, dict):
            for key, value in list(attrs.items())[:200]:
                scan(value, f"{where}.{key}", depth + 1)


for mod_name, module in sorted(sys.modules.items()):
    if mod_name == "personalclaw" or mod_name.startswith("personalclaw."):
        for key, value in list(vars(module).items()):
            if not key.startswith("__"):
                scan(value, f"{mod_name}.{key}", 0)

left = sorted(
    os.path.relpath(os.path.join(d, n), ROOT)
    for d, dirs, files in os.walk(ROOT)
    for n in dirs + files
)
print(json.dumps({"done": done, "failed": failed, "touches": touches, "frozen": frozen,
                  "left": left}))
"""


def _run_child(tmp_path: Path, mode: str) -> dict:
    """Run :data:`_CHILD` with ``HOME`` and ``PERSONALCLAW_HOME`` each an EMPTY directory."""
    root = (tmp_path / "scratch").resolve()
    (root / "home").mkdir(parents=True)
    (root / "pclaw-home").mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PERSONALCLAW_", "PYTEST_", "COV_CORE_"))
    }
    env["HOME"] = str(root / "home")
    env["PERSONALCLAW_HOME"] = str(root / "pclaw-home")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_SRC), env.get("PYTHONPATH", "")) if p)
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), mode],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=110,
    )
    assert proc.returncode == 0, f"the child failed:\n{proc.stderr[-4000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _render(touches: list[dict]) -> str:
    lines = []
    for t in touches[:25]:
        lines.append(f"  {t['event']} {t['path']}  (while {t['while']})")
        lines.extend(f"      {frame}" for frame in reversed(t["stack"]))
    return "\n".join(lines)


def test_importing_every_module_creates_and_opens_nothing(tmp_path: Path) -> None:
    result = _run_child(tmp_path, "import")
    # Non-vacuous: the walk really imported the package, not a handful of modules.
    assert (
        result["done"] >= 1000
    ), f"only {result['done']} modules imported — the walk broke: {result['failed']}"
    assert not result["touches"], (
        "importing personalclaw modules touched the scratch HOME / PERSONALCLAW_HOME — resolve "
        "the home when it is used, never at import:\n" + _render(result["touches"])
    )
    assert result["left"] == [
        "home",
        "pclaw-home",
    ], f"importing left something behind in the empty directories: {result['left']}"
    assert not result["frozen"], (
        "a module froze the home into a value at import — a home established after import (the "
        "supported way to isolate a test) can never move it:\n  " + "\n  ".join(result["frozen"])
    )


def _as_a_user(root: Path, argv: list[str]) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run *argv* the way a person runs PersonalClaw on a machine with no home: ``HOME`` an empty
    folder under *root* and no ``PERSONALCLAW_HOME``, so the home would be the default one inside
    that folder. Returns the process and what it left in that ``HOME``."""
    home = root / "home"
    home.mkdir(parents=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PERSONALCLAW_", "PYTEST_", "COV_CORE_", "XDG_"))
    }
    env["HOME"] = str(home)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_SRC), env.get("PYTHONPATH", "")) if p)
    proc = subprocess.run(
        [sys.executable, *argv], cwd=root, env=env, capture_output=True, text=True, timeout=110
    )
    assert proc.returncode == 0, f"the child failed:\n{proc.stderr[-4000:]}"
    return proc, sorted(str(p.relative_to(home)) for p in home.rglob("*"))


@pytest.mark.parametrize("argv", [["--version"], ["--help"], ["doctor", "--help"]])
def test_a_command_that_needs_no_home_makes_none(tmp_path: Path, argv: list[str]) -> None:
    """``personalclaw --version`` answers before any command runs. The start-up ahead of it worked
    out two paths in the home (a ``.env`` to read, the folders the libraries are told) with the
    resolver that CREATES the home, so every invocation made one, on a machine that had none and
    for a command that never needed it."""
    proc, left = _as_a_user((tmp_path / "scratch").resolve(), ["-m", "personalclaw", *argv])
    assert "personalclaw" in proc.stdout, "vacuity floor: the command answered"
    assert left == [], f"`personalclaw {' '.join(argv)}` left this in an empty HOME: {left}"


#: Writes a file outside every home, then stamps a built SPA the way ``make web-build`` does
#: (``scripts/spa_dist_freshness.py stamp``), in a repo-shaped tree under ``argv[1]``.
_WRITE_OUTSIDE = r"""
import sys
from pathlib import Path

from personalclaw.atomic_write import atomic_write, is_in_home
from personalclaw.frontend import write_spa_build_stamp

root = Path(sys.argv[1])
elsewhere = root / "elsewhere" / "note.txt"
elsewhere.parent.mkdir(parents=True)
print("in the home:", is_in_home(elsewhere))
atomic_write(elsewhere, "written")
web = root / "repo" / "web"
(root / "repo" / "src" / "personalclaw").mkdir(parents=True)
(web / "src").mkdir(parents=True)
(web / "src" / "App.tsx").write_text("export const App = () => null;\n")
(web / "index.html").write_text("<div id=root></div>")
(web / "vite.config.ts").write_text("export default {};\n")
(web / "package.json").write_text('{"name":"web"}')
(root / "repo" / "package-lock.json").write_text('{"lockfileVersion":3}')
(web / "dist").mkdir()
(web / "dist" / "index.html").write_text("<script src=/assets/app.js></script>")
print("stamped:", bool(write_spa_build_stamp(root / "repo")))
"""


def test_a_write_outside_the_home_makes_no_home(tmp_path: Path) -> None:
    """Every atomic write asks whether its file is in the home, to write it 0600 there. The
    question was answered by the resolver that CREATES the home, so a write anywhere made one:
    building the web app did, with its build stamp."""
    root = (tmp_path / "scratch").resolve()
    proc, left = _as_a_user(root, ["-c", _WRITE_OUTSIDE, str(root)])
    assert "in the home: False" in proc.stdout and "stamped: True" in proc.stdout, proc.stdout
    assert (root / "elsewhere" / "note.txt").read_text() == "written", "vacuity floor: it wrote"
    assert left == [], f"writing outside the home left this in an empty HOME: {left}"


#: Where a conversation's memory partition is, asked the way every recall asks it.
_WHERE_MEMORY_IS = r"""
from personalclaw import memory_locality
from personalclaw.config.loader import memory_dir_for_cwd

print("a project's partition is its own:", memory_locality.is_local_partition("/srv/example"))
print("the shared one:", memory_dir_for_cwd(None).name)
"""


def test_asking_where_memory_is_makes_no_home(tmp_path: Path) -> None:
    """Every recall asks which memory partition a conversation's folder resolves to. The answer is
    a path, and asking made the home: a recall still running after the turn that timed it out
    (its worker cannot be stopped) made one wherever the home had since moved."""
    proc, left = _as_a_user((tmp_path / "scratch").resolve(), ["-c", _WHERE_MEMORY_IS])
    assert "its own: True" in proc.stdout and "shared one: _default" in proc.stdout, proc.stdout
    assert left == [], f"asking where memory is left this in an empty HOME: {left}"


#: One READ per path helper, each as the code that uses the helper makes it. The keychain is off,
#: as in the suite: one keychain serves every home on the machine.
_READS = {
    "the settings (config_path)": (
        "from personalclaw.config.loader import AppConfig\nAppConfig.load()"
    ),
    "a credential (env_path)": (
        "from personalclaw.config.credentials import get_credential\n"
        "assert get_credential('EXAMPLE_TOKEN') == ''"
    ),
    "the workspace's paths (workspace_dir, default_workspace_root)": (
        "from personalclaw.config import loader\n"
        "from personalclaw.memory import memory_dir, workspace_dir\n"
        "loader.default_workspace_root(), loader._workspace_dir_file(), workspace_dir()\n"
        "memory_dir()"
    ),
    "a memory store's paths (MemoryStore)": (
        "from personalclaw.memory import MemoryStore\nMemoryStore()"
    ),
    "the installed apps (apps_dir)": (
        "from personalclaw.apps.manager import apps_dir, list_apps\n"
        "apps_dir()\n"
        "assert list_apps() == []"
    ),
    "the apps' packages (app_python.root)": (
        "from personalclaw.apps import app_python\n"
        "app_python.root()\n"
        "assert app_python.broken_apps() == [] and app_python.collect() == []"
    ),
    "a push delivery (its devices and signing key)": (
        "from personalclaw import push\n"
        "assert push.deliver('cron', 'run-9') == 0 and push.vapid_keys() is None"
    ),
}


@pytest.mark.parametrize("what", list(_READS))
def test_a_read_makes_no_home(tmp_path: Path, what: str) -> None:
    """Reading something from the home asks where it is, and the answer is a path: asking made
    the home. A credential read by a background thread after its test had undone the home's
    isolation (a push delivery reading its signing key) made the real one. Each helper now says
    where, and only the code that writes makes what it writes into."""
    code = (
        "from personalclaw.config.credentials import keychain_off\n"
        f"keychain_off()\n{_READS[what]}\nprint('read')"
    )
    proc, left = _as_a_user((tmp_path / "scratch").resolve(), ["-c", code])
    assert "read" in proc.stdout, "vacuity floor: the read ran"
    assert left == [], f"reading {what} left this in an empty HOME: {left}"


#: One WRITE per writer those helpers used to make the home for, and what it writes there. A file
#: written through the shared writer is 0600 (``atomic_write``); a lock file is not one.
_WRITES = {
    "a credential": (
        "from personalclaw.config.credentials import save_credential\n"
        "save_credential('EXAMPLE_TOKEN', 'example')",
        ".env",
    ),
    "the settings": (
        "from personalclaw.config.loader import AppConfig\nAppConfig().save()",
        "config.json",
    ),
    "the workspace": (
        "from personalclaw.config.loader import workspace_root\nworkspace_root()",
        "workspace",
    ),
    "a memory store": (
        "from personalclaw.memory import MemoryStore\nMemoryStore().init()",
        "workspace/memory/preferences.md",
    ),
    "a day's history": (
        "from personalclaw.memory import MemoryStore\nMemoryStore().append_history('example')",
        "workspace/memory/history",
    ),
    "an app's data": (
        "from personalclaw.apps.manager import app_data_dir\napp_data_dir('example')",
        "apps/example/data",
    ),
    "an app being staged": (
        "from personalclaw.apps.app_manager import _quarantine_dir\n_quarantine_dir()",
        "apps/.quarantine",
    ),
    "the apps' packages": (
        "from personalclaw.apps import app_python\nwith app_python._locked():\n    pass",
        "app-python",
    ),
    "the apps' dependency ledger": (
        "from personalclaw.apps import dependency_ledger\n"
        "with dependency_ledger._locked():\n    pass",
        "apps",
    ),
}


@pytest.mark.parametrize("what", list(_WRITES))
def test_the_first_write_makes_the_home_private(tmp_path: Path, what: str) -> None:
    """The home is worked out without making it, so the first WRITE makes it, before the folder it
    writes into: 0700, like ``~/.ssh``. A folder made in a home that is not there yet makes the
    home as its parent, at the umask's mode, 0755 under the common 022, and the home is private
    because the credential store and every setting are in it."""
    code, written = _WRITES[what]
    probe = (
        "import os, stat\n"
        "os.umask(0o022)\n"
        "from personalclaw.config.credentials import keychain_off\n"
        f"keychain_off()\n{code}\n"
        "from personalclaw.config.loader import resolve_config_dir\n"
        "home = resolve_config_dir()\n"
        f"target = home / {written!r}\n"
        "print('home', oct(stat.S_IMODE(home.stat().st_mode)))\n"
        "print('file' if target.is_file() else 'folder' if target.is_dir() else 'missing',\n"
        "      oct(stat.S_IMODE(target.stat().st_mode)) if target.exists() else '')\n"
    )
    proc, left = _as_a_user((tmp_path / "scratch").resolve(), ["-c", probe])
    home_line, target_line = proc.stdout.strip().splitlines()[-2:]
    assert not target_line.startswith("missing"), f"vacuity floor: {what} wrote {written}"
    assert home_line == "home 0o700", f"writing {what} made the home at {home_line}: {left}"
    if target_line.startswith("file"):
        assert target_line == "file 0o600", f"{written} in the home: {target_line}"


def test_the_offline_reference_renders_without_touching_a_home(tmp_path: Path) -> None:
    """``python -m personalclaw.manifest_reference`` generates checked-in markdown. It has no
    business with any home — not the owner's, and not a scratch one either."""
    result = _run_child(tmp_path, "reference")
    assert result["done"] == 4, f"the reference did not render its four files: {result}"
    assert not result[
        "touches"
    ], "rendering the offline reference touched the scratch home:\n" + _render(result["touches"])
    assert result["left"] == [
        "home",
        "pclaw-home",
    ], f"rendering the offline reference left files in the empty homes: {result['left'][:20]}"
