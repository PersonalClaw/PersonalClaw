"""``tools/ci/capabilities.py``: the host-capability fingerprint a recorded pass is keyed on.

What the suite probes is read from a ``tests/`` tree by AST, never imported, and each probed tool
is named by its resolved path and a digest of its bytes, so nothing is ever started. The suite's
skip switches are inputs too. Every case plants its own tree and stand-in tools under
``tmp_path``, but two: the browser lookup is held to ``tests/browse_chrome.py``'s own, imported
and read from this repository, and the import check reads ``tools/ci/capabilities.py`` itself.
The rail in ``tests/rails`` holds the scan to this repository's tree.

Security-marked: a probe the fingerprint missed would let a pass recorded with a tool stand for
a run without it.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from table_rows import row

from tools.ci import capabilities as cap
from tools.ci import verdict_store as vs

pytestmark = pytest.mark.security

_CONTAINER = 'PROGRAMS = frozenset({"docker", "limactl"})\n'
_CONTAINER_ON_PATH = (
    "import shutil\n"
    "def _on_path(candidates):\n"
    "    for name in candidates:\n"
    "        if shutil.which(name):\n"
    "            return name\n"
)
_CHROME = textwrap.dedent("""\
    CHROME_ENV = "PERSONALCLAW_TEST_CHROME"
    REQUIRE_ENV = "PERSONALCLAW_REQUIRE_BROWSE_PROOF"
    _REVISION_DIR_PREFIXES = ("chromium_headless_shell-", "chromium-")
    _BINARY_GLOBS = ("*/chrome-headless-shell", "*/chrome")
    """)


def _tree(tmp_path: Path, files: dict[str, str], *, base: bool = True) -> Path:
    """A repository root whose ``tests/`` holds *files*, beside the two modules every tree has."""
    root = tmp_path / "repo"
    every = {
        "tests/container_runtime.py": _CONTAINER + _CONTAINER_ON_PATH,
        "tests/browse_chrome.py": _CHROME,
    }
    for rel, text in {**(every if base else {}), **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def test_it_imports_only_the_standard_library() -> None:
    """A trusted job runs it on the runner image's own python3, and it imports no test module."""
    tree = ast.parse(Path(cap.__file__).read_text(encoding="utf-8"))
    names = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    names.discard("__future__")
    assert names and names <= set(sys.stdlib_module_names), names
    assert not any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree))


# ── the probe list, read from the suite ──────────────────────────────────────────────────────

SCAN_ROWS: list[tuple[str, str, set[str]]] = [
    ("a literal", 'import shutil\nGIT = shutil.which("git")\n', {"git"}),
    (
        "in a skip mark",
        'import shutil, pytest\nm = pytest.mark.skipif(shutil.which("tmux") is None, reason="x")\n',
        {"tmux"},
    ),
    ("shutil under another name", 'import shutil as sh\nsh.which("ffmpeg")\n', {"ffmpeg"}),
    ("which imported by name", 'from shutil import which\nwhich("node")\n', {"node"}),
    ("which under another name", 'from shutil import which as w\nw("npm")\n', {"npm"}),
    ("which under a star import", 'from shutil import *\nwhich("pnpm")\n', {"pnpm"}),
    (
        "a loop over a module constant",
        'import shutil\nTOOLS = ("cat", "uname")\n'
        "def f():\n    for t in TOOLS:\n        shutil.which(t)\n",
        {"cat", "uname"},
    ),
    (
        "a loop over a literal",
        'import shutil\ndef f():\n    for t in ["lsof", "ss"]:\n        shutil.which(t)\n',
        {"lsof", "ss"},
    ),
    (
        "a helper's parameter, from every call of it",
        "import shutil\ndef _tool_or_skip(tool):\n    return shutil.which(tool)\n"
        'def test_a():\n    _tool_or_skip("dash")\n'
        'def test_b():\n    _tool_or_skip(tool="shellcheck")\n',
        {"dash", "shellcheck"},
    ),
    # A PATH a test builds may still hold the host's own folders, and the scan cannot tell one
    # from the other, so a lookup names its tool whatever PATH it is given.
    (
        "a lookup on a PATH of the test's own",
        'import shutil\nshutil.which("curl", path="/x")\n',
        {"curl"},
    ),
    (
        "a PATH given by position",
        'import os, shutil\nshutil.which("wget", os.X_OK, "/x")\n',
        {"wget"},
    ),
    ("path=None is the host's PATH", 'import shutil\nshutil.which("jq", path=None)\n', {"jq"}),
    (
        "the host's PATH spelled out",
        'import os, shutil\nshutil.which("jq", path=os.environ["PATH"])\n',
        {"jq"},
    ),
    ("a mode given by position", "import os, shutil\nshutil.which('jq', os.X_OK)\n", {"jq"}),
    ("the tool by keyword", 'import shutil\nshutil.which(cmd="git")\n', {"git"}),
    (
        "a generator over literals",
        'import shutil\nOK = all(shutil.which(t) for t in ("git", "tmux"))\n',
        {"git", "tmux"},
    ),
    (
        "an annotated constant",
        'import shutil\nTOOLS: tuple[str, ...] = ("cat",)\n'
        "def f():\n    for t in TOOLS:\n        shutil.which(t)\n",
        {"cat"},
    ),
    ("another module's shutil", 'import cli\ncli.shutil.which("git")\n', {"git"}),
    (
        "a helper named only in a string of another module",
        "import shutil\ndef _probe(tool):\n    return shutil.which(tool)\n_probe('git')\n",
        {"git"},
    ),
    # It imports shutil, so the row reaches the check that `other` is not shutil.
    ("which on something that is not shutil", 'import shutil, other\nother.which("gh")\n', set()),
]


