"""🪤 The rail: no committed dev tool can reach the install by default.

The trees a developer runs tools from by hand — ``scripts/``, ``harness/`` and the docs capture
tooling (the showcase, demo and brand-card scripts with the runbooks that say how to run them) —
are held to three things:

* **no literal real-home path.** Nothing spells the default home's folder, whether as a path
  (``~/.personalclaw``, ``$HOME/.personalclaw``), joined onto the user's home, or as the product's
  ``CONFIG_DIR_NAME``. A tool that must know the default home asks ``config.loader``, and one that
  acts on a home asks ``harness/named_home.py``, which refuses the default home. Prose included:
  a line that tells a reader to run a tool on the default home is the defect too, so prose says
  "the default home".
* **no ``:10000`` default.** Port 10000 is the install's own. Nothing names it as a host and port,
  a ``--port`` or a ``PORT=`` assignment, nor reaches the product's default-port constants. A
  gateway is found from the record it keeps in the home it was named for, never from a port.
* **no home default that isn't scratch.** Where ``PERSONALCLAW_HOME`` is read with a fallback for
  when it is unset, or a ``--home`` option has a default, the fallback is empty (nothing named,
  which the helper refuses) or a fresh temporary folder.

What this cannot see, stated rather than implied: a tool that never mentions the home at all and
simply calls product code, which then resolves the default home. That is what the helper is for,
and ``tests/test_dev_tools_act_only_on_a_named_scratch_home.py`` runs each tool with nothing named.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

#: The trees held to the rule: directories, so a new tool in one is covered the day it lands.
TREES = ("scripts/", "harness/", "docs/screenshots/", "docs/demo/", "docs/brand/")

#: Files a check must see, so a scan that stopped reading a tree fails here instead of passing.
KNOWN_TOOLS = (
    "harness/named_home.py",
    "harness/worktree_bench.py",
    "scripts/seed_tasks.py",
    "scripts/memory_validate.py",
    "scripts/smoke_unified_loop_classify.py",
    "scripts/lib/named_home.mjs",
    "scripts/ci_smoke_run.sh",
    "docs/screenshots/capture.mjs",
    "docs/screenshots/CAPTURE.md",
    "docs/demo/capture_demo.mjs",
    "docs/brand/social_preview.mjs",
)

_HOME_LITERAL = re.compile(r"(?<![\w\]\-.])\.personalclaw(?![\w-])|\bCONFIG_DIR_NAME\b")
_INSTALL_PORT = re.compile(
    r":10000(?!\d)|--port[= ]10000(?!\d)|PORT=10000(?!\d)|\b_DEFAULT_PORT\b|\bDASHBOARD_PORT\b"
)
_SHELL_DEFAULT = re.compile(r"\$\{PERSONALCLAW_HOME:?[-=]([^}]*)\}")
_JS_DEFAULT = re.compile(r"process\.env\.PERSONALCLAW_HOME\s*(?:\|\||\?\?)\s*([^\n;]+)")
#: A fallback that is a FRESH folder: what makes a default scratch rather than someone's home.
_SCRATCH = ("mkdtemp", "TemporaryDirectory", "mktemp", "tmpdir()")
_ENV = "PERSONALCLAW_HOME"


def _scratch_text(text: str) -> bool:
    return any(maker in text for maker in _SCRATCH)


def _is_scratch_or_empty(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and node.value in ("", None):
        return True
    return isinstance(node, ast.Call) and _scratch_text(ast.unparse(node.func))


def _reads_home(node: ast.AST) -> bool:
    """``os.environ.get``/``os.getenv``/``os.environ[...]`` of ``PERSONALCLAW_HOME``."""
    if isinstance(node, ast.Call) and node.args:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        first = node.args[0]
        return name in ("get", "getenv") and isinstance(first, ast.Constant) and first.value == _ENV
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == _ENV
    return False


def _python_home_defaults(source: str) -> list[tuple[int, str]]:
    """Where a Python tool substitutes a home that is not scratch for an unset one."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            fallback = None
            if _reads_home(node) and len(node.args) > 1:
                fallback = node.args[1]
            elif (
                name == "setdefault" and node.args and getattr(node.args[0], "value", None) == _ENV
            ):
                fallback = node.args[1] if len(node.args) > 1 else ast.Constant(None)
            elif name == "add_argument" and any(
                isinstance(a, ast.Constant) and a.value == "--home" for a in node.args
            ):
                fallback = next((k.value for k in node.keywords if k.arg == "default"), None)
            if fallback is not None and not _is_scratch_or_empty(fallback):
                found.append((node.lineno, f"a home default: {ast.unparse(fallback)}"))
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if any(_reads_home(v) for v in node.values[:-1]):
                last = node.values[-1]
                if not _is_scratch_or_empty(last):
                    found.append((node.lineno, f"a home default: {ast.unparse(last)}"))
    return found


def violations(path: str, text: str) -> list[str]:
    """Every way *text*, the file at *path*, reaches the install by default: ``path:line why``."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if _HOME_LITERAL.search(line):
            found.append((number, "names the default home"))
        if _INSTALL_PORT.search(line):
            found.append((number, "names the install's port"))
        for match in _SHELL_DEFAULT.finditer(line):
            if match.group(1) and not _scratch_text(match.group(1)):
                found.append((number, f"a home default: {match.group(1)}"))
        for match in _JS_DEFAULT.finditer(line):
            if not _scratch_text(match.group(1)):
                found.append((number, f"a home default: {match.group(1).strip()}"))
    if path.endswith(".py"):
        found.extend(_python_home_defaults(text))
    return [f"{path}:{number}  {why}" for number, why in sorted(set(found))]


def _scope() -> dict[str, str]:
    """Every file in the trees that git tracks or would add (a new tool is held to the rule before
    it is committed), except binaries, which no tool runs from."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *TREES],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    files: dict[str, str] = {}
    for rel in sorted(set(listed)):
        try:
            raw = (REPO / rel).read_bytes()
        except FileNotFoundError:  # deleted in the working tree, not yet in the index
            continue
        if b"\0" in raw:
            continue
        files[rel] = raw.decode("utf-8", errors="replace")
    return files


