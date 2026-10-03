"""The desktop app refuses every child that needs a Python interpreter, before it starts, saying so.

The desktop app runs PersonalClaw from a frozen bundle whose executable is the CLI and runs three
declared child modules (``_frozen_child``) and no other Python. Every other child it was asked to
start reached that CLI's parser and exited with a usage error, or failed with no word: an app's
own Python server and background worker, pip installing an app's packages, an app's engine, a
connector pack's parse script, an app's own tests, the Linux sandbox's launcher. The doctor ran
the bundle with ``-c`` and reported its packages missing and pip gone.

So one predicate (``python_children.available``) is asked before any of them starts, and each is
refused with one sentence: what the desktop app cannot run, and that the version installed with uv
runs it. An app that needs any of them is shown as not available, and its install, enable and
start are refused with that sentence; an app that needs none of them installs and runs. Each test
simulates the bundle the way ``test_python_children_in_the_desktop_app`` does, and each refused
child is checked not to have started.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import python_children, sandbox
from personalclaw.apps import app_manager, backend_runtime, catalog, manager
from personalclaw.apps.manifest import AppManifest

_REPO = Path(__file__).resolve().parents[1]

#: A requirement nothing on this machine provides, so an install would have to fetch it.
_ABSENT = "pc-fixture-package-nobody-ships>=1.0"


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """This process reads as the desktop app's frozen bundle, with an executable to name."""
    bundle = tmp_path / "personalclaw-backend"
    bundle.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    bundle.chmod(0o755)
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr("sys.executable", str(bundle))
    return str(bundle)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home that app children may be started in, with startup's seeding and
    package repair out of the way."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_BACKENDS", raising=False)
    monkeypatch.delenv("PERSONALCLAW_SKIP_APP_WORKERS", raising=False)
    backend_runtime._supervisor = backend_runtime.BackendSupervisor()
    yield tmp_path
    backend_runtime.get_backend_supervisor().stop_all()


@pytest.fixture
def no_child(monkeypatch):
    """Every way a child is started fails the test: a refused child must never be started."""
    started: list[Any] = []

    def _refuse(*args, **kwargs):
        started.append(args[0] if args else kwargs.get("args"))
        raise AssertionError(f"a child was started: {started[-1]!r}")

    async def _refuse_async(*args, **kwargs):
        _refuse(list(args))

    monkeypatch.setattr(subprocess, "Popen", _refuse)
    monkeypatch.setattr(subprocess, "run", _refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _refuse_async)
    return started


def _assert_the_sentence(sentence: str, cannot: str) -> None:
    """*sentence* is the one refusal: what the desktop app cannot do, and what to do instead."""
    assert sentence == python_children.refusal(cannot), sentence
    assert sentence.startswith("The desktop app can't "), sentence
    assert f"`{python_children.INSTALL_COMMAND}`" in sentence


def _manifest(**fields: Any) -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": "py-app",
            "version": "1.0.0",
            "displayName": "Py App",
            "description": "x",
            **fields,
        }
    )


#: What each kind of Python child an app declares is refused as, and the manifest that declares it.
_PYTHON_CHILDREN = {
    "its own server": (
        {"backend": {"entryPoint": "backend/server.py", "type": "python"}},
        "start this app's own Python server",
    ),
    "a server named by its file": (
        {"backend": {"entryPoint": "backend/server.py"}},
        "start this app's own Python server",
    ),
    "an asgi server": (
        {"backend": {"entryPoint": "backend/app.py", "type": "asgi"}},
        "start this app's own Python server",
    ),
    "a background worker": (
        {"permissions": {"backgroundTasks": True}},
        "start this app's Python background worker",
    ),
    "an engine": (
        {
            "provider": {
                "type": "model",
                "implementation": "provider:create_provider",
                "execution": "sidecar",
                "capabilities": ["tts"],
            },
            "dependencies": {"sidecarDependencies": ["pc-fixture-engine>=1"]},
        },
        "start this app's engine in a Python environment of its own",
    ),
    "parse scripts": (
        {
            "sources": [
                {
                    "name": "feed",
                    "script": "parse.py",
                    "fetchSpec": {"url": "https://example.com/feed", "method": "GET"},
                }
            ]
        },
        "run this app's Python parse scripts",
    ),
    "packages it does not carry": (
        {"dependencies": {"pythonDependencies": [_ABSENT]}},
        f"install the Python packages this app needs ({_ABSENT})",
    ),
}