def test_each_probe_shape_is_read(tmp_path: Path, subtests: Any) -> None:
    assert len(SCAN_ROWS) == 20, "a scan row was dropped from the table"
    for n, (name, source, expected) in enumerate(SCAN_ROWS):
        with row(subtests, case=name):
            files = {"tests/test_x.py": source, "tests/test_y.py": "NOTE = '_probe(\"tmux\")'\n"}
            root = _tree(tmp_path / str(n), files)
            # The container runtime's programs are probes whatever the planted file holds.
            assert set(cap.probe_list(root)) == expected | {"docker", "limactl"}


def test_which_under_another_name_is_found_with_and_without_the_prefilter(
    tmp_path: Path,
) -> None:
    """``from shutil import which as w`` never spells ``which`` at the call, but it spells
    ``shutil``, so the prefilter keeps the file and the alias is followed."""
    source = "from shutil import which as w\n\ndef test_it():\n    assert w('jq')\n"
    root = _tree(tmp_path, {"tests/test_alias.py": source})
    for prefilter in (True, False):
        assert "jq" in cap.scan(root, prefilter=prefilter).probes, prefilter


def test_the_prefilter_skips_only_files_that_never_name_shutil(tmp_path: Path) -> None:
    """A file without the word holds no probe the scan reads, so it is left unparsed: even one
    that does not parse, which pytest's own collection fails. The full reading still refuses it."""
    root = _tree(
        tmp_path,
        {
            "tests/test_plain.py": "def test_it():\n    assert True\n",
            "tests/test_broken.py": "def broken(:\n",
            "tests/test_git.py": 'import shutil\nshutil.which("git")\n',
        },
    )
    assert cap.scan(root).probes == {"git"}
    with pytest.raises(cap.Refused, match="test_broken.py"):
        cap.scan(root, prefilter=False)


def test_a_test_module_under_a_subfolder_is_read(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"tests/security/test_y.py": 'import shutil\nshutil.which("gh")\n'})
    assert "gh" in cap.probe_list(root)


