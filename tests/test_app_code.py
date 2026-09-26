"""`personalclaw.app_code`: which registrations are an app's, and what unloading it takes back.

The route-level contract lives in ``test_app_update_runs_the_new_code.py``. These pin the rules
underneath it that no fixture app exercises by accident: whose registration is whose when app
code pulls in a core module, a registration made later from the app's own call, and the four
things :func:`release` has to report or clean because Python cannot unload them for it.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import threading
import types
from pathlib import Path
from typing import Iterator

import pytest

from personalclaw import app_code, media_catalogs
from personalclaw.apps.native_contract import app_dir_on_path, load_bundle_module
from personalclaw.media_catalogs import MediaCatalog

APP = "code-probe"


@pytest.fixture
def root(tmp_path, monkeypatch) -> Iterator[Path]:
    """An app directory, a clean ledger, and a media-catalog table the test may write into."""
    monkeypatch.setattr(app_code, "_roots", {})
    monkeypatch.setattr(app_code, "_undo", {})
    monkeypatch.setattr(
        media_catalogs, "_catalogs", {k: dict(v) for k, v in media_catalogs._catalogs.items()}
    )
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


def _load(root: Path, module: str = "provider") -> types.ModuleType:
    return load_bundle_module(root, APP, module)


def _catalog(provider_type: str) -> MediaCatalog | None:
    return media_catalogs.get_media_catalog("tts", provider_type)


def test_a_registration_the_apps_module_makes_on_import_is_taken_back(root):
    _write(
        root / "provider.py",
        """
        from personalclaw.sdk.model import MediaCatalog, register_media_catalog
        register_media_catalog("tts", "code-probe", MediaCatalog(default_model="m"))
        """,
    )
    _load(root)
    assert _catalog("code-probe") is not None

    app_code.release(APP)
    assert _catalog("code-probe") is None


def test_a_core_module_the_app_imports_registers_as_core(root, tmp_path, monkeypatch):
    """The app's import pulls in a module that is not the app's, whose body registers its own
    entry: that entry is core's, and unloading the app must leave it."""
    elsewhere = tmp_path / "site"
    _write(
        elsewhere / "corelike_probe.py",
        """
        from personalclaw.media_catalogs import MediaCatalog, register_media_catalog
        register_media_catalog("tts", "corelike", MediaCatalog(default_model="m"))
        """,
    )
    monkeypatch.syspath_prepend(str(elsewhere))
    _write(root / "provider.py", "import corelike_probe  # noqa: F401\n")
    _load(root)
    assert _catalog("corelike") is not None

    app_code.release(APP)
    assert _catalog("corelike") is not None, "unloading the app took back a core registration"


def test_a_registration_made_later_from_the_apps_own_call_is_taken_back(root):
    """A sidecar runner, an ACP entry: registered from app code at call time, not at import."""
    _write(
        root / "provider.py",
        """
        from personalclaw.sdk.model import MediaCatalog, register_media_catalog

        def on_first_use():
            register_media_catalog("tts", "code-probe-late", MediaCatalog(default_model="m"))
        """,
    )
    _load(root).on_first_use()
    assert _catalog("code-probe-late") is not None

    app_code.release(APP)
    assert _catalog("code-probe-late") is None


def test_a_take_back_leaves_a_newer_registration_under_the_same_key(root):
    _write(
        root / "provider.py",
        """
        from personalclaw.sdk.model import MediaCatalog, register_media_catalog
        register_media_catalog("tts", "code-probe", MediaCatalog(default_model="old"))
        """,
    )
    _load(root)
    newer = MediaCatalog(default_model="newer")
    media_catalogs.register_media_catalog("tts", "code-probe", newer)  # core, after the app

    app_code.release(APP)
    assert _catalog("code-probe") is newer


def test_release_unloads_every_module_loaded_from_the_apps_directory(root):
    _write(root / "runtime" / "__init__.py", "")
    _write(root / "runtime" / "settings.py", "VALUE = 1\n")
    _write(root / "loose" / "part.py", "PART = 1\n")  # a namespace package: no __init__
    _write(
        root / "provider.py",
        "from runtime.settings import VALUE  # noqa: F401\nimport loose.part  # noqa: F401\n",
    )
    _load(root)
    loaded = {
        "_pclaw_app_code_probe__provider",
        "runtime",
        "runtime.settings",
        "loose",
        "loose.part",
    }
    assert loaded <= set(sys.modules)

    released = app_code.release(APP)
    assert loaded <= set(released.modules)
    assert not loaded & set(sys.modules)
    assert released.left_running == []


def test_release_drops_the_old_versions_cached_bytecode(root):
    """The next version's file sits at the same path; its size and mtime second may match."""
    _write(root / "runtime" / "__init__.py", "")
    _write(root / "runtime" / "version.py", "VERSION = 'v1'\n")
    _write(root / "provider.py", "from runtime.version import VERSION  # noqa: F401\n")
    _load(root)
    cached = Path(sys.modules["runtime.version"].__cached__)
    assert cached.is_file(), "vacuity floor: the import wrote bytecode to drop"

    app_code.release(APP)
    assert not cached.exists()


def test_compiled_code_the_app_loaded_needs_a_restart(root):
    """Python cannot unload an extension module; the release says so, and names it."""
    fake = types.ModuleType("code_probe_fast")
    fake.__file__ = str(root / "fast.cpython-313-darwin.so")
    sys.modules["code_probe_fast"] = fake
    with app_dir_on_path(APP, root):
        pass  # the loader's claim, as for any code it runs from this directory

    left = app_code.release(APP).left_running
    assert left == [
        "it loaded compiled Python code (fast.cpython-313-darwin.so), which Python cannot unload"
    ]


def test_a_thread_still_running_the_apps_code_needs_a_restart(root):
    _write(
        root / "provider.py",
        """
        import threading

        def start_waiting(event):
            thread = threading.Thread(target=_wait, args=(event,), name="code-probe-poller")
            thread.daemon = True
            thread.start()

        def _wait(event):
            event.wait()
        """,
    )
    release = threading.Event()
    try:
        _load(root).start_waiting(release)
        left = app_code.release(APP).left_running
    finally:
        release.set()
    assert left == ["a thread its previous version started is still running (code-probe-poller)"]


def test_a_task_still_suspended_in_the_apps_code_needs_a_restart(root):
    _write(
        root / "provider.py",
        """
        import asyncio

        async def poll_forever(event):
            await event.wait()
        """,
    )
    loop = asyncio.new_event_loop()
    try:
        event = asyncio.Event()
        task = loop.create_task(_load(root).poll_forever(event))
        loop.run_until_complete(asyncio.sleep(0))  # the task starts and suspends in app code
        left = app_code.release(APP).left_running
        task.cancel()
        loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
    finally:
        loop.close()
    assert left == ["a task its previous version started is still running (poll_forever)"]


def test_a_task_that_finished_is_not_counted(root):
    _write(root / "provider.py", "async def once():\n    return 1\n")
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(_load(root).once()) == 1
    finally:
        loop.close()
    assert app_code.release(APP).left_running == []