# ── the one predicate and its sentence ───────────────────────────────────────────────────────


class TestTheOnePredicate:
    def test_an_interpreter_starts_python_children(self):
        assert python_children.available() is True
        python_children.require("start anything")  # no refusal

    def test_the_desktop_apps_bundle_does_not(self, frozen):
        assert python_children.available() is False
        with pytest.raises(python_children.NeedsInterpreter) as refused:
            python_children.require("start this app's own Python server")
        _assert_the_sentence(str(refused.value), "start this app's own Python server")

    def test_the_sentence_says_what_the_app_cannot_run_and_what_to_do_instead(self):
        assert python_children.refusal("run an app's own tests") == (
            "The desktop app can't run an app's own tests; the version you install with "
            "`uv tool install --python 3.13 personalclaw` can."
        )

    def test_the_install_it_names_is_the_route_the_guides_give(self):
        """Never an invented command: the getting-started guide's recommended install, which the
        desktop guide points at for everything the desktop app does not run."""
        for guide in ("getting-started.md", "desktop.md"):
            text = (_REPO / "docs" / "guides" / guide).read_text(encoding="utf-8")
            assert python_children.INSTALL_COMMAND in text, guide


# ── what of an app needs an interpreter ──────────────────────────────────────────────────────


class TestWhatOfAnAppNeedsAnInterpreter:
    @pytest.mark.parametrize("kind", sorted(_PYTHON_CHILDREN))
    def test_each_python_child_an_app_declares_is_named(self, frozen, kind):
        fields, cannot = _PYTHON_CHILDREN[kind]
        _assert_the_sentence(python_children.app_refusal(_manifest(**fields)), cannot)

    @pytest.mark.parametrize(
        "fields",
        [
            {},
            {"backend": {"entryPoint": "dist/server.js", "type": "node"}},
            {"backend": {"entryPoint": "dist/server.mjs"}},
            {"provider": {"type": "tool", "implementation": "provider:create_tools"}},
            # Packages this install already carries need no install, so nothing needs pip.
            {"dependencies": {"pythonDependencies": ["packaging>=20"]}},
        ],
        ids=["nothing", "node server", "node server by file", "in-process provider", "carried"],
    )
    def test_an_app_that_runs_none_of_them_is_not_refused(self, frozen, fields):
        assert python_children.app_refusal(_manifest(**fields)) == ""

    @pytest.mark.parametrize("kind", sorted(_PYTHON_CHILDREN))
    def test_an_install_with_an_interpreter_runs_every_one_of_them(self, kind):
        assert python_children.app_refusal(_manifest(**_PYTHON_CHILDREN[kind][0])) == ""

    def test_everything_an_app_needs_is_said_in_the_one_sentence(self, frozen):
        both = _manifest(
            backend={"entryPoint": "backend/server.py"},
            dependencies={"pythonDependencies": [_ABSENT]},
        )
        assert python_children.app_refusal(both) == python_children.refusal(
            f"start this app's own Python server or install the Python packages it needs "
            f"({_ABSENT})"
        )


# ── the app's install, enable and start ──────────────────────────────────────────────────────