UNRESOLVED_ROWS: list[tuple[str, str, str]] = [
    ("a name from nowhere", "import shutil\ndef f(x):\n    shutil.which(os.environ['T'])\n", "f"),
    (
        "a helper no one calls",
        "import shutil\ndef _probe(tool):\n    return shutil.which(tool)\n",
        "_probe",
    ),
    (
        "a helper called with a literal and with a name",
        "import shutil\ndef _probe(tool):\n    return shutil.which(tool)\n"
        'def test_a(t):\n    _probe("git")\n    _probe(t)\n',
        "_probe",
    ),
    (
        "a loop over a constant that is not all text",
        "import shutil\nTOOLS = ('git', 3)\n"
        "def g():\n    for t in TOOLS:\n        shutil.which(t)\n",
        "g",
    ),
    ("a literal that is not text", "import shutil\nshutil.which(3)\n", "<module>"),
    (
        "a helper given a literal that is not text",
        "import shutil\ndef _probe(tool):\n    return shutil.which(tool)\n_probe(3)\n",
        "_probe",
    ),
    (
        "a constant built by another call",
        'import shutil\nTOOLS = _suffixed(("git",))\n'
        "def f():\n    for t in TOOLS:\n        shutil.which(t)\n",
        "f",
    ),
    (
        "a loop over another call's result",
        'import shutil\ndef f():\n    for t in _suffixed(("git",)):\n        shutil.which(t)\n',
        "f",
    ),
    (
        "a lambda's own parameter",
        'import shutil\ndef helper(t):\n    return lambda t: shutil.which(t)\nhelper("git")\n',
        "<lambda>",
    ),
    # Which taken as a value is called later with any tool.
    ("which bound to a name first", 'import shutil\nw = shutil.which\nw("git")\n', "<module>"),
    (
        "which passed to map",
        'import shutil\nOK = all(map(shutil.which, ("git", "tmux")))\n',
        "<module>",
    ),
    (
        "which imported by name, taken as a value",
        'from shutil import which\ndef f():\n    return list(map(which, ["git"]))\n',
        "f",
    ),
    (
        "another module's shutil.which taken as a value",
        'import cli\ndef f():\n    real = cli.shutil.which\n    return real("git")\n',
        "f",
    ),
    # A helper reached other than by a call its module makes by name is called with anything.
    (
        "a helper taken as a value in its own module",
        "import shutil\ndef _probe(tool):\n    return shutil.which(tool)\n"
        '_probe("git")\nOK = list(map(_probe, NAMES))\n',
        "_probe",
    ),
    (
        "a helper called through an attribute in its own module",
        "import shutil, sys\ndef _probe(tool):\n    return shutil.which(tool)\n"
        '_probe("git")\nsys.modules[__name__]._probe("tmux")\n',
        "_probe",
    ),
    # A name bound more than once, or a collection that can change in place, is no constant.
    (
        "a constant extended by +=",
        'import shutil\nTOOLS = ("git",)\nTOOLS += ("tmux",)\n'
        "def f():\n    for t in TOOLS:\n        shutil.which(t)\n",
        "f",
    ),
    (
        "a constant reassigned after a module-level loop",
        'import shutil\nTOOLS = ("git",)\n'
        'for t in TOOLS:\n    shutil.which(t)\nTOOLS = ("tmux",)\n',
        "<module>",
    ),
    (
        "a constant rebound in a function",
        'import shutil\nTOOLS = ("git",)\n'
        'def f():\n    TOOLS = ("tmux",)\n    for t in TOOLS:\n        shutil.which(t)\n',
        "f",
    ),
    (
        "a list constant appended to",
        'import shutil\nTOOLS = ["git"]\nTOOLS.append("tmux")\n'
        "def f():\n    for t in TOOLS:\n        shutil.which(t)\n",
        "f",
    ),
    (
        "a constant under a star import",
        'import shutil\nTOOLS = ("git",)\nfrom tools_elsewhere import *\n'
        "def f():\n    for t in TOOLS:\n        shutil.which(t)\n",
        "f",
    ),
    (
        "a lookup on a PATH it is given",
        "import shutil\ndef refusal(named, env):\n"
        '    return shutil.which(named, path=env["PATH"])\n',
        "refusal",
    ),
]


def test_a_probe_the_scan_cannot_name_is_refused(tmp_path: Path, subtests: Any) -> None:
    """A probe of a tool the fingerprint cannot name would let one host's pass stand for
    another's, so the list is refused rather than read short."""
    assert len(UNRESOLVED_ROWS) == 21, "an unresolved row was dropped from the table"
    for n, (name, source, function) in enumerate(UNRESOLVED_ROWS):
        with row(subtests, case=name):
            root = _tree(tmp_path / str(n), {"tests/test_x.py": source})
            assert ("tests/test_x.py", function) in cap.scan(root).unresolved
            with pytest.raises(cap.Refused, match=f"tests/test_x.py.*{function}"):
                cap.probe_list(root)


