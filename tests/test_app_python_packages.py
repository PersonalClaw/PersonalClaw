"""App Python packages install into ``<home>/app-python`` — never into the gateway's environment.

The defect (measured on the published image, uid 10001): the runtime stage copies a ROOT-owned
``/opt/venv``, and the installer pip-installed each app's ``pythonDependencies`` into it — so
Amazon Bedrock, both diarization apps, RapidOCR and Qdrant each failed with a raw pip line,
``[Errno 13] Permission denied: '/opt/venv/…' Check the permissions.``, beside a "Fix with AI"
button for an environment the user cannot change. Apps whose packages the image already carried
skipped pip, which made a systemic failure look selective. And a writable image layer would not
have helped: nothing an app installed was on ``/data``, so the next ``docker rm`` + ``run`` lost it.

These tests hold the replacement's contract through the real lifecycle entry points:

* the install targets ``<home>/app-python`` with every distribution the gateway can import
  pinned, and uses pip even where uv exists (uv's ``--prefix`` ignores what is installed);
* the packages load AFTER the gateway's own, so an app can add a module but never shadow one;
* a failure reads as a sentence, and only a failure whose LOG is the next step offers one;
* uninstall collects what no remaining app needs, and never deletes outside the directory;
* an update whose packages cannot be installed leaves the installed version exactly as it was;
* a Python child of an app, and its setup hooks, see the same packages;
* the boot repair reinstalls what a new interpreter (a new image) finds missing.

The fake pip replaces ``subprocess.run`` on the ``subprocess`` MODULE, so it intercepts the pip
call wherever the installer makes it. The ``TestRealPip`` cases run the real pip, offline, against
wheels built in the test, because a fake cannot witness what pip actually does with a prefix.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.apps.manifest import AppManifest

# ── fixtures ──────────────────────────────────────────────────────────────────────


def _manifest(deps: list[str], name: str = "dep-app", label: str = "Dep App") -> AppManifest:
    return AppManifest.from_dict(
        {
            "name": name,
            "version": "1.0.0",
            "displayName": label,
            "dependencies": {"pythonDependencies": deps},
        }
    )


def _write_app(name: str, deps: list[str], *, version: str = "1.0.0", installed: bool = True):
    tree = manager.apps_dir() / name
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "displayName": name.replace("-", " ").title(),
                "description": "fixture",
                "dependencies": {"pythonDependencies": deps},
            }
        ),
        encoding="utf-8",
    )
    if installed:
        (tree / "installed.json").write_text(
            json.dumps({"name": name, "version": version, "enabled": True}), encoding="utf-8"
        )
    return tree


def _source(tmp_path: Path, name: str, deps: list[str], *, version: str = "1.0.0") -> Path:
    src = tmp_path / f"src-{name}-{version}"
    src.mkdir(parents=True)
    (src / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "displayName": "Dep App",
                "description": "fixture",
                "dependencies": {"pythonDependencies": deps},
            }
        ),
        encoding="utf-8",
    )
    return src


def _ap():
    """``apps/app_python`` — imported per use, so the tests that drive the lifecycle through
    ``app_manager`` alone still reach their assertions on a tree without the module."""
    from personalclaw.apps import app_python

    return app_python


def _root() -> Path:
    return manager.config_dir() / "app-python"


def _site() -> Path:
    """Where ``pip install --prefix <home>/app-python`` puts pure-Python packages — derived
    here independently of ``app_python.site_dirs``; ``TestRealPip`` proves pip agrees."""
    scheme = sysconfig.get_preferred_scheme("prefix")
    if scheme == "osx_framework_library":
        scheme = "posix_prefix"
    base = str(_root())
    keys = ("installed_base", "base", "installed_platbase", "platbase", "prefix", "exec_prefix")
    return Path(sysconfig.get_paths(scheme=scheme, vars={k: base for k in keys})["purelib"])


def _fake_dist(name: str, version: str, *, requires: list[str] = (), module: str | None = None):
    """A distribution as pip leaves it in the prefix: package, dist-info, a RECORD of both."""
    site = _site()
    mod = module or name.replace("-", "_")
    info = f"{name.replace('-', '_')}-{version}.dist-info"
    files = {
        f"{mod}/__init__.py": f"VERSION = {version!r}\n",
        f"{info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {r}\n" for r in requires)
        ),
        f"{info}/INSTALLER": "pip\n",
    }
    for rel, text in files.items():
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text(text, encoding="utf-8")
    record = [f"{rel},," for rel in files] + [f"{info}/RECORD,,"]
    (site / info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    return site / mod


class _FakePip:
    """Stands in for ``subprocess.run`` — records each pip install, optionally "installs"."""

    def __init__(self, real_run, *, returncode: int = 0, output: str = "", installs=None):
        self.real_run = real_run
        self.returncode, self.output = returncode, output
        self.installs = installs or []  # (name, version, requires)
        self.calls: list[list[str]] = []
        self.constraints: list[str] = []

    def __call__(self, argv, *args, **kwargs):
        if not isinstance(argv, list) or "install" not in argv or "pip" not in argv[:4]:
            return self.real_run(argv, *args, **kwargs)  # not a package install
        self.calls.append(list(argv))
        if "--constraint" in argv:
            path = Path(argv[argv.index("--constraint") + 1])
            self.constraints = path.read_text(encoding="utf-8").split()
        if self.returncode == 0:
            for name, version, requires in self.installs:
                _fake_dist(name, version, requires=requires)
        return subprocess.CompletedProcess(argv, self.returncode, stdout="", stderr=self.output)


@pytest.fixture
def fake_pip(monkeypatch):
    real_run = subprocess.run

    def install(**kwargs) -> _FakePip:
        fake = _FakePip(real_run, **kwargs)
        monkeypatch.setattr(subprocess, "run", fake)
        return fake

    return install


@pytest.fixture(autouse=True)
def _forget_activation():
    """`activate()` appends to the process-wide sys.path; each test's home is fresh."""
    before = list(sys.path)
    yield
    sys.path[:] = before
    importlib.invalidate_caches()


