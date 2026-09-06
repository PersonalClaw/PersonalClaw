"""SPIKE — PROPOSAL, NOT A LANDING. Scope a DANGEROUS match by what it can reach.

The question this exists to answer
----------------------------------
``SkillScanner``'s DANGEROUS band matches a *string shape* and is blind to whether that
string is ever executed. So a bundle whose test fixture contains a literal attack string
— the string that proves its input validation refuses the attack — is judged identically
to a bundle that runs that string. DANGEROUS is terminal and non-consentable, so the
fixture permanently blocks the install.

This module computes an independent, conservative answer to a narrower question: *can
this matched literal be executed at all, anywhere in this bundle?* When the answer is a
provable no, it re-scores that one finding from DANGEROUS to WARNING and records why.

What it deliberately does NOT do
--------------------------------
* It does not import, patch, wrap, or alter ``personalclaw.supply_chain``. The shipped
  scanner is untouched; there is no default-on path and nothing to disable.
* It never raises a severity, never removes a finding, and never lowers a verdict below
  WARNING. Disclosure is preserved in full: the user still sees the finding, the rule id,
  the file and the evidence, and still has to consent.
* It refuses to answer rather than guess. Every predicate below is default-deny: an
  unparseable file, an import graph that runtime can bend, or an execution site whose
  program name is not statically known all mean "no downgrade".

The rule, and why each clause is load-bearing
---------------------------------------------
A DANGEROUS finding is re-scored iff ALL of the following hold. Each clause closes one
route from "a string sits in a file" to "a command ran".

L1  LITERAL, NOT CODE. The file parses as Python and the matched span lies wholly inside
    a ``str`` constant. A shell script has no data positions — its text *is* the program —
    so a ``.sh`` finding is never eligible. This is the clause that makes the whole idea
    honest: it is decided on the AST, not on a filename, so calling a file
    ``test_evil.py`` buys an attacker exactly nothing.

L2  NO LOCAL EXECUTION PATH. The file itself cannot hand a string to an interpreter: no
    ``os.system``/``popen``/``exec*``/``spawn*``/``pty``, no ``eval``/``exec``/``compile``,
    no ``subprocess`` call whose ``shell`` is anything but a literal ``False``, and every
    spawn site's argv[0] is a literal program name that is neither a shell nor an
    interpreter nor a path (so it cannot be a script shipped in the bundle).

L3  NO EXPORT. Nothing else in the bundle can lift the literal out: no other module
    imports it, no other file mentions its filename or module stem as a string (which is
    how a payload would be read rather than imported), and ``app.json`` does not name it
    as an entry point.

L4  THE GRAPH IS TRUSTWORTHY. L3 is a static claim, so it is void if the bundle can
    rewrite its own import graph at runtime. Any ``eval``/``exec``/``compile``/
    ``__import__``/``importlib``/``runpy``/``pickle``/``marshal``/``ctypes``, any
    ``getattr`` with a computed name, any ``import *``, or any ``sys.path`` mutation
    ANYWHERE in the bundle revokes the downgrade for the whole bundle.

L5  IMPORTING IT IS A NO-OP. Unreachable is not the same as never-imported — a test
    runner, or a curious user, may import the module. Its top level must therefore
    contain no call outside a small pure allowlist, so the payload cannot fire on import.

Residual risk a reviewer must weigh (stated, not hidden)
--------------------------------------------------------
* A bundle can ship a module that is pure inert data, referenced by nothing, and get its
  literal disclosed-and-consentable instead of refused. That is a real reduction in the
  floor for that one shape. Measured counterweight: the shipped DANGEROUS band already
  scores ``"rm -" + "rf /"`` as CLEAN and ``os.system("rm -rf /")`` as WARNING, so the
  band never held against an attacker who was trying. See the spike's test suite.
* L2 assumes a pinned, non-shell, non-interpreter argv[0] cannot be talked into running a
  shell by its arguments. ``git -c core.pager=…`` is a counterexample in principle. The
  tighter alternative — require every argv element to be a literal — is one line here and
  is what a reviewer should consider if that residual is judged too wide.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# ── the sink vocabulary ───────────────────────────────────────────────────────

#: argv[0] values that make the spawned process an interpreter of its arguments. A
#: string handed to any of these becomes code, so a file containing such a site can
#: execute any literal it holds.
_SHELL_ARGV0 = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "fish",
        "ash",
        "csh",
        "tcsh",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "env",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "deno",
        "bun",
        "php",
        "awk",
        "sed",
        "osascript",
        "xargs",
        "eval",
    }
)

#: Attribute names that execute or delete without going through ``subprocess``.
_EXEC_ATTRS = frozenset(
    {
        "system",
        "popen",
        "execl",
        "execle",
        "execlp",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnv",
        "spawnve",
        "spawnvp",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
        "spawn",
    }
)

#: Builtins that turn a string into code, or that make a static name resolution a lie.
_DYNAMIC_BUILTINS = frozenset({"eval", "exec", "compile", "__import__"})

#: Modules whose mere presence means the import graph or a byte string can become code.
_DYNAMIC_MODULES = frozenset(
    {"importlib", "runpy", "pickle", "marshal", "dill", "ctypes", "cffi", "imp"}
)

#: Callables allowed at module level under L5 — pure, no execution, no I/O beyond
#: resolving where the file is. Anything else at module level revokes the downgrade.
_INERT_TOP_LEVEL_CALLS = frozenset(
    {
        "Path",
        "Path(__file__).resolve",
        "os.path.dirname",
        "os.path.abspath",
        "os.path.join",
        "os.environ.get",
        "os.getenv",
        "re.compile",
        "frozenset",
        "set",
        "dict",
        "list",
        "tuple",
        "len",
        "sorted",
        "range",
        "logging.getLogger",
        "shutil.which",
        "pytest.mark.skipif",
        "pytest.importorskip",
    }
)

#: Function-call prefixes that spawn a process. ``asyncio.create_subprocess_shell`` is
#: separately fatal (it takes a command *string*), which the shell check below catches.
_SPAWN_FUNCS = ("subprocess.", "asyncio.create_subprocess_")

#: ``subprocess`` names that are exceptions, not spawns — referencing them is inert.
_SUBPROCESS_NON_SPAWN = frozenset(
    {"TimeoutExpired", "CalledProcessError", "SubprocessError", "PIPE", "STDOUT", "DEVNULL"}
)


# ── findings ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rescope:
    """One re-scoring decision, with the sentence a reviewer or a consent surface needs."""

    path: str
    rule: str
    downgraded: bool
    reason: str


# ── AST facts about one file ──────────────────────────────────────────────────


@dataclass
class _FileFacts:
    parsed: bool
    str_spans: list[tuple[int, int]]
    imports: set[str]
    string_literals: set[str]
    star_import: bool
    dynamic: set[str]  # L4 evidence: names that make the graph untrustworthy
    executes: set[str]  # L2 evidence: sites that could run a string
    top_level_calls: set[str]


def _offset_table(text: str) -> list[int]:
    """Line-start offsets, so an AST (lineno, col) becomes a flat text offset."""
    starts = [0]
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _argv0_of(call: ast.Call) -> tuple[bool, str | None]:
    """(pinned, program) for a spawn site's argv[0].

    ``pinned`` is False whenever the program name is not a plain string literal — which
    is the default-deny answer, because an argv built at runtime can be anything.
    """
    if not call.args:
        return False, None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        # A bare command STRING (not a list) is only ever run through a shell.
        return False, first.value
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return True, head.value
    return False, None


def _analyse(path: Path) -> _FileFacts:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _FileFacts(False, [], set(), set(), False, {"unparseable"}, {"unparseable"}, set())

    starts = _offset_table(text)

    def off(lineno: int, col: int) -> int:
        return starts[lineno - 1] + col

    facts = _FileFacts(True, [], set(), set(), False, set(), set(), set())

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            facts.string_literals.add(node.value)
            if node.end_lineno is not None:
                facts.str_spans.append(
                    (off(node.lineno, node.col_offset), off(node.end_lineno, node.end_col_offset))
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                facts.imports.add(root)
                facts.imports.add(alias.name)
                if root in _DYNAMIC_MODULES:
                    facts.dynamic.add(f"imports {root}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            facts.imports.add(root)
            for alias in node.names:
                facts.imports.add(alias.name)
                if alias.name == "*":
                    facts.star_import = True
                    facts.dynamic.add("star import")
            if root in _DYNAMIC_MODULES:
                facts.dynamic.add(f"imports {root}")
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC_BUILTINS:
            facts.dynamic.add(f"{node.id}()")
            facts.executes.add(f"{node.id}()")
        elif isinstance(node, ast.Attribute):
            if node.attr in _EXEC_ATTRS:
                facts.executes.add(f".{node.attr}")
            if node.attr == "path" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                # `sys.path.insert(...)` / `sys.path += …` makes any import resolution a guess.
                facts.dynamic.add("sys.path mutation")

        if isinstance(node, ast.Call):
            _walk_call(node, facts)

    for stmt in tree.body:
        if isinstance(
            stmt,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom),
        ):
            continue
        for inner in ast.walk(stmt):
            if isinstance(inner, ast.Call):
                facts.top_level_calls.add(ast.unparse(inner.func))

    return facts


def _walk_call(node: ast.Call, facts: _FileFacts) -> None:
    name = ast.unparse(node.func)
    if name == "getattr" and (len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)):
        facts.dynamic.add("getattr with a computed name")
    if name.startswith("subprocess.") and name.split(".")[-1] in _SUBPROCESS_NON_SPAWN:
        return
    if not name.startswith(_SPAWN_FUNCS):
        return
    if name.endswith("create_subprocess_shell"):
        facts.executes.add(name)
        return
    shell = next((kw.value for kw in node.keywords if kw.arg == "shell"), None)
    if shell is not None and not (isinstance(shell, ast.Constant) and shell.value is False):
        facts.executes.add(f"{name}(shell=…)")
        return
    pinned, program = _argv0_of(node)
    if not pinned:
        facts.executes.add(f"{name} with an argv the source does not pin")
        return
    leaf = (program or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "/" in (program or "") or leaf in _SHELL_ARGV0 or leaf.endswith((".sh", ".bash", ".py")):
        facts.executes.add(f"{name} spawning {program!r}")


# ── the bundle-level predicate ────────────────────────────────────────────────


def _bundle_python_files(bundle: Path) -> list[Path]:
    skip = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".tox"}
    return [
        p
        for p in sorted(bundle.rglob("*.py"))
        if p.is_file() and not any(part in skip for part in p.parts)
    ]


def _manifest_names(bundle: Path) -> set[str]:
    """Every module-ish token ``app.json`` names, so a declared entry point is not
    mistaken for an unreferenced file just because no ``import`` statement mentions it."""
    manifest = bundle / "app.json"
    if not manifest.is_file():
        return set()
    try:
        data: Any = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"*"}  # unreadable manifest → assume every file is named (default-deny)
    out: set[str] = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]*", json.dumps(data)):
        out.add(token)
        out.add(token.split(":")[0])
        out.add(Path(token).stem)
    return out


#: Non-Python surfaces that can *load or run* a file, and so count as a reference under
#: L3. Prose is excluded on purpose: a README saying "run pytest test_provider.py" is
#: documentation, not plumbing, and counting it would make L3 unsatisfiable for every
#: bundle that documents its own test command — which is every well-documented bundle.
_LOADER_SUFFIXES = frozenset(
    {
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".cmd",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".rb",
        ".pl",
        ".php",
        ".lua",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".cfg",
        ".ini",
        ".mk",
    }
)
_LOADER_NAMES = frozenset({"Makefile", "makefile", "Dockerfile", "Procfile", "justfile"})


def _loader_text(bundle: Path) -> str:
    """Concatenated text of the bundle's non-Python files that could load or run code, so
    a payload module referenced from a shell script or a config still counts as reached."""
    chunks = []
    skip = {"__pycache__", ".venv", "venv", "node_modules", ".git", ".tox"}
    for p in sorted(bundle.rglob("*")):
        if not p.is_file() or p.suffix == ".py":
            continue
        if p.suffix.lower() not in _LOADER_SUFFIXES and p.name not in _LOADER_NAMES:
            continue
        if any(part in skip for part in p.parts):
            continue
        try:
            if p.stat().st_size > 512 * 1024:
                continue
            chunks.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


@dataclass
class BundleScope:
    """The per-bundle analysis, computed once and queried per finding."""

    bundle: Path
    facts: dict[str, _FileFacts]
    graph_untrustworthy: str | None

    @classmethod
    def build(cls, bundle: Path) -> "BundleScope":
        bundle = Path(bundle)
        facts = {str(p.relative_to(bundle)): _analyse(p) for p in _bundle_python_files(bundle)}
        reason = None
        for rel, f in sorted(facts.items()):
            if f.dynamic:
                reason = f"{rel}: {sorted(f.dynamic)[0]}"
                break
        return cls(bundle=bundle, facts=facts, graph_untrustworthy=reason)

    def _referenced_elsewhere(self, rel: str) -> str | None:
        stem = Path(rel).stem
        for other, f in self.facts.items():
            if other == rel:
                continue
            if stem in f.imports:
                return f"{other} imports it"
            if (
                stem in f.string_literals
                or rel in f.string_literals
                or Path(rel).name in f.string_literals
            ):
                return f"{other} names it in a string"
        if stem in _manifest_names(self.bundle) or "*" in _manifest_names(self.bundle):
            return "app.json names it"
        blob = _loader_text(self.bundle)
        if Path(rel).name in blob or f"{stem} " in blob:
            return "a script or config in the bundle names it"
        return None

    def rescope(self, *, path: str, rule: str, match_start: int, match_end: int) -> Rescope:
        """Decide L1-L5 for one DANGEROUS finding. Returns the decision AND the reason,
        because a downgrade a reviewer cannot read the justification for is not reviewable."""
        rel = str(Path(path))
        facts = self.facts.get(rel)

        if not path.endswith(".py"):
            return Rescope(path, rule, False, "not Python — a script's text is its program (L1)")
        if facts is None or not facts.parsed:
            return Rescope(path, rule, False, "file does not parse as Python (L1)")
        if not any(s <= match_start and match_end <= e for s, e in facts.str_spans):
            return Rescope(path, rule, False, "match is code, not a string literal (L1)")
        if facts.executes:
            return Rescope(
                path, rule, False, f"file can execute a string: {sorted(facts.executes)[0]} (L2)"
            )
        exported = self._referenced_elsewhere(rel)
        if exported:
            return Rescope(path, rule, False, f"literal is reachable — {exported} (L3)")
        if self.graph_untrustworthy:
            return Rescope(
                path,
                rule,
                False,
                f"bundle can rewrite its own graph — {self.graph_untrustworthy} (L4)",
            )
        impure = sorted(facts.top_level_calls - _INERT_TOP_LEVEL_CALLS)
        if impure:
            return Rescope(path, rule, False, f"module runs {impure[0]} on import (L5)")
        return Rescope(
            path,
            rule,
            True,
            "inert literal: not code, this file cannot execute a string, nothing in the "
            "bundle reaches it, and importing it runs nothing (L1-L5)",
        )


# ── the reviewable output: a rescoped copy of a report ────────────────────────

#: A downgrade never goes below this. WARNING keeps the finding on the consent surface
#: with its rule, path and evidence intact — the user is still told, and still consents.
_FLOOR = "warning"


def _match_span(text: str, rule: str) -> tuple[int, int]:
    """Re-derive the matched span by re-running the SHIPPED rule against the file.

    The finding's ``evidence`` is a human-shaped window (leading context, an ellipsis on
    every cut), so searching for it back in the source is guesswork. Re-running the rule
    that produced the finding is exact, and it keeps the spike honest: it asks about the
    same bytes the scanner objected to, not about a paraphrase of them.

    ``exfil_sensitive_path`` is derived from two regexes over comment-stripped text; the
    span that matters for "is this a literal" is the credential path itself, which is
    located in the RAW text here so the offset lines up with the AST.
    """
    from personalclaw.supply_chain import _DANGEROUS_SCRIPT, _SENSITIVE_RE

    if rule == "exfil_sensitive_path":
        hit = _SENSITIVE_RE.search(text)
        return (hit.start(), hit.end()) if hit else (-1, -1)
    for name, pattern in _DANGEROUS_SCRIPT:
        if name != rule:
            continue
        hit = pattern.search(text)
        return (hit.start(), hit.end()) if hit else (-1, -1)
    return (-1, -1)


def rescope_report(report: Any, bundle: Path, *, text_of: Any = None) -> tuple[Any, list[Rescope]]:
    """Return ``(new_report, decisions)`` — a COPY of ``report`` with eligible DANGEROUS
    findings re-scored to WARNING, plus the reason for every decision.

    ``report`` is left untouched. Nothing in ``src/`` calls this; a reviewer does, and a
    reviewer can therefore diff the two reports side by side.

    ``text_of`` is an optional ``(bundle, rel) -> str`` reader, injected only so a caller
    can drive a synthetic tree; it defaults to reading the file.
    """
    from personalclaw.supply_chain import ScanReport, Verdict  # local: one-way edge

    read = text_of or (lambda b, rel: (Path(b) / rel).read_text(encoding="utf-8", errors="replace"))
    scope = BundleScope.build(Path(bundle))
    decisions: list[Rescope] = []
    new_findings = []

    for finding in report.findings:
        if finding.severity is not Verdict.DANGEROUS or finding.surface != "script":
            new_findings.append(finding)
            continue
        start, end = _match_span(read(bundle, finding.path), finding.rule)
        if start < 0:
            # No span means the spike cannot say where the objection is, so it does not
            # get an opinion. Invisible-character rules land here by construction.
            decisions.append(
                Rescope(finding.path, finding.rule, False, "rule has no locatable span (L1)")
            )
            new_findings.append(finding)
            continue
        decision = scope.rescope(
            path=finding.path, rule=finding.rule, match_start=start, match_end=end
        )
        decisions.append(decision)
        new_findings.append(
            replace(finding, severity=Verdict(_FLOOR)) if decision.downgraded else finding
        )

    worst = max((f.severity for f in new_findings), key=lambda v: v.rank, default=Verdict.CLEAN)
    return (
        ScanReport(
            verdict=Verdict.CLEAN if not new_findings else worst,
            findings=new_findings,
            surfaces_scanned=list(report.surfaces_scanned),
            tier=report.tier,
            signature=report.signature,
        ),
        decisions,
    )