def test_a_helper_another_module_reaches_is_refused(tmp_path: Path, subtests: Any) -> None:
    """A helper's parameter is read from its own module's calls, so a helper another module
    imports or reaches, and may call with any tool, is a site the scan cannot name."""
    helper = 'import shutil\ndef need(tool):\n    return shutil.which(tool)\nGIT = need("git")\n'
    rows = {
        "imported by name": "from tool_need import need\nassert need('tmux')\n",
        "through its module": "import tool_need\nassert tool_need.need('tmux')\n",
        "imported under another name": "from tool_need import need as n\nassert n('tmux')\n",
        "in a file that does not parse": "need(\n",
    }
    for n, (name, caller) in enumerate(rows.items()):
        with row(subtests, case=name):
            files = {"tests/tool_need.py": helper, "tests/test_y.py": caller}
            root = _tree(tmp_path / str(n), files)
            assert ("tests/tool_need.py", "need") in cap.scan(root).unresolved
            with pytest.raises(cap.Refused, match="tool_need.py in need"):
                cap.probe_list(root)


_CONTAINER_GUARD = (
    "class Guard:\n"
    "    def refusal(self, args, env):\n"
    "        named = args[0]\n"
    "        if named not in PROGRAMS:\n"
    "            return ''\n"
    "        path = env.get('PATH') if isinstance(env, dict) else None\n"
    "        return shutil.which(named, path=path) or ''\n"
)


def test_the_container_runtimes_lookups_are_covered_by_its_programs(tmp_path: Path) -> None:
    """Its lookup over a test's candidates, and its guard's lookup of a program a test starts,
    which searches the host's PATH when the launch gives no environment of its own."""
    root = _tree(
        tmp_path, {"tests/container_runtime.py": _CONTAINER + _CONTAINER_ON_PATH + _CONTAINER_GUARD}
    )
    found = cap.scan(root)
    expected = {
        ("tests/container_runtime.py", "_on_path"),
        ("tests/container_runtime.py", "refusal"),
    }
    assert found.unresolved == expected
    assert set(cap.COVERED_SITES) == expected
    assert cap.probe_list(root) == ("docker", "limactl")


CONTAINER_ASK_ROWS: list[tuple[str, str, set[str] | str]] = [
    (
        "each by literal",
        'import container_runtime\ncontainer_runtime.require("orbstack", "podman")\n',
        {"orbstack", "podman"},
    ),
    (
        "a starred constant",
        'import container_runtime\nRUNTIMES = ("finch",)\n'
        "def t():\n    container_runtime.require(*RUNTIMES)\n",
        {"finch"},
    ),
    (
        "a loop over a constant",
        'import container_runtime\nRUNTIMES = ("nerdctl",)\n'
        "def t():\n    for c in RUNTIMES:\n        container_runtime.available(c)\n",
        {"nerdctl"},
    ),
    (
        "a sequence, under another name",
        'import container_runtime as cr\nREASON = cr.skip_reason(("colima",))\n',
        {"colima"},
    ),
    ("imported by name", 'from container_runtime import require as need\nneed("lima")\n', {"lima"}),
    (
        "candidates from nowhere",
        "import container_runtime\ndef t(names):\n    container_runtime.require(*names)\n",
        "t",
    ),
    (
        "a sequence from nowhere",
        "import container_runtime\n"
        "def t(names):\n    return container_runtime.skip_reason(names)\n",
        "t",
    ),
    (
        "taken as a value",
        "import container_runtime\nOK = any(map(container_runtime.available, NAMES))\n",
        "<module>",
    ),
]


def test_the_candidates_a_test_hands_the_container_runtime_are_probes(
    tmp_path: Path, subtests: Any
) -> None:
    """Its lookup is covered because each candidate is read where a test hands it over: so a
    runtime that is not one of its PROGRAMS is a probe too, and a candidate the scan cannot read
    is refused. None of these files names shutil."""
    assert len(CONTAINER_ASK_ROWS) == 8, "a container row was dropped from the table"
    for n, (name, source, expected) in enumerate(CONTAINER_ASK_ROWS):
        with row(subtests, case=name):
            assert "shutil" not in source
            root = _tree(tmp_path / str(n), {"tests/test_x.py": source})
            if isinstance(expected, set):
                assert set(cap.probe_list(root)) == expected | {"docker", "limactl"}
            else:
                assert ("tests/test_x.py", expected) in cap.scan(root).unresolved
                with pytest.raises(cap.Refused, match=f"tests/test_x.py in {expected}"):
                    cap.probe_list(root)