# ── where the install goes ─────────────────────────────────────────────────────────


def test_the_install_targets_the_home_and_pins_the_running_environment(fake_pip, monkeypatch):
    """THE defect. The install must land in `<home>/app-python` — writable, on the volume — and
    pip must resolve with every distribution the gateway already imports pinned, so no
    resolution can replace one. uv is on PATH here and still not used: its `--prefix` treats
    nothing as installed (measured), so it would duplicate core's packages instead."""
    from personalclaw import _installer

    monkeypatch.setattr(_installer, "_have_uv", lambda: True)
    fake = fake_pip(installs=[("pclaw-fixture-dep", "1.0", [])])

    app_manager._install_python_deps(_manifest(["pclaw-fixture-dep==1.0"]))

    argv = fake.calls[0]
    assert argv[:4] == [sys.executable, "-m", "pip", "install"], argv
    assert argv[argv.index("--prefix") + 1] == str(manager.config_dir() / "app-python")
    for core in ("packaging", "numpy"):
        pin = f"{core}=={importlib.metadata.version(core)}"
        assert pin in fake.constraints, f"{pin} not pinned: pip could move a core package"
    assert "pclaw-fixture-dep==1.0" in argv


def test_satisfied_requirements_run_no_pip_and_need_no_restart(fake_pip):
    """numpy is a core dependency, so an app declaring it installs nothing — offline included."""
    fake = fake_pip()
    assert app_manager._install_python_deps(_manifest(["numpy>=1.0"])) == []
    assert fake.calls == []
    assert not _ap().root().exists(), "no install needed, so nothing is created"


def test_every_other_installed_apps_requirements_resolve_in_the_same_run(fake_pip):
    """One interpreter holds one version of a module, so pip must see every app's needs at once."""
    _write_app("other-app", ["pclaw-other==2.0"])
    fake = fake_pip(installs=[("pclaw-fixture-dep", "1.0", [])])
    app_manager._install_python_deps(_manifest(["pclaw-fixture-dep==1.0"]))
    assert {"pclaw-fixture-dep==1.0", "pclaw-other==2.0"} <= set(fake.calls[0])


