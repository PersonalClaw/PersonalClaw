"""The scanner's terminal band judges what the app RUNS.

A destructive string in an app's own test — the one that proves a revision like
``x; rm -rf /`` is refused — is test data. When nothing the app runs loads that test file,
and the file cannot hand a string to an interpreter itself, the finding is disclosed and
consentable (WARNING), never a refusal with no way past it. The bundles below are shaped
like a git-backed notebook app: its provider runs git through the SDK's argv builder, and
its tests put a shared test kit on ``sys.path`` before they import it.

The same string in a file the app DOES load stays terminal, whichever way it is loaded: an
import, a dotted import of a package module, the manifest, or the name the platform runs.

"Nothing the app runs loads it" is a claim on a security surface, so the attack table holds
every way the analysis cannot see past — each keeps the string terminal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw import supply_chain
from personalclaw.supply_chain import (
    Reachability,
    RuntimeUse,
    TrustTier,
    Verdict,
    default_scanner,
)

_APP = {
    "name": "notebook-demo",
    "version": "1.0.0",
    "provider": {"type": "tool", "implementation": "provider:create_provider"},
}

_PROVIDER = (
    "from notebook import Notebook\n\n\n"
    "def create_provider(config=None):\n"
    "    return Notebook()\n"
)


def _notebook(spawn: str, *, head: str = "") -> str:
    """The module the provider loads: it validates a revision, and runs git with ``spawn``."""
    return (
        "import subprocess\n"
        "import sys\n\n"
        "from personalclaw.sdk.git import git_argv, git_env\n"
        f"{head}\n\n"
        "def parse_revision(raw):\n"
        "    if not raw.isalnum():\n"
        "        raise ValueError(raw)\n"
        "    return raw\n\n\n"
        "class Notebook:\n"
        "    def run(self, args, program='git', script='x.py'):\n"
        f"{spawn}"
    )


#: The shape the scanner could not pin: the SDK's argv builder, bound to a local name first.
_BOUND_BUILDER = (
    "        argv = git_argv(['-C', '.', *args])\n"
    "        return subprocess.run(argv, capture_output=True, text=True, env=git_env())\n"
)
#: The builder handed straight to the spawn.
_INLINE_BUILDER = "        return subprocess.run(git_argv(['-C', '.', *args]), env=git_env())\n"
#: A plain list whose program is a literal.
_PINNED_LIST = "        return subprocess.run(['git', '-C', '.', *args], env=git_env())\n"

_REFUSED = "x; rm -rf /"

#: The app's own test: the test kit goes on ``sys.path`` at import, and a refused revision
#: is a literal in its parametrisation.
_TEST = (
    "import sys\n"
    "from pathlib import Path\n\n"
    "import pytest\n\n"
    "from notebook import parse_revision\n\n"
    "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n\n\n"
    f'@pytest.mark.parametrize("rev", ["HEAD^", "{_REFUSED}"])\n'
    "def test_a_revision_that_is_not_a_sha_is_refused(rev):\n"
    "    with pytest.raises(ValueError):\n"
    "        parse_revision(rev)\n"
)


def _bundle(tmp_path: Path, files: dict[str, str], manifest: dict | None = None) -> Path:
    root = tmp_path / "bundle"
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.json").write_text(json.dumps(manifest or _APP), encoding="utf-8")
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def _destructive(tmp_path: Path, files: dict[str, str], manifest: dict | None = None):
    report = default_scanner.scan(_bundle(tmp_path, files, manifest), TrustTier.COMMUNITY)
    found = [f for f in report.findings if f.rule == "destructive_root"]
    assert len(found) == 1, [(f.rule, f.path) for f in report.findings]
    return report, found[0]


# ── a refusal string in the app's own tests is disclosed, not refused ─────────────────────


@pytest.mark.parametrize(
    "spawn",
    [_BOUND_BUILDER, _INLINE_BUILDER, _PINNED_LIST],
    ids=["argv-built-by-the-sdk-then-bound", "argv-built-by-the-sdk-inline", "pinned-list"],
)
def test_a_refusal_string_in_the_apps_own_test_installs_with_a_warning(tmp_path, spawn):
    files = {"provider.py": _PROVIDER, "notebook.py": _notebook(spawn), "test_provider.py": _TEST}
    report, finding = _destructive(tmp_path, files)

    assert report.verdict is Verdict.WARNING, finding.reachability_reason
    assert finding.path == "test_provider.py"
    assert finding.severity is Verdict.WARNING
    assert finding.reachability is Reachability.NOT_RUN, finding.reachability_reason
    assert finding.runtime is RuntimeUse.UNLOADED, finding.runtime_reason
    assert "nothing the app runs loads this file" in finding.reachability_reason
    assert finding.to_dict()["reachability"] == "not_run"


def test_the_rescored_finding_keeps_its_whole_disclosure(tmp_path):
    """Only the severity moves: rule, surface and evidence are what the raw rule reported."""
    files = {
        "provider.py": _PROVIDER,
        "notebook.py": _notebook(_BOUND_BUILDER),
        "test_provider.py": _TEST,
    }
    _, scored = _destructive(tmp_path, files)
    raw = [
        f
        for f in default_scanner.scan_text(_TEST, surface="script").findings
        if f.rule == "destructive_root"
    ][0]
    assert (scored.rule, scored.surface, scored.evidence) == (raw.rule, raw.surface, raw.evidence)
    assert _REFUSED in scored.evidence
    assert raw.severity is Verdict.DANGEROUS


def test_a_rescored_test_string_is_a_warning_like_any_other_under_every_tier(tmp_path):
    root = _bundle(
        tmp_path,
        {
            "provider.py": _PROVIDER,
            "notebook.py": _notebook(_BOUND_BUILDER),
            "test_provider.py": _TEST,
        },
    )
    for tier in TrustTier:
        verdict = default_scanner.scan(root, tier).verdict
        assert verdict is not Verdict.CLEAN, tier
        assert verdict.rank <= Verdict.WARNING.rank, tier


# ── the same string in a file the app loads stays terminal ────────────────────────────────

_HELD = f'REFUSED = ["HEAD^", "{_REFUSED}"]\n'

_LOADED_ROUTES: dict[str, tuple[dict[str, str], dict | None, str]] = {
    "the provider imports it": (
        {
            "provider.py": "from refusals import REFUSED\n" + _PROVIDER,
            "refusals.py": _HELD,
        },
        None,
        "refusals.py",
    ),
    # `import pkg.refusals` names the module only by its dotted path, never by its stem.
    "the provider imports a package module by its dotted name": (
        {
            "provider.py": "import pkg.refusals\n" + _PROVIDER,
            "pkg/__init__.py": "",
            "pkg/refusals.py": _HELD,
        },
        None,
        "pkg/refusals.py",
    ),
    "the manifest names it": (
        {"provider.py": _PROVIDER, "refusals.py": _HELD},
        {**_APP, "cli": {"setup": "refusals:setup"}},
        "refusals.py",
    ),
    "the platform runs it by its entry-point name": (
        {"provider.py": _PROVIDER, "worker.py": _HELD},
        None,
        "worker.py",
    ),
}


@pytest.mark.parametrize("label", sorted(_LOADED_ROUTES))
def test_the_same_string_in_a_file_the_app_loads_stays_terminal(tmp_path, label):
    files, manifest, path = _LOADED_ROUTES[label]
    files = {**files, "notebook.py": _notebook(_BOUND_BUILDER)}
    report, finding = _destructive(tmp_path, files, manifest)

    assert finding.path == path
    assert report.verdict is Verdict.DANGEROUS, f"{label}: {finding.reachability_reason}"
    assert finding.severity is Verdict.DANGEROUS
    assert finding.reachability is Reachability.REACHABLE
    assert "the app loads this file" in finding.reachability_reason, finding.reachability_reason
    assert finding.runtime is RuntimeUse.LOADED


def test_a_package_module_the_provider_runs_cannot_hand_its_string_to_a_shell(tmp_path):
    """The dotted route end to end: the provider passes the module's string to ``os.system``.
    The string sits in a file the app loads, so it is refused outright."""
    files = {
        "provider.py": "import os\n\nimport pkg.refusals\n\n\n"
        "def create_provider(config=None):\n    os.system(pkg.refusals.REFUSED[1])\n",
        "pkg/__init__.py": "",
        "pkg/refusals.py": _HELD,
    }
    report, finding = _destructive(tmp_path, files)
    assert report.verdict is Verdict.DANGEROUS
    assert finding.reachability is Reachability.REACHABLE, finding.reachability_reason


# ── the "never loaded" proof cannot be talked into a false answer ─────────────────────────

_ATTACKS: dict[str, tuple[dict[str, str], dict | None]] = {
    "the loaded module starts a program the source does not pin": (
        {"notebook.py": _notebook("        return subprocess.run([sys.executable, script])\n")},
        None,
    ),
    "the loaded module starts a launcher that runs its arguments": (
        {"notebook.py": _notebook("        return subprocess.run(['nohup', 'python3', script])\n")},
        None,
    ),
    "the loaded module starts a versioned interpreter": (
        {"notebook.py": _notebook("        return subprocess.run(['python3.12', script])\n")},
        None,
    ),
    "the loaded module starts a test runner": (
        {"notebook.py": _notebook("        return subprocess.run(['pytest', '-q'])\n")},
        None,
    ),
    "the loaded module runs the test suite in-process": (
        {"notebook.py": _notebook("        return pytest.main(['-q'])\n", head="import pytest\n")},
        None,
    ),
    "an install hook runs the test suite": (
        {"notebook.py": _notebook(_BOUND_BUILDER)},
        {**_APP, "setup": {"onInstall": "python -m pytest -q"}},
    ),
    # An app's own process puts its folder first on `sys.path`, so this package is the one
    # its `from personalclaw.sdk.git import git_argv` would load.
    "the bundle ships a module that stands in for the platform's SDK": (
        {
            "notebook.py": _notebook(_BOUND_BUILDER),
            "personalclaw/__init__.py": "",
            "personalclaw/sdk/__init__.py": "",
            "personalclaw/sdk/git.py": "def git_argv(args):\n    return list(args)\n\n\n"
            "def git_env():\n    return {}\n",
        },
        None,
    ),
    "a module the app loads replaces the SDK's builder": (
        {
            "notebook.py": _notebook(_BOUND_BUILDER),
            "provider.py": "import personalclaw.sdk.git as sdk_git\n\nsdk_git.git_argv = list\n"
            + _PROVIDER,
        },
        None,
    ),
    "a module the app loads replaces the SDK's builder with setattr": (
        {
            "notebook.py": _notebook(_BOUND_BUILDER),
            "provider.py": "from personalclaw.sdk import git\n\nsetattr(git, 'git_argv', list)\n"
            + _PROVIDER,
        },
        None,
    ),
    "the bound argv is changed before it runs": (
        {
            "notebook.py": _notebook(
                "        argv = git_argv(['-C', '.', *args])\n"
                "        argv[0] = program\n"
                "        return subprocess.run(argv)\n"
            )
        },
        None,
    ),
    "the builder is told which program to run": (
        {
            "notebook.py": _notebook(
                "        return subprocess.run(git_argv(['-C', '.', *args], git=program))\n"
            )
        },
        None,
    ),
}


@pytest.mark.parametrize("label", sorted(_ATTACKS))
def test_no_way_the_analysis_cannot_see_reads_as_never_loaded(tmp_path, label):
    extra, manifest = _ATTACKS[label]
    files = {"provider.py": _PROVIDER, "test_provider.py": _TEST, **extra}
    report, finding = _destructive(tmp_path, files, manifest)

    assert finding.path == "test_provider.py"
    assert finding.runtime is not RuntimeUse.UNLOADED, f"{label}: {finding.runtime_reason}"
    assert report.verdict is Verdict.DANGEROUS, f"{label}: {finding.reachability_reason}"
    assert finding.reachability is Reachability.REACHABLE


def test_a_test_file_that_can_hand_a_string_to_a_shell_stays_terminal(tmp_path):
    """Never loaded is not enough on its own: the file holding the string must not be able
    to run one (L2), or running the tests by hand would run it."""
    shelling = (
        _TEST + "\n\ndef _sh(cmd):\n    import subprocess\n    subprocess.run(cmd, shell=True)\n"
    )
    files = {
        "provider.py": _PROVIDER,
        "notebook.py": _notebook(_BOUND_BUILDER),
        "test_provider.py": shelling,
    }
    report, finding = _destructive(tmp_path, files)
    assert finding.runtime is RuntimeUse.UNLOADED, finding.runtime_reason
    assert report.verdict is Verdict.DANGEROUS
    assert "(L2)" in finding.reachability_reason


def test_a_destructive_call_in_a_test_file_stays_terminal(tmp_path):
    """A call is code, not a string the file holds, so where it sits changes nothing."""
    test = _TEST + "\n\ndef test_cleanup():\n    import shutil\n    shutil.rmtree('/')\n"
    files = {
        "provider.py": _PROVIDER,
        "notebook.py": _notebook(_BOUND_BUILDER),
        "test_provider.py": test,
    }
    report = default_scanner.scan(_bundle(tmp_path, files), TrustTier.COMMUNITY)
    native = [f for f in report.findings if f.rule == "destructive_delete"]
    assert native and native[0].severity is Verdict.DANGEROUS
    assert report.verdict is Verdict.DANGEROUS


def test_the_launcher_and_interpreter_names_are_load_bearing(tmp_path, monkeypatch):
    """Emptied, a launcher spawn no longer counts as a way to start a file, and the test
    string would be re-scored: so the attack rows above test the names, not the fixture."""
    extra, manifest = _ATTACKS["the loaded module starts a launcher that runs its arguments"]
    files = {"provider.py": _PROVIDER, "test_provider.py": _TEST, **extra}
    monkeypatch.setattr(supply_chain, "_SHELL_ARGV0", frozenset({"sh"}))
    report, finding = _destructive(tmp_path, files, manifest)
    assert finding.runtime is RuntimeUse.UNLOADED
    assert report.verdict is Verdict.WARNING


# ── reading a spawn's program: precision that cannot widen the floor ──────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "import subprocess\n\nX = subprocess.run(['git', 'log'], capture_output=True, text=True)"
        ".stdout.split()\n",
        "import subprocess\n\nX = subprocess.CompletedProcess(['git'], 0, '', '')\n",
        "import subprocess\nfrom personalclaw.sdk.git import git_argv\n\n\n"
        "def f(args):\n    argv = git_argv(args)\n    return subprocess.run(argv)\n",
        "import subprocess\nfrom personalclaw.sdk import git\n\n\n"
        "def f(args):\n    return subprocess.run(git.git_argv(args, token=True))\n",
    ],
    ids=["a-call-on-the-result", "a-result-object", "bound-sdk-argv", "sdk-module-alias"],
)
def test_what_is_not_a_way_to_run_a_string_is_not_a_sink(body):
    assert supply_chain._analyse_python(body).sinks == set()


@pytest.mark.parametrize(
    "body",
    [
        "import subprocess\n\n\ndef f(argv):\n    return subprocess.run(argv)\n",
        "import subprocess\n\nARGV = ['git']\n\n\ndef f():\n    return subprocess.run(ARGV)\n",
        "import subprocess\nfrom personalclaw.sdk.git import git_argv\n\n\n"
        "def f(args, extra):\n    return subprocess.run(git_argv(args, **extra))\n",
        "import subprocess\n\n\ndef f(args):\n    argv = ['git', *args]\n    argv.insert(0, 'sh')\n"
        "    return subprocess.run(argv)\n",
    ],
    ids=["a-parameter", "a-module-level-name", "keywords-the-source-does-not-show", "mutated"],
)
def test_an_argv_the_source_does_not_pin_is_still_a_sink(body):
    assert supply_chain._analyse_python(body).sinks