def test_only_the_named_lookups_of_the_container_runtime_are_covered(tmp_path: Path) -> None:
    source = _CONTAINER + _CONTAINER_ON_PATH + "def other(tool):\n    return shutil.which(tool)\n"
    root = _tree(tmp_path, {"tests/container_runtime.py": source})
    with pytest.raises(cap.Refused, match="container_runtime.py in other"):
        cap.probe_list(root)


@pytest.mark.parametrize("name", ["/usr/bin/git", "./bin/jq", ""])
def test_a_probe_that_names_no_plain_program_is_refused(tmp_path: Path, name: str) -> None:
    root = _tree(tmp_path, {"tests/test_x.py": f'import shutil\nshutil.which("{name}")\n'})
    with pytest.raises(cap.Refused, match="no plain program"):
        cap.probe_list(root)


@pytest.mark.parametrize(
    ("files", "match"),
    [
        pytest.param(
            {"tests/container_runtime.py": _CONTAINER_ON_PATH}, "PROGRAMS", id="no PROGRAMS"
        ),
        pytest.param(
            {"tests/container_runtime.py": "PROGRAMS = frozenset({NAME})\n" + _CONTAINER_ON_PATH},
            "PROGRAMS",
            id="PROGRAMS that is not all text",
        ),
        pytest.param(
            {"tests/container_runtime.py": "PROGRAMS = frozenset(())\n" + _CONTAINER_ON_PATH},
            "PROGRAMS",
            id="an empty PROGRAMS",
        ),
        pytest.param(
            {"tests/browse_chrome.py": _CHROME.replace("PERSONALCLAW_TEST_CHROME", "OTHER")},
            "CHROME_ENV",
            id="a Chrome lookup under another override",
        ),
        pytest.param(
            {"tests/test_x.py": "import shutil\ndef broken(:\n"},
            "test_x.py",
            id="a file naming shutil that does not parse",
        ),
        pytest.param(
            {"tests/browse_chrome.py": 'CHROME_ENV = "X"\n'},
            "_REVISION_DIR_PREFIXES",
            id="no Chrome lookup",
        ),
        pytest.param(
            {
                "tests/browse_chrome.py": _CHROME.replace(
                    '("*/chrome-headless-shell", "*/chrome")', "(X,)"
                )
            },
            "_BINARY_GLOBS",
            id="binary globs that are not text",
        ),
    ],
)
def test_a_tree_the_scan_cannot_read_whole_is_refused(
    tmp_path: Path, files: dict[str, str], match: str
) -> None:
    root = _tree(tmp_path, files)
    with pytest.raises(cap.Refused, match=match):
        cap.fingerprint(root, path="", environ={}, home=tmp_path / "home")


def test_no_tests_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(cap.Refused, match="tests"):
        cap.probe_list(tmp_path)


# ── a tool's identity: its path and its bytes, never a run ───────────────────────────────────


def _tool(folder: Path, name: str, body: str = "") -> Path:
    """A stand-in tool that, if it were ever run, would leave a file beside itself."""
    folder.mkdir(parents=True, exist_ok=True)
    tool = folder / name
    tool.write_text(f"#!/bin/sh\ntouch {folder / (name + '.ran')}\n{body}\n")
    tool.chmod(0o755)
    return tool


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git_only(tmp_path: Path) -> Path:
    return _tree(tmp_path, {"tests/test_x.py": 'import shutil\nshutil.which("git")\n'})


def test_a_present_tool_is_its_resolved_path_and_digest_and_never_runs(tmp_path: Path) -> None:
    root = _git_only(tmp_path)
    tool = _tool(tmp_path / "bin", "git")
    found = cap.fingerprint(root, path=str(tmp_path / "bin"), environ={}, home=tmp_path / "home")
    assert found["probe:git"] == f"{tool.resolve()}@{_digest(tool)}"
    assert found["probe:docker"] == cap.ABSENT and found["probe:limactl"] == cap.ABSENT
    assert not list((tmp_path / "bin").glob("*.ran")), "a probed tool was started"