def test_no_committed_dev_tool_reaches_the_install_by_default():
    offenders = [v for rel, text in sorted(_scope().items()) for v in violations(rel, text)]
    assert offenders == [], (
        "these reach the install's own data by default:\n  "
        + "\n  ".join(offenders)
        + "\n\nA tool names the home it acts on through harness/named_home.py (JavaScript: "
        "scripts/lib/named_home.mjs), which refuses the default home and finds that home's "
        "gateway from the record it keeps there. Prose says 'the default home', not its path."
    )


def test_the_scan_reads_every_tree_it_holds():
    """🪤 Vacuity floor: a scan that stopped listing a tree would read as a clean tree forever."""
    scope = _scope()
    assert [t for t in KNOWN_TOOLS if t not in scope] == []
    assert all(any(rel.startswith(tree) for rel in scope) for tree in TREES)


@pytest.mark.parametrize(
    "path, text",
    [
        ("scripts/x.py", 'DB = os.path.expanduser("~/.personalclaw/memory.db")'),
        ("scripts/x.py", 'home = Path.home() / ".personalclaw"'),
        ("scripts/x.py", "home = Path.home() / CONFIG_DIR_NAME"),
        ("scripts/x.py", '"""Run it as PERSONALCLAW_HOME=~/.personalclaw python x.py"""'),
        ("scripts/x.sh", 'HOME_DIR="$HOME/.personalclaw"'),
        ("scripts/x.mjs", "const home = path.join(os.homedir(), '.personalclaw')"),
        ("docs/screenshots/X.md", "never run it on your real `~/.personalclaw`"),
        ("scripts/x.py", 'BASE = "http://127.0.0.1:10000"'),
        (
            "docs/screenshots/x.mjs",
            "const BASE = process.env.PCLAW_URL || 'http://localhost:10000';",
        ),
        ("docs/screenshots/X.md", "personalclaw gateway --port 10000 --no-open"),
        ("scripts/x.sh", "PERSONALCLAW_PORT=10000 personalclaw token"),
        ("scripts/x.py", "from personalclaw.config.loader import DASHBOARD_PORT"),
        ("scripts/x.py", 'home = os.environ.get("PERSONALCLAW_HOME", ".dev-home")'),
        ("scripts/x.py", 'home = os.getenv("PERSONALCLAW_HOME", str(cwd / "home"))'),
        ("scripts/x.py", 'home = args.home or os.environ.get("PERSONALCLAW_HOME") or ".dev-home"'),
        ("scripts/x.py", 'os.environ.setdefault("PERSONALCLAW_HOME", "/srv/pc")'),
        ("scripts/x.py", 'parser.add_argument("--home", default="/srv/pc")'),
        ("scripts/x.sh", 'home="${PERSONALCLAW_HOME:-$PWD/.dev-home}"'),
        ("scripts/x.mjs", "const home = process.env.PERSONALCLAW_HOME ?? '/srv/pc'"),
    ],
)
def test_the_rail_catches_a_planted_violation(path, text):
    assert violations(path, text), f"the rail missed: {text!r}"


@pytest.mark.parametrize(
    "path, text",
    [
        ("scripts/x.py", "home = named_home.scratch_home(args.home)"),
        (
            "scripts/x.py",
            'home = Path(args.home or os.environ.get("PERSONALCLAW_HOME") or tempfile.mkdtemp())',
        ),
        ("scripts/x.py", 'label = os.environ.get("PERSONALCLAW_HOME", "")'),
        ("scripts/x.py", 'os.environ.pop("PERSONALCLAW_HOME", None)'),
        ("scripts/x.py", 'env["PERSONALCLAW_HOME"] = str(home)'),
        ("scripts/x.py", 'parser.add_argument("--home", default=None)'),
        ("scripts/x.py", 'tasks = get("/api/tasks?limit=10000")'),
        (
            "harness/x.py",
            'help="Files in the synthesized repo (default 10000; ignored with --repo)."',
        ),
        ("scripts/x.mjs", "if (port === 10000) throw new Error('refusing to bind port 10000')"),
        ("scripts/x.sh", 'if [[ -z "${PERSONALCLAW_HOME:-}" ]]; then'),
        ("scripts/x.sh", 'PERSONALCLAW_HOME="${PERSONALCLAW_HOME:-$(mktemp -d)}"'),
        (
            "scripts/x.mjs",
            "const home = process.env.PERSONALCLAW_HOME || mkdtempSync(path.join(tmp, 'pc-'))",
        ),
        ("scripts/x.sh", 'EXPECTED_IDENTIFIER="io.personalclaw.app"'),
        ("scripts/x.py", '"""[tool.setuptools.package-data].personalclaw, verbatim"""'),
        (
            "docs/screenshots/X.md",
            "Capture against a throwaway home: the script refuses the default home.",
        ),
    ],
)
def test_the_rail_passes_what_reaches_no_install(path, text):
    assert violations(path, text) == [], f"not a default to the install, but flagged: {text!r}"