def test_a_first_install_is_importable_in_place_without_a_restart(fake_pip):
    fake_pip(installs=[("pclaw-fixture-dep", "1.0", [])])
    assert app_manager._install_python_deps(_manifest(["pclaw-fixture-dep==1.0"])) == []
    module = importlib.import_module("pclaw_fixture_dep")
    assert Path(module.__file__).is_relative_to(_ap().root())
    sys.modules.pop("pclaw_fixture_dep", None)


# ── import order ──────────────────────────────────────────────────────────────────


def test_app_packages_load_after_the_gateways_own_so_they_cannot_shadow_it():
    """A package here named like one the gateway has must lose the import to the gateway's."""
    _fake_dist("pclaw-fixture-dep", "1.0")
    shadow = _site() / "json"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("SHADOWED = True\n", encoding="utf-8")

    packaging_shadow = _site() / "packaging"
    packaging_shadow.mkdir()
    (packaging_shadow / "__init__.py").write_text("SHADOWED = True\n", encoding="utf-8")

    assert _ap().activate() is True
    site = str(_site())
    assert sys.path.index(site) > max(
        i for i, p in enumerate(sys.path) if p.endswith("site-packages") and p != site
    )
    from importlib.machinery import PathFinder

    # Resolved the way a FIRST import would be — through sys.path, in order — for a stdlib
    # module and for a core dependency; `sys.modules` already holding both would prove nothing.
    for name in ("json", "packaging"):
        spec = PathFinder.find_spec(name, sys.path)
        assert spec is not None and not Path(spec.origin).is_relative_to(_ap().root()), name
    assert importlib.import_module("pclaw_fixture_dep").VERSION == "1.0"
    sys.modules.pop("pclaw_fixture_dep", None)