def test_a_link_is_named_by_what_it_resolves_to(tmp_path: Path) -> None:
    root = _git_only(tmp_path)
    real = _tool(tmp_path / "real", "git-2.47")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "git").symlink_to(real)
    found = cap.fingerprint(root, path=str(tmp_path / "bin"), environ={}, home=tmp_path / "home")
    assert found["probe:git"] == f"{real.resolve()}@{_digest(real)}"


def test_a_change_past_the_first_mib_is_another_identity(tmp_path: Path) -> None:
    """The digest is of every byte, not a prefix: a build that differs only near its end is
    another tool."""
    tool = tmp_path / "git"
    tool.write_bytes(b"#!/bin/sh\n" + b"\0" * (2 << 20) + b"a")
    first = cap.identity(tool)
    tool.write_bytes(b"#!/bin/sh\n" + b"\0" * (2 << 20) + b"b")
    assert cap.identity(tool) != first


def test_a_tool_that_runs_but_cannot_be_read_is_refused(tmp_path: Path) -> None:
    """Present but unreadable is no identity, and never ``absent``: that would key a host that
    has the tool as one that does not."""
    root = _git_only(tmp_path)
    tool = _tool(tmp_path / "bin", "git")
    tool.chmod(0o111)
    try:
        if os.access(tool, os.R_OK):
            pytest.skip("this user reads every file")
        with pytest.raises(cap.Refused, match="cannot be read for its digest"):
            cap.fingerprint(root, path=str(tmp_path / "bin"), environ={}, home=tmp_path / "home")
    finally:
        tool.chmod(0o755)


def test_a_file_that_is_not_executable_is_absent(tmp_path: Path) -> None:
    root = _git_only(tmp_path)
    _tool(tmp_path / "bin", "git").chmod(0o644)
    found = cap.fingerprint(root, path=str(tmp_path / "bin"), environ={}, home=tmp_path / "home")
    assert found["probe:git"] == cap.ABSENT


def test_the_first_tool_on_the_path_is_the_one_named(tmp_path: Path) -> None:
    root = _git_only(tmp_path)
    first = _tool(tmp_path / "a", "git", "# one")
    _tool(tmp_path / "b", "git", "# two")
    path = os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
    found = cap.fingerprint(root, path=path, environ={}, home=tmp_path / "home")
    assert found["probe:git"] == f"{first.resolve()}@{_digest(first)}"


def test_the_callers_own_path_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _git_only(tmp_path)
    _tool(tmp_path / "bin", "git")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    found = cap.fingerprint(root, path="", environ={}, home=tmp_path / "home")
    assert found["probe:git"] == cap.ABSENT


# ── the skip switches ────────────────────────────────────────────────────────────────────────


def test_the_switches_are_the_six_that_change_what_runs() -> None:
    assert cap.SWITCHES == (
        "PERSONALCLAW_TEST_CONTAINER_RUNTIME",
        "PERSONALCLAW_REQUIRE_INSTALL_PROOF",
        "PERSONALCLAW_REQUIRE_BROWSE_PROOF",
        "PERSONALCLAW_REQUIRE_NPM_TREE",
        "PERSONALCLAW_REQUIRE_FFMPEG",
        "PERSONALCLAW_TEST_CHROME",
    )


def test_each_switch_is_read_from_the_environment_given(tmp_path: Path, subtests: Any) -> None:
    root = _tree(tmp_path, {})
    home = tmp_path / "home"
    unset = cap.fingerprint(root, path="", environ={}, home=home)
    assert {k: v for k, v in unset.items() if k.startswith("switch:")} == {
        f"switch:{name}": cap.UNSET for name in cap.SWITCHES
    }
    for name in cap.SWITCHES:
        with row(subtests, switch=name):
            # "unset" too: the prefix keeps a switch set to that text apart from one not set.
            for value in ("1", "0", "", "unset"):
                found = cap.fingerprint(root, path="", environ={name: value}, home=home)
                assert found[f"switch:{name}"] == f"set:{value}"
                assert found != unset


def test_another_variable_is_no_input(tmp_path: Path) -> None:
    root = _tree(tmp_path, {})
    home = tmp_path / "home"
    assert cap.fingerprint(root, path="", environ={"HOME": "/x", "CI": "true"}, home=home) == (
        cap.fingerprint(root, path="", environ={}, home=home)
    )


# ── the browse proofs' browser ───────────────────────────────────────────────────────────────