def _source(root: Path, name: str = "py-server", **fields: Any) -> Path:
    """An app bundle that runs its own Python server, unless *fields* say otherwise."""
    src = root / "src" / name
    (src / "backend").mkdir(parents=True, exist_ok=True)
    (src / "backend" / "server.py").write_text("print('serving')\n", encoding="utf-8")
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": "Py Server",
        "description": "A fixture app with a Python server of its own.",
        "backend": {"entryPoint": "backend/server.py", "type": "python"},
        **fields,
    }
    (src / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    return src


_SERVER = "start this app's own Python server"


class TestTheAppIsRefusedAtEveryDoor:
    def test_its_install_is_refused_before_anything_is_staged(self, home, frozen, no_child):
        result = app_manager.install(_source(home), confirm=True)
        assert result.ok is False
        _assert_the_sentence(result.error, _SERVER)
        assert manager._read_installed("py-server") is None
        assert not app_manager.app_dir("py-server").exists()
        assert no_child == []

    def test_the_review_a_consent_dialog_shows_says_the_same(self, home, frozen):
        review = app_manager.preview(_source(home))
        assert review.ok is False and not review.needs_consent
        _assert_the_sentence(review.error, _SERVER)

    def test_an_app_that_needs_none_installs_in_the_desktop_app(self, home, frozen):
        node = _source(home, name="node-server", backend={"entryPoint": "server.js"})
        result = app_manager.install(node, confirm=True)
        assert result.ok is True, result.error

    def test_an_update_that_starts_needing_one_is_refused_and_changes_nothing(self, home, frozen):
        assert app_manager.install(
            _source(home, name="grows", backend={"entryPoint": "server.js"}), confirm=True
        ).ok
        newer = _source(home / "v2", name="grows", version="2.0.0")
        result = app_manager.update(newer, "grows", confirm=True)
        assert result.ok is False
        _assert_the_sentence(result.error, _SERVER)
        assert manager._read_installed("grows").version == "1.0.0"

    def test_its_enable_is_refused_saying_why(self, home, monkeypatch):
        """Installed where it could run (the home a uv install and the desktop app share), then
        found by the desktop app."""
        monkeypatch.setenv("PERSONALCLAW_SKIP_APP_BACKENDS", "1")
        assert app_manager.install(_source(home), confirm=True).ok
        assert app_manager.disable("py-server")
        monkeypatch.setattr("sys.frozen", True, raising=False)
        assert app_manager.enable("py-server") is False
        assert manager._read_installed("py-server").enabled is False
        _assert_the_sentence(app_manager.enable_refusal("py-server"), _SERVER)

    def test_startup_holds_an_installed_one_and_says_why(self, home, monkeypatch, no_child):
        """It never starts, so nothing crash-loops: the watchdog's sweep starts nothing either.
        Its provider is listed off with the sentence, which Settings → Providers shows."""
        from personalclaw.apps import app_runtime
        from personalclaw.apps.worker_runtime import get_worker_supervisor
        from personalclaw.providers import registry as registry_module

        tool = {"type": "tool", "implementation": "provider:create_tools"}
        src = _source(home, provider=tool)
        live = app_manager.app_dir("py-server")
        live.parent.mkdir(parents=True, exist_ok=True)
        (live / "backend").mkdir(parents=True)
        (live / "backend" / "server.py").write_text((src / "backend" / "server.py").read_text())
        (live / "app.json").write_text((src / "app.json").read_text())
        manager._write_installed(
            "py-server",
            manager.InstalledApp(
                name="py-server", version="1.0.0", displayName="Py Server", enabled=True
            ),
        )
        monkeypatch.setattr("sys.frozen", True, raising=False)
        registry_module.reset_provider_registry()
        try:
            assert app_runtime.start_installed() == []
            backend_runtime._check_and_revive()  # one watchdog pass
            assert backend_runtime.get_backend_supervisor().get("py-server") is None
            assert no_child == []
            listed = registry_module.get_provider_registry().get("py-server")
            assert listed is not None and not listed.enabled
            _assert_the_sentence(listed.error, _SERVER)
        finally:
            backend_runtime.get_backend_supervisor().unhold("py-server")
            get_worker_supervisor().unhold("py-server")
            registry_module.reset_provider_registry()


class TestTheProcessesAreNeverStarted:
    def test_the_backend_supervisor_starts_no_python_server(self, home, frozen, no_child):
        live = app_manager.app_dir("py-server")
        (live / "backend").mkdir(parents=True)
        (live / "backend" / "server.py").write_text("print('serving')\n")
        manifest = _manifest(name="py-server", backend={"entryPoint": "backend/server.py"})
        assert backend_runtime.get_backend_supervisor().start(manifest) is None
        assert no_child == []

    def test_a_python_worker_is_given_up_on_before_it_starts_with_the_sentence(
        self, home, frozen, no_child
    ):
        from personalclaw.apps import worker_runtime

        live = app_manager.app_dir("py-worker")
        live.mkdir(parents=True)
        (live / "worker.py").write_text("import time\ntime.sleep(60)\n")
        manifest = _manifest(name="py-worker", permissions={"backgroundTasks": True})
        (live / "app.json").write_text(json.dumps(manifest.to_dict()))
        supervisor = worker_runtime.WorkerSupervisor()
        supervisor.reap_orphans = lambda app, entry: 0  # its `ps` read is not the worker
        assert supervisor.start(manifest) == []
        (row,) = supervisor.report("py-worker")
        assert row["state"] == "failed" and row["running"] is False
        _assert_the_sentence(row["reason"], "start this app's Python background worker")
        supervisor.sweep()  # the give-up is sticky: nothing is started on a later pass
        assert no_child == []


# ── what the Store and the Library say ───────────────────────────────────────────────────────


@asynccontextmanager
async def _client(home: Path):
    from personalclaw import inbox as _inbox
    from personalclaw.dashboard.handlers.apps import register_app_routes
    from personalclaw.providers import entity_routes as _er

    with (
        patch.object(catalog, "config_dir", return_value=home),
        patch.object(_er, "config_dir", return_value=home),
        patch.object(_inbox, "config_dir", return_value=home),
    ):
        app = web.Application()
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


class TestTheStoreAndTheLibrarySayIt:
    def test_the_store_card_of_an_app_it_cannot_run_is_refused_with_the_sentence(
        self, home, frozen
    ):
        _source(home)
        _source(home, name="node-server", backend={"entryPoint": "server.js"})
        with patch.object(catalog, "list_local_sources", return_value=[str(home / "src")]):
            cards = {e.name: e for e in catalog._scan_local_sources()}
        _assert_the_sentence(cards["py-server"].refused, _SERVER)
        assert cards["node-server"].refused == ""

    def test_every_card_built_from_a_manifest_says_it(self):
        """A count, as the disclosure rail counts its projection: the git, local and native scans
        each build a card from a manifest they read, and one that left the refusal out would
        offer Install for an app whose install can only be refused."""
        src = (_REPO / "src" / "personalclaw" / "apps" / "catalog.py").read_text(encoding="utf-8")
        built = len(re.findall(r"^\s+consentKnown=True,$", src, re.M))
        said = len(re.findall(r"^\s+refused=python_children\.app_refusal\(m\),$", src, re.M))
        assert built == 3, f"expected 3 cards built from a manifest, found {built}"
        assert said == built, f"{built} cards are built from a manifest, {said} say the refusal"

    @pytest.mark.asyncio
    async def test_the_library_and_the_enable_route_say_the_same_sentence(self, home, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_SKIP_APP_BACKENDS", "1")
        assert app_manager.install(_source(home), confirm=True).ok
        assert app_manager.disable("py-server")
        monkeypatch.setattr("sys.frozen", True, raising=False)
        async with _client(home) as client:
            apps = (await (await client.get("/api/apps")).json())["apps"]
            (row,) = [a for a in apps if a["name"] == "py-server"]
            _assert_the_sentence(row["refused"], _SERVER)
            r = await client.post("/api/apps/py-server/enable")
            assert r.status == 400
            assert (await r.json())["error"] == row["refused"]

    @pytest.mark.asyncio
    async def test_an_app_the_desktop_app_runs_says_nothing(self, home, frozen):
        assert app_manager.install(
            _source(home, name="node-server", backend={"entryPoint": "server.js"}), confirm=True
        ).ok
        async with _client(home) as client:
            apps = (await (await client.get("/api/apps")).json())["apps"]
            (row,) = [a for a in apps if a["name"] == "node-server"]
            assert row["refused"] == ""


# ── packages ─────────────────────────────────────────────────────────────────────────────────


class TestPackagesAreNotInstalled:
    def test_pip_is_refused_with_the_sentence_not_a_missing_pip(self, frozen):
        from personalclaw._installer import NoInstallerError, prefix_install_argv

        with pytest.raises(NoInstallerError) as refused:
            prefix_install_argv([_ABSENT])
        _assert_the_sentence(str(refused.value), "install Python packages")

    def test_an_install_of_packages_says_so_without_running_pip(self, home, frozen, no_child):
        from personalclaw.apps import app_python

        with pytest.raises(app_python.PackageInstallError) as refused:
            app_python.ensure("py-app", [_ABSENT], label="Py App")
        assert str(refused.value) == (
            "Couldn't install Py App's Python packages: "
            + python_children.refusal("install Python packages")
        )
        assert no_child == []

    def test_the_boot_repair_runs_no_pip(self, home, frozen, monkeypatch):
        """An installed app whose packages are missing is what the boot repair reinstalls; in the
        desktop app startup refuses it instead, and the repair runs nothing."""
        from personalclaw.apps import app_python

        live = app_manager.app_dir("py-app")
        live.mkdir(parents=True)
        manifest = _manifest(dependencies={"pythonDependencies": [_ABSENT]})
        (live / "app.json").write_text(json.dumps(manifest.to_dict()))
        manager._write_installed(
            "py-app",
            manager.InstalledApp(
                name="py-app", version="1.0.0", displayName="Py App", enabled=True
            ),
        )
        assert [d.name for d, _missing in app_python.broken_apps()] == ["py-app"]  # the floor
        monkeypatch.setattr(
            app_python, "install_everything", lambda: pytest.fail("the repair ran pip")
        )
        assert app_manager.repair_app_packages() == []


def _shared_folder(home: Path) -> tuple[Path, Path]:
    """The app packages folder as the version installed with uv leaves it in a PersonalClaw
    folder the desktop app also opens: an installed app's package copied there although this
    process carries it too (as the bundle carries a model app's ``openai``), and a layout of
    another Python version. Returns the package's directory and that layout."""
    from personalclaw.apps import app_python

    site = app_python.site_dirs()[0]
    info = site / "packaging-99.0.dist-info"
    files = {
        "packaging/__init__.py": "",
        "packaging-99.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: packaging\n"
        "Version: 99.0\n",
    }
    for rel, text in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text(text, encoding="utf-8")
    (info / "RECORD").write_text("".join(f"{rel},,\n" for rel in [*files, f"{info.name}/RECORD"]))
    other = app_python.root() / "lib" / "python3.9" / "site-packages"
    other.mkdir(parents=True)
    live = app_manager.app_dir("needs-packaging")
    live.mkdir(parents=True)
    manifest = _manifest(name="needs-packaging", dependencies={"pythonDependencies": ["packaging"]})
    (live / "app.json").write_text(json.dumps(manifest.to_dict()))
    (live / "installed.json").write_text(
        json.dumps({"name": "needs-packaging", "version": "1.0.0"})
    )
    return site / "packaging", other


class TestTheSharedPackagesFolderIsLeftAlone:
    """The desktop app installs no package in the folder it shares with the installed version, so
    what is there is that version's, judged against that version's own packages. Judged against
    the bundle's instead, a copy of one the bundle carries read as one no app needs, and the
    desktop app's collection deleted it, with the other Python version's layout: the installed
    version, opened on the folder again, had to fetch them again before its apps loaded."""

    def test_the_desktop_app_deletes_nothing_from_it(self, home, frozen):
        package, other = _shared_folder(home)
        from personalclaw.apps import app_python

        assert app_python.collect() == []
        assert package.is_dir() and other.is_dir()

    def test_with_an_interpreter_the_install_collects_its_own_folder(self, home):
        """The control: the same folder, collected by an install whose own packages these are,
        loses the copy its own environment shadows and the stale layout."""
        package, other = _shared_folder(home)
        from personalclaw.apps import app_python

        assert app_python.collect() == ["packaging 99.0"]
        assert not package.exists() and not other.exists()


# ── the app tests runner, the pack-parse harness, engines ────────────────────────────────────


def test_an_apps_own_tests_are_refused_with_the_sentence(tmp_path, frozen, no_child):
    from personalclaw.apps.quality import run_bundle_tests

    passed, tail = run_bundle_tests(tmp_path)
    assert passed is False
    _assert_the_sentence(tail, "run an app's own tests")
    assert no_child == []


def test_a_parse_script_is_refused_before_anything_is_written(tmp_path, frozen, monkeypatch):
    import tempfile

    from personalclaw.knowledge_providers.pack_parse import ParseFailure, run_parse_script

    monkeypatch.setattr(tempfile, "mkstemp", lambda **kw: pytest.fail("a file was written"))
    script = tmp_path / "parse.py"
    script.write_text("print('{}')\n")
    with pytest.raises(ParseFailure) as refused:
        run_parse_script(script, b"{}")
    assert refused.value.code == ParseFailure.UNRUNNABLE
    _assert_the_sentence(refused.value.detail, "run this source's Python parse script")


_ENGINE = "install or start this app's engine in a Python environment of its own"


class TestEnginesAreNotStarted:
    def test_a_supervised_engine_is_refused_before_it_starts(self, tmp_path, frozen, no_child):
        from personalclaw.local_models.sidecar import SidecarCrashed, SidecarRunner

        runner = SidecarRunner(
            app="py-engine", worker=tmp_path / "worker.py", venv=tmp_path, restart_max=2
        )
        # Every call says it, never the restart budget: a refusal is not a crash, and no
        # restart would change it.
        for _ in range(runner.restart_max + 2):
            with pytest.raises(SidecarCrashed) as refused:
                runner.call("health")
            assert refused.value.reason == "spawn_failed"
            _assert_the_sentence(refused.value.detail, _ENGINE)
        assert runner.health()["consecutive_failures"] == 0
        # Nothing started, so the watchdog has nothing to bring back every half minute.
        assert runner.watchdog_sweep()["action"] == "noop"
        assert no_child == []

    @pytest.mark.asyncio
    async def test_a_one_call_engine_is_refused_before_it_starts(
        self, tmp_path, frozen, no_child, monkeypatch
    ):
        from personalclaw.local_models import sidecar

        monkeypatch.setattr(sidecar, "sidecar_venv_dir", lambda app: tmp_path / "venv")
        with pytest.raises(sidecar.SidecarCrashed) as refused:
            await sidecar.run_once("py-engine", tmp_path / "worker.py", "diarize", {})
        _assert_the_sentence(refused.value.detail, _ENGINE)
        assert no_child == []

    def test_install_engine_is_refused_before_a_job_exists(self, tmp_path, frozen, monkeypatch):
        from personalclaw.dashboard.model_downloads import ModelDownloadRegistry
        from personalclaw.local_models.sidecar import SidecarInstall

        monkeypatch.setattr(
            SidecarInstall, "for_app", classmethod(lambda cls, app: cls(app, venv=tmp_path))
        )
        registry = ModelDownloadRegistry()
        job, error = registry.start_install("py-engine")
        assert job is None
        _assert_the_sentence(error, _ENGINE)
        assert registry.install_job("py-engine") is None

    def test_an_engine_install_step_says_the_whole_sentence(self, tmp_path, frozen, no_child):
        from personalclaw.local_models.sidecar import SidecarInstall

        install = SidecarInstall("py-engine", requirements=["pc-fixture-engine>=1"], venv=tmp_path)
        assert install.run_one("venv") is False
        _assert_the_sentence(install.error, _ENGINE)
        assert install.status()["steps"][0]["detail"] == install.error
        assert no_child == []


# ── the Linux namespace sandbox fails closed ─────────────────────────────────────────────────

_SANDBOX = "start the Linux sandbox this command runs in, so it was not run"


@pytest.fixture
def namespaces(monkeypatch):
    """A Linux host whose user and mount namespaces work: the backend ``wrap_argv`` applies."""
    monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": "namespace")


class TestTheLinuxSandboxFailsClosed:
    """The launcher is a generated script this interpreter runs. In the desktop app it could not
    start, and the command went with it (the parser refused the script's path); the command now
    is refused before anything is written, with the sentence, and never runs outside it."""

    def test_the_wrap_is_refused_and_writes_no_launcher(self, frozen, namespaces, monkeypatch):
        import tempfile

        monkeypatch.setattr(tempfile, "mkstemp", lambda **kw: pytest.fail("a launcher was written"))
        assert sandbox.wrap_refusal("standard") == python_children.refusal(_SANDBOX)
        with pytest.raises(sandbox.SandboxEnforcementUnavailable) as refused:
            sandbox.wrap_argv(["/bin/sh", "-c", "true"], mode="standard")
        _assert_the_sentence(str(refused.value), _SANDBOX)

    def test_with_an_interpreter_the_sandbox_is_applied(self, namespaces):
        wrapped, cleanup = sandbox.wrap_argv(["/bin/sh", "-c", "true"], mode="standard")
        try:
            assert wrapped[0] == sys.executable and wrapped[2:] == ["/bin/sh", "-c", "true"]
            assert sandbox.wrap_refusal("standard") == ""
        finally:
            Path(cleanup).unlink(missing_ok=True)

    @pytest.mark.parametrize("backend", ["sandbox-exec", "none"])
    def test_the_other_backends_need_no_interpreter(self, frozen, monkeypatch, backend):
        monkeypatch.setattr(sandbox, "detect_backend", lambda config_mode="auto": backend)
        assert sandbox.wrap_refusal("standard") == ""

    def test_a_command_with_the_sandbox_off_runs(self, frozen, namespaces):
        assert sandbox.wrap_refusal("off") == ""
        assert sandbox.wrap_argv(["true"], mode="off") == (["true"], None)

    @pytest.mark.asyncio
    async def test_the_agents_bash_command_is_refused_and_never_runs(
        self, tmp_path, frozen, namespaces
    ):
        from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

        marker = tmp_path / "ran"
        tools = NativeBuiltinToolProvider(cwd=tmp_path)
        result = await tools.invoke("bash", {"command": f"touch {marker}"})
        assert result.success is False
        _assert_the_sentence(result.error, _SANDBOX)
        assert not marker.exists()
        # Refused in the pre-flight too, so the owner is never asked to approve it.
        early = await tools.preflight("bash", {"command": f"touch {marker}"})
        assert early is not None and early.error == result.error

    @pytest.mark.asyncio
    async def test_a_hook_or_automation_command_is_refused_and_never_runs(
        self, tmp_path, frozen, namespaces
    ):
        from personalclaw.action_providers.base import ActionContext
        from personalclaw.action_providers.bash_provider import BashActionProvider

        marker = tmp_path / "ran"
        result = await BashActionProvider().execute(
            {"command": f"touch {marker}"}, ActionContext(event="PreToolUse")
        )
        assert result.success is False and result.blocked is False
        _assert_the_sentence(result.error, _SANDBOX)
        assert not marker.exists()

    def test_a_scheduled_script_is_refused_and_never_runs(self, tmp_path, frozen, namespaces):
        from personalclaw import schedule_script

        script = tmp_path / "job.py"
        marker = tmp_path / "ran"
        script.write_text(f"def run(ctx):\n    open({str(marker)!r}, 'w').close()\n")
        with patch.object(schedule_script, "resolve_script_path", return_value=(script, "run")):
            outcome = schedule_script.run_script_sandboxed(f"{script}:run", "job", "")
        assert outcome["status"] == "error"
        _assert_the_sentence(outcome["error"], _SANDBOX)
        assert not marker.exists()

    def test_the_host_tier_refuses_the_launch_as_a_tier_does(self, frozen, namespaces):
        """Agent CLI sessions and the proposer launch through it, and already refuse a tier
        that cannot run rather than starting on the host."""
        from personalclaw.sandbox_providers.base import SandboxSpec, SandboxUnavailableError
        from personalclaw.sandbox_providers.none import NoneSandboxProvider

        with pytest.raises(SandboxUnavailableError) as refused:
            NoneSandboxProvider().wrap(SandboxSpec(mode="standard"), ["agent-cli", "acp"])
        assert refused.value.what == "This command was not run"
        assert python_children.INSTALL_COMMAND in refused.value.fix


# ── the doctor ───────────────────────────────────────────────────────────────────────────────


def test_the_doctors_dependency_rows_say_the_truth_for_the_desktop_app(frozen, no_child, capsys):
    """Checked in this process, which is the bundle: asking it to run ``-c`` reached its CLI's
    parser, and the rows read websockets and aiohttp missing and pip gone, a fault to fix."""
    from personalclaw import cli_doctor

    issues: list[str] = []
    cli_doctor._bundle_rows(issues)
    out = capsys.readouterr().out
    assert "deps:        ✅ websockets, aiohttp available" in out
    assert "pip:         ⏹  not in the desktop app, which installs no Python packages" in out
    assert python_children.INSTALL_COMMAND in out
    assert issues == []
    assert no_child == []


def test_the_doctor_asks_the_one_predicate_before_it_starts_an_interpreter():
    """Its Runtime section takes the bundle's rows first, before either branch runs one."""
    source = (_REPO / "src" / "personalclaw" / "cli_doctor.py").read_text(encoding="utf-8")
    runtime = source[source.index('print("\\nRuntime")') :]
    assert runtime.index("if not python_children.available():") < runtime.index("subprocess.run(")