def test_a_python_child_of_an_app_sees_the_packages_after_its_own(tmp_path):
    """Backends and workers start through the resource-ceiling shim, so PYTHONPATH (which comes
    before the standard library) must not carry app packages. The child bootstrap appends them."""
    _fake_dist("pclaw-fixture-dep", "1.0")
    (_site() / "json").mkdir()
    (_site() / "json" / "__init__.py").write_text("SHADOWED = True\n", encoding="utf-8")
    script = tmp_path / "entry.py"
    script.write_text(
        "import json, sys, pclaw_fixture_dep\n"
        "print(json.dumps([hasattr(json, 'SHADOWED'), pclaw_fixture_dep.__file__,"
        " sys.argv, sys.path[0]]))\n",
        encoding="utf-8",
    )
    # The isolated home, as for any PersonalClaw child a test starts (tests/real_home_guard.py).
    env = {**os.environ, **_ap().child_env(), "PERSONALCLAW_HOME": str(manager.config_dir())}
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [*_ap().child_argv(script), "--flag"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    shadowed, dep_file, argv, path0 = json.loads(proc.stdout)
    assert shadowed is False
    assert Path(dep_file).is_relative_to(_ap().root())
    assert argv == [str(script), "--flag"] and path0 == str(tmp_path)


def test_a_backend_starts_through_the_child_bootstrap_only_when_its_app_declares_packages():
    from personalclaw.apps.backend_runtime import BackendSupervisor

    entry = Path("/apps/x/server.py")
    assert BackendSupervisor._launch_cmd("python", entry) == [sys.executable, str(entry)]
    assert BackendSupervisor._launch_cmd("python", entry, app_packages=True) == [
        sys.executable,
        "-m",
        "personalclaw._app_python_child",
        str(entry),
    ]


def test_a_setup_hook_can_import_the_apps_packages(tmp_path):
    _fake_dist("pclaw-fixture-dep", "1.0")
    out = tmp_path / "hook-saw.txt"
    app_manager._run_hook(
        f'{sys.executable} -c "import pclaw_fixture_dep, pathlib; '
        f"pathlib.Path(r'{out}').write_text(pclaw_fixture_dep.__file__)\"",
        cwd=tmp_path,
        timeout=60,
        env_name="onInstall",
    )
    assert Path(out.read_text()).is_relative_to(_ap().root())


# ── failures read as sentences ──────────────────────────────────────────────────────

_EACCES = (
    "Collecting boto3>=1.34\nInstalling collected packages: jmespath, botocore, s3transfer, boto3\n"
    "ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: "
    "'/data/app-python/lib/python3.13/site-packages/jmespath'\nCheck the permissions.\n"
)


def test_a_permission_error_is_a_sentence_and_offers_no_fix_with_ai(fake_pip):
    """The screenshot defect: the raw pip line as the error, and a "Fix with AI" for it."""
    fake_pip(returncode=1, output=_EACCES)
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(
            _manifest(["pclaw-fixture-boto3>=1.34"], label="Amazon Bedrock")
        )
    message = str(ei.value)
    assert message.startswith("Couldn't install Amazon Bedrock's Python packages")
    assert "can't write to /data/app-python" in message and f"uid {os.getuid()}" in message
    assert "ERROR:" not in message and "Errno" not in message, message
    assert ei.value.log_excerpt == "", "no Fix with AI for a folder the user must fix by hand"


def test_the_install_result_carries_no_fix_prompt_for_it(fake_pip, tmp_path):
    fake_pip(returncode=1, output=_EACCES)
    result = app_manager.install(
        _source(tmp_path, "dep-app", ["pclaw-fixture-boto3>=1.34"]), confirm=True
    )
    assert result.ok is False and result.to_dict()["fix_prompt"] == ""
    assert not (manager.apps_dir() / "dep-app").exists(), "a failed install is rolled back"


def test_offline_reads_as_a_network_problem_not_a_missing_package(fake_pip):
    """pip reports an unreachable index as retry warnings, then ends with the same line a
    package that does not exist ends with — so the tail alone blames the package."""
    fake_pip(
        returncode=1,
        output=(
            "WARNING: Retrying (Retry(total=0, connect=None)) after connection broken by "
            "'NameResolutionError(\"HTTPSConnection(host='pypi.org', port=443): Failed to "
            "resolve 'pypi.org'\")': /simple/qdrant-client/\n"
            "ERROR: Could not find a version that satisfies the requirement "
            "qdrant-client<2,>=1.9 (from versions: none)\n"
            "ERROR: No matching distribution found for qdrant-client<2,>=1.9\n"
        ),
    )
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(
            _manifest(["pclaw-fixture-qdrant>=1.9,<2"], label="Qdrant")
        )
    assert "couldn't reach pypi.org" in str(ei.value)
    assert ei.value.log_excerpt == ""


def test_a_conflict_names_what_the_gateway_runs(fake_pip):
    fake_pip(
        returncode=1,
        output=(
            "ERROR: Cannot install botocore==1.29.0 because these package versions have "
            "conflicting dependencies.\n\nThe conflict is caused by:\n"
            "    botocore 1.29.0 depends on urllib3<1.27 and >=1.25.4\n"
            "    The user requested (constraint) urllib3==2.2.3\n\n"
            "To fix this you could try to:\n\nERROR: ResolutionImpossible: for help visit x\n"
        ),
    )
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(
            _manifest(["pclaw-fixture-botocore==1.29.0"], label="Old Bedrock")
        )
    message = str(ei.value)
    assert "botocore 1.29.0 depends on urllib3<1.27 and >=1.25.4" in message
    assert "PersonalClaw runs urllib3 2.2.3" in message and "nothing was installed" in message
    assert ei.value.log_excerpt == ""


def test_a_build_failure_keeps_the_log_because_it_is_the_next_step(fake_pip):
    fake_pip(
        returncode=1,
        output="      clang: error: no input files\n  ERROR: Failed building wheel for badpkg\n"
        "Failed to build badpkg\nerror: failed-wheel-build-for-install\n",
    )
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(_manifest(["pclaw-fixture-badpkg==0.1"]))
    assert str(ei.value).startswith("badpkg has no ready-made package for this machine")
    assert "clang: error" in ei.value.log_excerpt


def test_a_platform_without_a_release_says_so(fake_pip):
    fake_pip(
        returncode=1,
        output="ERROR: No matching distribution found for sherpa-onnx>=1.10\n",
    )
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(
            _manifest(["pclaw-fixture-sherpa>=1.10"], label="Diarization")
        )
    assert "No release of sherpa-onnx>=1.10 can be installed on this machine" in str(ei.value)
    assert ei.value.log_excerpt == ""


# ── uninstall collects ─────────────────────────────────────────────────────────────


def test_uninstall_removes_what_only_that_app_needed_and_keeps_what_another_needs():
    _write_app("app-a", ["pclaw-only-a", "pclaw-shared"])
    _write_app("app-b", ["pclaw-only-b"])
    only_a = _fake_dist("pclaw-only-a", "1.0")
    shared = _fake_dist("pclaw-shared", "1.0")
    only_b = _fake_dist("pclaw-only-b", "1.0", requires=["pclaw-shared>=1"])
    assert only_a.is_dir()  # positive control: the package really is installed

    assert app_manager.force_uninstall("app-a") is True

    assert not only_a.exists(), "a package only the uninstalled app needed must go"
    assert shared.is_dir() and only_b.is_dir(), "a package another app reaches must stay"
    names = {d.metadata["Name"] for d in importlib.metadata.distributions(path=[str(_site())])}
    assert names == {"pclaw-shared", "pclaw-only-b"}


def test_collection_never_deletes_outside_the_directory(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    victim = _fake_dist("pclaw-evil", "1.0")
    record = next(_site().glob("pclaw_evil-1.0.dist-info")) / "RECORD"
    escape = os.path.relpath(outside, _site())
    record.write_text(record.read_text() + f"{escape},,\n{outside},,\n", encoding="utf-8")

    assert "pclaw-evil 1.0" in _ap().collect()
    assert not victim.exists() and outside.read_text() == "keep me"


def test_collection_never_deletes_a_file_a_kept_distribution_also_lists():
    """`opencv-python` and `opencv-python-headless` both install `cv2/`. Collecting the one no
    app needs must not delete the module the other one — still needed — is made of."""
    _write_app("needs-headless", ["pclaw-cv-headless"])
    shared = _fake_dist("pclaw-cv-headless", "1.0", module="cv2")
    _fake_dist("pclaw-cv-full", "1.0", module="cv2")  # same module dir, its own dist-info

    assert _ap().collect() == ["pclaw-cv-full 1.0"]

    assert (shared / "__init__.py").is_file(), "the kept distribution's module was deleted"
    names = {d.metadata["Name"] for d in importlib.metadata.distributions(path=[str(_site())])}
    assert names == {"pclaw-cv-headless"}


def test_a_conflict_with_another_installed_app_names_that_app(fake_pip):
    """pip prints requirements normalized, so matching them back to their app must normalize
    too — else the other app's requirement reads as the one being installed."""
    _write_app("vector-store-qdrant", ["pclaw-qdrant>=1.9,<2"])
    fake_pip(
        returncode=1,
        output=(
            "ERROR: Cannot install pclaw-qdrant<2,>=1.9 and pclaw-new-app==1.0 because these "
            "package versions have conflicting dependencies.\n\nThe conflict is caused by:\n"
            "    The user requested pclaw-qdrant<2,>=1.9\n"
            "    pclaw-new-app 1.0 depends on pclaw-qdrant>=2\n\n"
            "ERROR: ResolutionImpossible: for help visit x\n"
        ),
    )
    with pytest.raises(app_manager.AppLifecycleError) as ei:
        app_manager._install_python_deps(_manifest(["pclaw-new-app==1.0"], label="New App"))
    message = str(ei.value)
    assert "the installed app Vector Store Qdrant requires pclaw-qdrant<2,>=1.9" in message
    assert "pclaw-new-app 1.0 depends on pclaw-qdrant>=2" in message


def test_collection_drops_another_python_versions_layout():
    """After an image moves Python, the old version's extension modules are dead weight."""
    stale = _ap().root() / "lib" / "python3.9" / "site-packages" / "old_pkg"
    stale.mkdir(parents=True)
    _fake_dist("pclaw-kept", "1.0")
    _write_app("keeper", ["pclaw-kept"])
    _ap().collect()
    assert not stale.parent.parent.exists() and (_site() / "pclaw_kept").is_dir()


# ── update ──────────────────────────────────────────────────────────────────────────


def test_an_update_whose_packages_fail_leaves_the_installed_version_untouched(fake_pip, tmp_path):
    """The dependency step used to run AFTER the swap, with the rollback already dropped, so a
    failure left the new code live, installed.json un-bumped, and the result ok=False."""
    fake_pip()  # the 1.0.0 install needs nothing (numpy is core)
    assert app_manager.install(_source(tmp_path, "dep-app", ["numpy>=1.0"]), confirm=True).ok
    fake_pip(returncode=1, output=_EACCES)

    result = app_manager.update(
        _source(tmp_path, "dep-app", ["pclaw-fixture-boto3>=1.34"], version="2.0.0"), confirm=True
    )

    live = json.loads((manager.apps_dir() / "dep-app" / "app.json").read_text())
    assert live["version"] == "1.0.0", "the refused version must not be the one on disk"
    assert manager._read_installed("dep-app").version == "1.0.0"
    assert not (manager.apps_dir() / ".dep-app.rollback").exists()
    assert result.ok is False and "Couldn't install" in result.error


# ── the boot repair ─────────────────────────────────────────────────────────────────


def test_boot_repair_reinstalls_what_a_new_python_finds_missing(fake_pip):
    """A new image's Python reads its own layout (lib/python3.X) and finds it empty: every
    installed app with packages is broken until they are reinstalled from the manifests."""
    _write_app("dep-app", ["pclaw-fixture-dep==1.0"])
    assert [d.name for d, _ in _ap().broken_apps()] == ["dep-app"]
    fake = fake_pip(installs=[("pclaw-fixture-dep", "1.0", [])])

    assert app_manager.repair_app_packages() == ["dep-app"]

    assert "pclaw-fixture-dep==1.0" in fake.calls[0]
    assert _ap().broken_apps() == []


def test_boot_repair_with_nothing_missing_runs_no_pip(fake_pip):
    _write_app("core-only", ["numpy>=1.0"])
    fake = fake_pip()
    assert app_manager.repair_app_packages() == []
    assert fake.calls == []


# ── the real pip ────────────────────────────────────────────────────────────────────


def _wheel(
    directory: Path,
    name: str,
    version: str,
    *,
    requires: list[str] = (),
    extra: dict[str, str] | None = None,
) -> Path:
    """A minimal pure-Python wheel, so pip can install offline from a local directory.
    ``extra`` adds files (``{relative path: text}``), for a version that ships one the other
    does not."""
    dist, mod = name.replace("-", "_"), name.replace("-", "_")
    info = f"{dist}-{version}.dist-info"
    files = {
        f"{mod}/__init__.py": f"VERSION = {version!r}\n",
        **(extra or {}),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {r}\n" for r in requires),
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: pclaw-test\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n",
    }
    lines = []
    for rel, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        lines.append(f"{rel},sha256={digest.decode()},{len(text.encode())}")
    files[f"{info}/RECORD"] = "\n".join([*lines, f"{info}/RECORD,,"]) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{dist}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for rel, text in files.items():
            zf.writestr(rel, text)
    return path


class TestRealPip:
    @pytest.fixture(autouse=True)
    def _offline_index(self, tmp_path, monkeypatch):
        """pip reads PIP_* from the environment, and the installer passes those through."""
        self.wheels = tmp_path / "wheels"
        monkeypatch.setenv("PIP_NO_INDEX", "1")
        monkeypatch.setenv("PIP_FIND_LINKS", str(self.wheels))
        monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")

    def test_it_installs_into_the_home_and_never_into_the_running_environment(self):
        _wheel(self.wheels, "pclaw-fixture-dep", "1.0")
        before = {d.metadata["Name"] for d in importlib.metadata.distributions()}
        _write_app("dep-app", ["pclaw-fixture-dep==1.0"], installed=False)

        assert app_manager._install_python_deps(_manifest(["pclaw-fixture-dep==1.0"])) == []

        assert (_site() / "pclaw_fixture_dep" / "__init__.py").is_file()
        base = importlib.metadata.distributions(path=_ap()._base_paths())
        assert "pclaw-fixture-dep" not in {d.metadata["Name"] for d in base}
        assert "pclaw-fixture-dep" not in before
        assert importlib.import_module("pclaw_fixture_dep").VERSION == "1.0"
        sys.modules.pop("pclaw_fixture_dep", None)

        (manager.apps_dir() / "dep-app").joinpath("app.json").unlink()
        assert _ap().collect() == ["pclaw-fixture-dep 1.0"]

    def test_a_dependency_that_would_move_a_core_package_is_refused_by_the_resolver(self):
        """`packaging` is core. A dependency pinning it below the running version could only be
        satisfied by replacing it — which is what `--prefix` without the pins did (measured)."""
        _wheel(self.wheels, "pclaw-badpin", "1.0", requires=["packaging<1"])
        have = importlib.metadata.version("packaging")

        with pytest.raises(app_manager.AppLifecycleError) as ei:
            app_manager._install_python_deps(_manifest(["pclaw-badpin==1.0"], label="Bad Pin"))

        message = str(ei.value)
        assert f"PersonalClaw runs packaging {have}" in message, message
        assert importlib.metadata.version("packaging") == have
        assert not (_site() / "pclaw_badpin").exists()


# ── an update moves a pin: exactly the new version, nothing of the old ─────────────────
#
# Measured: PersonalClawApps #126 pinned `anthropic>=0.20,<1`, and an install that already had
# 1.8.0 in `<home>/app-python` kept crashing on it after the update. pip resolved the new pin,
# but running from the gateway's virtualenv it will not uninstall anything outside that
# environment ("Not uninstalling anthropic at …/app-python/…, outside environment …"), so it
# wrote the new version over the old: two dist-infos, and the old version's own files still
# there. Which copy then counted as installed was the directory listing's order, and that order
# is the file system's own: APFS lists `anthropic-1.8.0.dist-info` before
# `anthropic-0.125.0.dist-info`, and `pclaw_fixture_sdk-1.5` before `-2.0`, whichever was written
# first. So every move below runs in both directions: on any file system, one of them lists the
# old copy first.

SDK = "pclaw-fixture-sdk"

#: (installed before, what the new pin wants, the new pin), both ways.
_MOVES = [
    pytest.param("1.5", "2.0", f"{SDK}>=2", id="up"),
    pytest.param("2.0", "1.5", f"{SDK}<2", id="down"),
]


def _only_in(version: str) -> str:
    return f"pclaw_fixture_sdk/only_in_{version.replace('.', '_')}.py"


def _versions(name: str = SDK) -> list[str]:
    """Every copy of *name* in the app-package directory, by version."""
    found = importlib.metadata.distributions(path=[str(_site())])
    return sorted(d.version for d in found if d.metadata["Name"] == name)


def _fresh_import(module: str):
    sys.modules.pop(module, None)
    importlib.invalidate_caches()
    try:
        return importlib.import_module(module)
    finally:
        sys.modules.pop(module, None)


class TestAnUpdateMovesAPin:
    """Through the real `install` and `update`, with the real pip, offline."""

    @pytest.fixture(autouse=True)
    def _offline_index(self, tmp_path, monkeypatch):
        self.wheels = tmp_path / "wheels"
        monkeypatch.setenv("PIP_NO_INDEX", "1")
        monkeypatch.setenv("PIP_FIND_LINKS", str(self.wheels))
        monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")

    def _two_versions(self) -> None:
        for version in ("1.5", "2.0"):
            _wheel(self.wheels, SDK, version, extra={_only_in(version): f"X = {version!r}\n"})

    def _install(self, tmp_path: Path, pin: str) -> None:
        result = app_manager.install(_source(tmp_path, "dep-app", [pin]), confirm=True)
        assert result.ok, result.error

    def _update(self, tmp_path: Path, pin: str):
        return app_manager.update(
            _source(tmp_path, "dep-app", [pin], version="2.0.0"), confirm=True
        )

    @pytest.mark.parametrize("old, new, pin", _MOVES)
    def test_a_moved_pin_installs_that_version_and_nothing_of_the_old_one(
        self, tmp_path, old, new, pin
    ):
        self._two_versions()
        self._install(tmp_path, f"{SDK}=={old}")
        assert _versions() == [old]  # positive control: the version the old manifest pinned

        result = self._update(tmp_path, pin)

        assert result.ok, result.error
        assert _versions() == [new]
        assert not (_site() / _only_in(old)).exists()
        assert (_site() / _only_in(new)).is_file()
        assert _fresh_import("pclaw_fixture_sdk").VERSION == new

    def test_a_replaced_package_the_gateway_had_loaded_asks_for_a_restart(self, tmp_path):
        """The running process still holds the old module, so the new pin only takes effect
        after a restart, and the update has to say so."""
        self._two_versions()
        self._install(tmp_path, f"{SDK}<2")
        _ap().activate()
        importlib.import_module("pclaw_fixture_sdk")  # the gateway has used it
        try:
            result = self._update(tmp_path, f"{SDK}>=2")
        finally:
            sys.modules.pop("pclaw_fixture_sdk", None)

        assert result.ok, result.error
        assert result.restart_required is True

    def test_an_update_another_apps_pin_excludes_is_refused_and_changes_nothing(self, tmp_path):
        """One interpreter holds one version: an update may not move a package another installed
        app needs where it is. Refused, naming that app, with the installed version untouched."""
        self._two_versions()
        _write_app("other-app", [f"{SDK}>=2"])
        self._install(tmp_path, f"{SDK}>=1")
        assert _versions() == ["2.0"]

        result = self._update(tmp_path, f"{SDK}<2")

        assert result.ok is False
        assert (
            "the installed app Other App requires pclaw-fixture-sdk>=2" in result.error
        ), result.error
        assert _versions() == ["2.0"]
        assert manager._read_installed("dep-app").version == "1.0.0"

    @pytest.mark.parametrize("old, new, pin", _MOVES)
    def test_the_boot_repair_replaces_a_version_the_pin_no_longer_allows(self, old, new, pin):
        """The same move at boot: the installed apps' pins no longer allow what is here."""
        self._two_versions()
        _write_app("dep-app", [pin])
        subprocess.run(  # the version an earlier manifest left here
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--prefix",
                str(_root()),
                "--no-warn-script-location",
                f"{SDK}=={old}",
            ],
            check=True,
            capture_output=True,
        )
        assert _versions() == [old]

        assert app_manager.repair_app_packages() == ["dep-app"]

        assert _versions() == [new]
        assert not (_site() / _only_in(old)).exists()
        assert _fresh_import("pclaw_fixture_sdk").VERSION == new


@pytest.mark.parametrize("old, new, pin", _MOVES)
def test_a_directory_left_with_two_copies_heals_at_boot(fake_pip, old, new, pin):
    """What the installer left before this fix: the new version written over the old, both
    dist-infos present. The copy pip wrote last is what is on disk, so it is the one that counts,
    and the boot's collection removes the rest."""
    _write_app("dep-app", [pin])
    _fake_dist(SDK, old)
    (_site() / _only_in(old)).write_text("X = 1\n", encoding="utf-8")
    old_record = _site() / f"pclaw_fixture_sdk-{old}.dist-info" / "RECORD"
    old_record.write_text(old_record.read_text() + f"{_only_in(old)},,\n", encoding="utf-8")
    os.utime(old_record, ns=(1_000_000_000, 1_000_000_000))
    _fake_dist(SDK, new)  # pip writing the new version over the old, as the old installer did
    assert _versions() == sorted([old, new])  # positive control: the leftover state
    fake = fake_pip()

    assert app_manager.repair_app_packages() == []

    assert fake.calls == [], "the newest copy satisfies the pin; nothing needs reinstalling"
    assert _versions() == [new]
    assert not (_site() / _only_in(old)).exists()
    assert (_site() / "pclaw_fixture_sdk" / "__init__.py").read_text() == f"VERSION = {new!r}\n"