def _browser(root: Path, revision: str, binary: str) -> Path:
    path = root / revision / "arch" / binary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"chrome " + revision.encode() + binary.encode())
    path.chmod(0o755)
    return path


def _chrome(tmp_path: Path, environ: dict[str, str], platform: str = "linux") -> str:
    root = _tree(tmp_path / "repo-tree", {})
    found = cap.fingerprint(
        root, path="", environ=environ, home=tmp_path / "home", platform=platform
    )
    return found["chrome"]


def test_the_browser_is_found_where_the_browse_proofs_look(
    tmp_path: Path, subtests: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The caller's own HOME is another folder: a `~` is the home given, the job's.
    monkeypatch.setenv("HOME", str(tmp_path / "caller-home"))
    cache = tmp_path / "home" / ".cache" / "ms-playwright"
    mac = tmp_path / "home" / "Library" / "Caches" / "ms-playwright"
    pinned = tmp_path / "pw"
    headless = _browser(cache, "chromium_headless_shell-1200", "chrome-headless-shell")
    _browser(cache, "chromium-1200", "chrome")
    old = _browser(pinned, "chromium-1100", "chrome")
    new = _browser(pinned, "chromium-1300", "chrome")
    on_mac = _browser(mac, "chromium-1300", "chrome")
    under_home = _browser(tmp_path / "home" / "pw", "chromium-1400", "chrome")
    cached = _browser(tmp_path / "home" / "xdg" / "ms-playwright", "chromium-1500", "chrome")
    for decoy in ("pw", "xdg/ms-playwright"):
        _browser(tmp_path / "caller-home" / decoy, "chromium-1600", "chrome")
    override = _tool(tmp_path / "own", "my-chrome")
    rows: list[tuple[str, dict[str, str], str, Path | None]] = [
        ("the headless shell before the full browser", {}, "linux", headless),
        (
            "Playwright's own root first, newest revision first",
            {"PLAYWRIGHT_BROWSERS_PATH": str(pinned)},
            "linux",
            new,
        ),
        (
            "a falsey PLAYWRIGHT_BROWSERS_PATH is no root",
            {"PLAYWRIGHT_BROWSERS_PATH": "0"},
            "linux",
            headless,
        ),
        (
            "XDG_CACHE_HOME moves the Linux root",
            {"XDG_CACHE_HOME": str(tmp_path / "none")},
            "linux",
            None,
        ),
        ("macOS's own cache", {}, "darwin", on_mac),
        ("the override wins", {cap.CHROME_SWITCH: str(override)}, "linux", override),
        (
            "an override that cannot run falls through",
            {cap.CHROME_SWITCH: str(tmp_path / "gone")},
            "linux",
            headless,
        ),
        (
            "a ~ in PLAYWRIGHT_BROWSERS_PATH is the home given",
            {"PLAYWRIGHT_BROWSERS_PATH": "~/pw"},
            "linux",
            under_home,
        ),
        ("a ~ in XDG_CACHE_HOME is the home given", {"XDG_CACHE_HOME": "~/xdg"}, "linux", cached),
    ]
    assert len(rows) == 9, "a browser row was dropped from the table"
    assert old.exists()
    for name, environ, platform, expected in rows:
        with row(subtests, case=name):
            got = _chrome(tmp_path, environ, platform)
            want = cap.ABSENT if expected is None else f"{expected.resolve()}@{_digest(expected)}"
            assert got == want
    assert not list((tmp_path / "own").glob("*.ran")), "the browser was started"


# ── into the key ─────────────────────────────────────────────────────────────────────────────


def _key(capabilities: dict[str, str]) -> str:
    inputs = vs.KeyInputs(
        job_id="python-suite",
        entry_text="[[job]]",
        catalogue_version="0" * 64,
        argv=(("pytest",),),
        selection_digest="1" * 64,
        tree_oid="2" * 40,
        os="linux",
        arch="x86_64",
        capabilities=capabilities,
    )
    return vs.key(inputs)


def test_a_tool_that_appears_changes_or_goes_is_another_key(tmp_path: Path) -> None:
    root = _git_only(tmp_path)
    home, bin_ = tmp_path / "home", tmp_path / "bin"

    def now() -> str:
        return _key(cap.fingerprint(root, path=str(bin_), environ={}, home=home))

    without = now()
    assert now() == without, "the same host gives the same key"
    tool = _tool(bin_, "git")
    present = now()
    tool.write_text(tool.read_text() + "# another build\n")
    changed = now()
    tool.unlink()
    assert len({without, present, changed}) == 3
    assert now() == without


def test_a_home_that_cannot_be_found_is_refused(tmp_path: Path) -> None:
    """``~user`` is that user's home, as the browse proofs expand it; one with no home there is
    no root the fingerprint can name, and the browse proofs fail on it too."""
    root = _tree(tmp_path, {})
    environ = {"PLAYWRIGHT_BROWSERS_PATH": "~no-such-user-of-this-host/pw"}
    with pytest.raises(cap.Refused, match="no-such-user-of-this-host"):
        cap.fingerprint(root, path="", environ=environ, home=tmp_path / "home", platform="linux")


def test_another_path_or_a_new_probe_site_is_another_key(tmp_path: Path) -> None:
    """Same tree, another PATH whose git is another build; then the same PATH, and a test that
    probes one more tool, absent here."""
    root = _git_only(tmp_path)
    home = tmp_path / "home"
    _tool(tmp_path / "a", "git")
    _tool(tmp_path / "b", "git", "# another build")
    before = cap.fingerprint(root, path=str(tmp_path / "a"), environ={}, home=home)
    elsewhere = cap.fingerprint(root, path=str(tmp_path / "b"), environ={}, home=home)
    assert _key(elsewhere) != _key(before)
    (root / "tests" / "test_new.py").write_text('import shutil\nshutil.which("tmux")\n')
    after = cap.fingerprint(root, path=str(tmp_path / "a"), environ={}, home=home)
    assert after["probe:tmux"] == cap.ABSENT
    assert _key(after) != _key(before)


def test_the_browser_lookup_finds_what_browse_chrome_finds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fingerprint's copy of the browse proofs' lookup, read from their constants, against
    the lookup itself, on the same planted Playwright roots."""
    import browse_chrome

    repo = Path(browse_chrome.__file__).resolve().parents[1]
    lookup = cap.ChromeLookup.read(repo)
    home = tmp_path / "home"
    pinned = tmp_path / "pw"
    for root in (home / ".cache" / "ms-playwright", home / "Library" / "Caches" / "ms-playwright"):
        _browser(root, "chromium-1200", "chrome")
        _browser(root, "chromium_headless_shell-1100", "chrome-headless-shell")
    _browser(pinned, "chromium-1300", "chrome")
    # A newer revision whose binary cannot run, and an override that cannot: both passed over.
    _browser(pinned, "chromium-1400", "chrome").chmod(0o644)
    _browser(home / "pw", "chromium-1500", "chrome")
    override = _tool(tmp_path / "own", "my-chrome")
    plain = _tool(tmp_path / "own", "plain-chrome")
    plain.chmod(0o644)
    monkeypatch.setattr(browse_chrome.pathlib.Path, "home", classmethod(lambda cls: home))
    # The job's HOME, which browse_chrome expands a `~` against.
    monkeypatch.setenv("HOME", str(home))
    envs: list[dict[str, str]] = [
        {},
        {"PLAYWRIGHT_BROWSERS_PATH": str(pinned)},
        {"PLAYWRIGHT_BROWSERS_PATH": "0"},
        {"PLAYWRIGHT_BROWSERS_PATH": "~/pw"},
        {"XDG_CACHE_HOME": "~/.cache"},
        {browse_chrome.CHROME_ENV: str(override)},
        {browse_chrome.CHROME_ENV: str(plain), "PLAYWRIGHT_BROWSERS_PATH": str(pinned)},
    ]
    for platform in ("linux", "darwin"):
        monkeypatch.setattr(browse_chrome.sys, "platform", platform)
        for env in envs:
            for name in ("PLAYWRIGHT_BROWSERS_PATH", "XDG_CACHE_HOME", browse_chrome.CHROME_ENV):
                monkeypatch.delenv(name, raising=False)
            for name, value in env.items():
                monkeypatch.setenv(name, value)
            roots = lookup.roots(env, home, platform)
            assert roots == browse_chrome.browsers_roots(), (platform, env)
            assert lookup.find(roots, env) == browse_chrome.find_chrome(), (platform, env)
    assert not list((tmp_path / "own").glob("*.ran")), "the browser was started"
