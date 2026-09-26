"""Runtime use — does anything the APP RUNS load the file a finding sits in?

The install-consent dialog groups findings by this, because ``subprocess.run`` in an app's
own ``test_provider.py`` describes its test suite, not what installing the app does — and
a consent screen that presents the two identically tells a user their credentials are
being read by a test fixture. Measured on the real Store: 15 apps' dialogs led with
findings in test files (Spec Builder's test "reads a credential file and sends its
contents off this machine").

The answer is a CLAIM on a security surface, so it is proved the way reachability is:
structurally, never from a filename, and default-deny. The attack table below is the half
that matters — every shape in it must NOT come back ``unloaded``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.supply_chain import RuntimeUse, TrustTier, Verdict, default_scanner

_PROVIDER_APP = {
    "name": "demo",
    "version": "1.0.0",
    "provider": {"type": "tool", "implementation": "provider:create_provider"},
}

#: The provider the platform loads; it shells out to a PINNED program, so it cannot start
#: an arbitrary file.
_PROVIDER = (
    "import subprocess\n\n"
    "def create_provider(config=None):\n"
    '    return subprocess.run(["git", "--version"], capture_output=True)\n'
)

#: The app's own test: it spawns git too — a WARNING-band finding in a file nothing the
#: app runs imports.
_TEST = (
    "import subprocess\n\n"
    "import provider\n\n"
    "def test_it():\n"
    '    subprocess.run(["git", "init"], check=True)\n'
    "    assert provider.create_provider()\n"
)


def _bundle(tmp_path: Path, files: dict[str, str], manifest: dict | None = None) -> Path:
    root = tmp_path / "bundle"
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps(manifest or _PROVIDER_APP), encoding="utf-8")
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def _use(tmp_path: Path, files: dict[str, str], path: str, manifest: dict | None = None):
    report = default_scanner.scan(_bundle(tmp_path, files, manifest), TrustTier.COMMUNITY)
    found = [f for f in report.findings if f.path == path]
    assert found, f"no finding in {path}: {[f.path for f in report.findings]}"
    return found[0]


def test_the_apps_own_test_file_is_unloaded_and_its_entry_point_is_loaded(tmp_path):
    files = {"provider.py": _PROVIDER, "test_provider.py": _TEST}
    test = _use(tmp_path, files, "test_provider.py")
    assert test.runtime is RuntimeUse.UNLOADED, test.runtime_reason
    assert "nothing the app runs loads this file" in test.runtime_reason

    entry = _use(tmp_path / "again", files, "provider.py")
    assert entry.runtime is RuntimeUse.LOADED
    assert entry.runtime_reason == "app.json names it"


def test_it_is_disclosure_and_never_touches_severity_or_verdict(tmp_path):
    report = default_scanner.scan(
        _bundle(tmp_path, {"provider.py": _PROVIDER, "test_provider.py": _TEST}),
        TrustTier.COMMUNITY,
    )
    assert report.verdict is Verdict.WARNING
    unloaded = [f for f in report.findings if f.runtime is RuntimeUse.UNLOADED]
    assert unloaded and all(f.severity is Verdict.WARNING for f in unloaded)
    wire = unloaded[0].to_dict()
    assert wire["runtime"] == "unloaded" and wire["runtime_reason"]


# ── the attack table: every one of these must stay OUT of "unloaded" ──────────────────────

_ATTACKS: dict[str, tuple[dict[str, str], dict | None, str]] = {
    # A file named like a test that the provider imports is runtime code. The name buys
    # an attacker nothing.
    "a test-named module the provider imports": (
        {
            "provider.py": "import test_helpers\n\ndef create_provider(config=None):\n"
            "    return test_helpers.go()\n",
            "test_helpers.py": 'import subprocess\n\ndef go():\n    subprocess.run(["git", "x"])\n',
        },
        None,
        "test_helpers.py",
    ),
    # `import pkg.sub.mod` runs `pkg/sub/__init__.py` first, though nothing names `pkg.sub`
    # on its own — only the dotted prefix of the import reaches it.
    "a package __init__ run by importing its submodule": (
        {
            "provider.py": "import pkg.sub.mod\n\ndef create_provider(config=None):\n"
            "    return pkg.sub.mod\n",
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": 'import subprocess\nsubprocess.run(["git", "status"])\n',
            "pkg/sub/mod.py": "X = 1\n",
        },
        None,
        "pkg/sub/__init__.py",
    ),
    # A loaded module that starts a program with a command it builds could run any file.
    "a loaded module that spawns a command it builds": (
        {
            "provider.py": "import subprocess, sys\n\ndef create_provider(config=None):\n"
            "    return subprocess.run([sys.executable, config['script']])\n",
            "test_provider.py": _TEST,
        },
        None,
        "test_provider.py",
    ),
    # A loaded module that imports by a computed name could load any file.
    "a loaded module that imports dynamically": (
        {
            "provider.py": "import importlib\n\ndef create_provider(config=None):\n"
            "    return importlib.import_module(config['mod'])\n",
            "test_provider.py": _TEST,
        },
        None,
        "test_provider.py",
    ),
    # A test runner loads test_*.py by convention, with nothing naming them.
    "an install hook that runs the test suite": (
        {"provider.py": _PROVIDER, "test_provider.py": _TEST},
        {**_PROVIDER_APP, "setup": {"onInstall": "python -m pytest -q"}},
        "test_provider.py",
    ),
    # A shell script the app runs can start any Python file by a path it builds.
    "a shell hook that starts Python": (
        {
            "provider.py": _PROVIDER,
            "test_provider.py": _TEST,
            "setup.sh": 'for f in *.py; do python3 "$f"; done\n',
        },
        {**_PROVIDER_APP, "setup": {"onInstall": "bash setup.sh"}},
        "test_provider.py",
    ),
    # The worker entry point runs by NAME — nothing has to point at it.
    "the convention-loaded worker": (
        {
            "provider.py": _PROVIDER,
            "worker.py": 'import subprocess\nsubprocess.run(["git", "gc"])\n',
        },
        None,
        "worker.py",
    ),
    # A loaded module that names the file in a string can open or run it.
    "a file a loaded module names in a string": (
        {
            "provider.py": "import runpy_free\n\nJOB = 'jobs/nightly.py'\n\n"
            "def create_provider(config=None):\n    return JOB\n",
            "runpy_free.py": "X = 1\n",
            "jobs/nightly.py": 'import subprocess\nsubprocess.run(["git", "fetch"])\n',
        },
        None,
        "jobs/nightly.py",
    ),
}


@pytest.mark.parametrize("label", sorted(_ATTACKS))
def test_no_way_in_that_the_analysis_cannot_see_reads_as_unloaded(tmp_path, label):
    files, manifest, path = _ATTACKS[label]
    finding = _use(tmp_path, files, path, manifest)
    assert finding.runtime in (
        RuntimeUse.LOADED,
        RuntimeUse.UNTRACEABLE,
    ), f"{label}: {finding.runtime} — {finding.runtime_reason}"


def test_an_untraceable_bundle_still_says_which_files_the_app_does_run(tmp_path):
    """UNTRACEABLE is its own answer, never folded into LOADED for everything: the files
    the app demonstrably runs keep the stronger statement."""
    files = {
        "provider.py": "import subprocess, sys\n\ndef create_provider(config=None):\n"
        "    return subprocess.run([sys.executable, config['script']])\n",
        "test_provider.py": _TEST,
    }
    report = default_scanner.scan(_bundle(tmp_path, files), TrustTier.COMMUNITY)
    by_path = {f.path: f for f in report.findings}
    assert by_path["provider.py"].runtime is RuntimeUse.LOADED
    assert by_path["test_provider.py"].runtime is RuntimeUse.UNTRACEABLE
    assert "provider.py can start a program" in by_path["test_provider.py"].runtime_reason


def test_dynamic_loading_inside_a_file_the_app_never_runs_does_not_poison_the_rest(tmp_path):
    """A test-only ``conftest.py`` that edits ``sys.path`` never runs when the app does, so
    it cannot hide an edge — only what LOADED code does can."""
    files = {
        "provider.py": _PROVIDER,
        "test_provider.py": _TEST,
        "conftest.py": "import sys\nsys.path.insert(0, '.')\n",
    }
    assert _use(tmp_path, files, "test_provider.py").runtime is RuntimeUse.UNLOADED


def test_a_finding_outside_python_is_not_analysed(tmp_path):
    files = {"provider.py": _PROVIDER, "scripts/fetch.sh": "curl https://example.com\n"}
    finding = _use(tmp_path, files, "scripts/fetch.sh")
    assert finding.runtime is RuntimeUse.NOT_ANALYSED and finding.runtime_reason == ""


def test_the_worker_convention_is_the_one_the_platform_runs():
    """Pinned against the platform's own constant, so renaming the worker entry point
    cannot silently turn a real entry point into an "unloaded" file."""
    from personalclaw import supply_chain
    from personalclaw.apps.background import WORKER_ENTRY_POINT

    assert WORKER_ENTRY_POINT in supply_chain._CONVENTION_ENTRY_NAMES
