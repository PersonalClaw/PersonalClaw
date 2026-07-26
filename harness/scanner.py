"""Static architectural-boundary scanner (§1.3).

Pure-static checks (AST/regex over files, no execution), each with a stable check-id that
rule specs reference via their ``scanner:`` frontmatter. The scanner is trust-critical: a
check that cries wolf on a clean tree erodes the whole harness, so every check here is
calibrated to produce **zero findings on a clean HEAD** and each is either:

- **ERROR** — an exactly-derivable invariant (parity of two enumerable sets); a finding is
  a real defect.
- **WARNING** — a heuristic that can't prove intent; a finding is worth a look, not a hard
  stop.

Findings are WHAT/WHY/FIX-formatted so a coding agent self-corrects without a human.

Checks operate over a caller-supplied set of files (``run --diff`` passes the changed
files; a bare ``scan`` passes the whole tracked tree). A check that needs a cross-file
"other end" (the FE lifecycle union, the allowlist) reads that end from the repo
regardless of whether it's in the changed set — the invariant is about agreement, and
either end changing can break it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# ── Finding model ─────────────────────────────────────────────────────────────

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    """One scanner finding. ``check`` is the stable check-id; ``level`` is ERROR/WARNING;
    ``what``/``why``/``fix`` are the agent-facing triad; ``file``/``line`` locate it."""

    check: str
    level: str
    file: Path
    line: int
    what: str
    why: str
    fix: str

    def format(self, root: Path) -> str:
        try:
            rel = self.file.relative_to(root)
        except ValueError:
            rel = self.file
        loc = f"{rel}:{self.line}" if self.line else str(rel)
        marker = "❌" if self.level == ERROR else "⚠️ "
        return (
            f"{marker} [{self.check}] {loc}\n"
            f"    WHAT: {self.what}\n"
            f"    WHY:  {self.why}\n"
            f"    FIX:  {self.fix}"
        )


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ── Helpers ─────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _is_under(path: Path, root: Path, *parts: str) -> bool:
    """True if ``path`` is under ``root/<parts…>`` (path-prefix match on relative parts)."""
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return False
    return rel_parts[: len(parts)] == parts


# ── Check: hook-provider-parity (ERROR) ─────────────────────────────────────────


def _string_literals_in(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def check_hook_provider_parity(files: list[Path], root: Path) -> list[Finding]:
    """Every provider-name string returned by a ``name`` property under
    ``action_providers/`` must be in ``ALLOWED_HOOK_PROVIDERS`` (validation.py).

    Exact set parity → ERROR. Reads both ends from the repo (the allowlist and the
    provider files) so a change to either end is caught; only reports when a
    provider file is in the changed set (or the allowlist itself changed).
    """
    ap_root = root / "src" / "personalclaw" / "action_providers"
    validation_py = root / "src" / "personalclaw" / "validation.py"
    changed = set(files)
    touches_providers = any(
        _is_under(f, root, "src", "personalclaw", "action_providers") for f in changed
    )
    touches_allowlist = validation_py in changed
    if not (touches_providers or touches_allowlist):
        return []

    allowlist = _extract_frozenset_members(_read(validation_py), "ALLOWED_HOOK_PROVIDERS")
    if allowlist is None:
        return []  # can't parse the allowlist; don't guess

    findings: list[Finding] = []
    for f in sorted(ap_root.glob("*_provider.py")) if ap_root.is_dir() else []:
        name, lineno = _provider_name_of(_read(f))
        if name and name not in allowlist:
            findings.append(
                Finding(
                    check="hook-provider-parity",
                    level=ERROR,
                    file=f,
                    line=lineno,
                    what=f"action provider {name!r} is not in ALLOWED_HOOK_PROVIDERS",
                    why="a provider absent from the allowlist makes hook create/update reject "
                    "any hook that uses it, even though the executor exists",
                    fix=f"add {name!r} to the ALLOWED_HOOK_PROVIDERS frozenset in validation.py",
                )
            )
    return findings


def _extract_frozenset_members(source: str, name: str) -> set[str] | None:
    """Parse ``NAME = frozenset({...})`` and return its string members, or None if absent."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if name in targets:
                return set(_string_literals_in(node.value))
    return None


def _provider_name_of(source: str) -> tuple[str, int]:
    """Return (name, lineno) for the string returned by a ``name`` property, or ("", 0).

    Matches the house idiom: ``@property\n def name(self) -> str: return "foo"``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "", 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "name":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Constant):
                    if isinstance(sub.value.value, str):
                        return sub.value.value, sub.lineno
    return "", 0


# ── Check: sse-event-registered (ERROR, scoped to the loop registry) ────────────

# The loop/run SSE registry is the one with a FE lifecycle union (RUN_LIFECYCLE). Only
# publishes on THAT registry are checked — other registries (model downloads, embedding
# reindex, knowledge ingest) have their own vocabularies and no RUN_LIFECYCLE union, so
# checking them would be a false positive. We identify loop-registry publishes by the
# receiver/context idiom: `loop_sse().publish(...)` or `ctx.publish(...)` in loop kinds
# (see _receiver_is_loop_registry).


def check_sse_event_registered(files: list[Path], root: Path) -> list[Finding]:
    """Every literal loop-registry ``publish(..., "event", ...)`` event string must appear
    in the FE ``RUN_LIFECYCLE`` union. Exact set membership → ERROR.

    Only literal event names are checkable statically; a variable ``event`` is skipped
    (the WARNING would be noise). The FE union is always read from the repo.
    """
    changed = set(files)
    py_changed = [
        f for f in changed if f.suffix == ".py" and _is_under(f, root, "src", "personalclaw")
    ]
    lifecycle = _read(root / "web" / "src" / "pages" / "loops" / "useRunStream.ts")
    union = _extract_ts_run_lifecycle(lifecycle)
    if union is None:
        return []

    findings: list[Finding] = []
    for f in py_changed:
        for event, lineno in _loop_publish_events(_read(f)):
            if event not in union:
                findings.append(
                    Finding(
                        check="sse-event-registered",
                        level=ERROR,
                        file=f,
                        line=lineno,
                        what=f"loop SSE event {event!r} is published but not in RUN_LIFECYCLE",
                        why="EventSource registers one listener per event name; an event not "
                        "in the FE union is silently dropped — no error, no UI update",
                        fix=f"add {event!r} to RUN_LIFECYCLE in "
                        "web/src/pages/loops/useRunStream.ts (and handle it)",
                    )
                )
    return findings


def _extract_ts_run_lifecycle(source: str) -> set[str] | None:
    """Extract the string members of ``export const RUN_LIFECYCLE = [ ... ] as const``."""
    m = re.search(r"RUN_LIFECYCLE\s*=\s*\[(.*?)\]\s*as const", source, re.DOTALL)
    if not m:
        return None
    return set(re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)))


def _loop_publish_events(source: str) -> list[tuple[str, int]]:
    """Find ``<recv>.publish(<key>, "<event>", ...)`` calls where recv is a loop-registry
    receiver, returning (event, lineno) for literal event names."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "publish":
            continue
        if not _receiver_is_loop_registry(node.func.value):
            continue
        # ctx.publish(cid, "event", data) → event is arg[1]; loop_sse().publish same shape.
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            val = node.args[1].value
            if isinstance(val, str):
                out.append((val, node.lineno))
    return out


def _receiver_is_loop_registry(recv: ast.AST) -> bool:
    """True if the publish receiver is the loop SSE registry: a call to ``loop_sse()``
    (possibly chained: ``x.loop_sse()``) or the loop-kind ``ctx`` object."""
    if isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute):
        return recv.func.attr == "loop_sse"
    if isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name):
        return recv.func.id == "loop_sse"
    if isinstance(recv, ast.Name):
        return recv.id == "ctx"
    return False


# ── Check: config-four-points (ERROR) ───────────────────────────────────────────


def check_config_four_points(files: list[Path], root: Path) -> list[Finding]:
    """A ``_meta``-carrying field on a config dataclass in loader.py must appear in
    ``AppConfig.load()``'s mapping and in ``to_dict()`` output. Missing either → the
    silent-drop / silent-revert bug. Exact presence → ERROR.

    Static approximation: a field name declared with ``metadata=_meta(...)`` must appear
    somewhere in ``load()``'s body (as ``<field>=`` kwarg) — the load mapping is the point
    most often forgotten. to_dict() serializes whole sections via ``asdict`` so per-field
    presence there is covered by test_config_roundtrip; the scanner guards the load()
    mapping specifically (the harder-to-test half at diff time).
    """
    loader = root / "src" / "personalclaw" / "config" / "loader.py"
    if loader not in set(files):
        return []
    source = _read(loader)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    load_kwargs = _load_body_kwarg_names(tree)
    if load_kwargs is None:
        return []

    findings: list[Finding] = []
    for field_name, lineno in _meta_fields(tree):
        if field_name not in load_kwargs:
            findings.append(
                Finding(
                    check="config-four-points",
                    level=ERROR,
                    file=loader,
                    line=lineno,
                    what=f"config field {field_name!r} has _meta but is not set in "
                    "AppConfig.load()'s mapping",
                    why="a field absent from load()'s explicit mapping silently reverts to "
                    "its default on every reload (the user's setting won't stick)",
                    fix=f"map it in AppConfig.load(): {field_name}=...(<section>_data.get"
                    f"({field_name!r}, <default>))",
                )
            )
    return findings


def _meta_fields(tree: ast.Module) -> list[tuple[str, int]]:
    """Field names assigned ``= field(..., metadata=_meta(...))`` inside any class body."""
    out: list[tuple[str, int]] = []
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if _calls_field_with_meta(stmt.value):
                    out.append((stmt.target.id, stmt.lineno))
    return out


def _calls_field_with_meta(value: ast.AST | None) -> bool:
    if not isinstance(value, ast.Call):
        return False
    is_field = (isinstance(value.func, ast.Name) and value.func.id == "field") or (
        isinstance(value.func, ast.Attribute) and value.func.attr == "field"
    )
    if not is_field:
        return False
    for kw in value.keywords:
        if kw.arg == "metadata" and isinstance(kw.value, ast.Call):
            fn = kw.value.func
            if (isinstance(fn, ast.Name) and fn.id == "_meta") or (
                isinstance(fn, ast.Attribute) and fn.attr == "_meta"
            ):
                return True
    return False


def _load_body_kwarg_names(tree: ast.Module) -> set[str] | None:
    """All keyword-argument names used anywhere inside ``AppConfig.load()``.

    Covers the nested-constructor idiom ``legibility=LegibilityConfig(discover_tips=...)``
    — we collect every kwarg name at any depth of load()'s body, which is exactly the set
    of field names the load mapping assigns.
    """
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and cls.name == "AppConfig":
            for item in cls.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "load"
                ):
                    names: set[str] = set()
                    for call in ast.walk(item):
                        if isinstance(call, ast.Call):
                            for kw in call.keywords:
                                if kw.arg:
                                    names.add(kw.arg)
                    return names
    return None


# ── Check: app-sdk-boundary (ERROR) ─────────────────────────────────────────────


def check_app_sdk_boundary(files: list[Path], root: Path) -> list[Finding]:
    """Code under repo-root ``apps/`` may import core only via ``personalclaw.sdk.*``.
    A deep ``personalclaw.<non-sdk>`` import → ERROR (this mirrors the boundary test,
    promoted to diff time)."""
    findings: list[Finding] = []
    for f in files:
        if f.suffix != ".py" or not _is_under(f, root, "apps"):
            continue
        if f.name.startswith("test_"):
            continue  # test files may import core helpers (matches the boundary test)
        for mod, lineno in _core_imports(_read(f)):
            parts = mod.split(".")
            if not (len(parts) >= 2 and parts[1] == "sdk"):
                findings.append(
                    Finding(
                        check="app-sdk-boundary",
                        level=ERROR,
                        file=f,
                        line=lineno,
                        what=f"app imports core internal {mod!r} (not personalclaw.sdk.*)",
                        why="apps must be removable; a deep core import couples the app to a "
                        "private internal that can move, breaking the boundary",
                        fix="import via personalclaw.sdk.* — or promote the needed symbol to "
                        "the SDK facade (a reviewed addition), never reach around it",
                    )
                )
    return findings


def _core_imports(source: str) -> list[tuple[str, int]]:
    """``personalclaw.*`` module paths imported by the source (absolute imports only)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("personalclaw"):
                    out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.startswith("personalclaw"):
                out.append((node.module, node.lineno))
    return out


# ── Check: destructive-test-isolation (WARNING, heuristic) ──────────────────────

# Require CALL shape (`config_dir(`, `save_credential(`) not keyword/param shape
# (`save_credential=lambda …`) so a test that passes a mock stub as a kwarg — already
# isolated by construction — isn't flagged. `\b(?<!=)` avoids `save_credential=`.
_HOME_TOUCH_RE = re.compile(
    r"(config_dir\(\)|local_models_dir\(|(?<![=\w])save_credential\(|credential_store\()"
)
_ISOLATION_RE = re.compile(r"tmp_path|monkeypatch|PERSONALCLAW_HOME")


def check_destructive_test_isolation(files: list[Path], root: Path) -> list[Finding]:
    """A test module referencing config-dir/local-models/credential paths should carry a
    tmp_path/monkeypatch isolation fixture. Heuristic → WARNING."""
    findings: list[Finding] = []
    for f in files:
        if not (f.name.startswith("test_") and f.suffix == ".py"):
            continue
        src = _read(f)
        if _HOME_TOUCH_RE.search(src) and not _ISOLATION_RE.search(src):
            findings.append(
                Finding(
                    check="destructive-test-isolation",
                    level=WARNING,
                    file=f,
                    line=1,
                    what="test touches config-dir/local-models/credential paths with no "
                    "tmp_path/monkeypatch isolation in the module",
                    why="an unisolated destructive test can corrupt the real ~/.personalclaw "
                    "home (a bound model was once deleted this way) and flakes under xdist",
                    fix="take tmp_path/monkeypatch and redirect config_dir()/PERSONALCLAW_HOME "
                    "to a per-test temp dir",
                )
            )
    return findings


# ── Check: fence-at-ingestion (WARNING, heuristic — diff-scoped) ─────────────────

# Requires an ADDED line (not a docstring) that appends external text to a prompt/context
# accumulator: a `+=`/`.append(`/`.format(`/f-string on a `prompt`/`context`/`system`
# accumulator whose value mentions an external source. Deliberately narrow: this heuristic
# only fires on changed lines (diff-scoped) so it advises on your diff, not the whole tree.
_PROMPT_ACCUM_RE = re.compile(
    r"\b(prompt|context|system_?prompt|messages)\b.{0,30}"
    r"(\+=|\.append\(|\.extend\(|\.format\(|f['\"])",
    re.IGNORECASE,
)
_EXTERNAL_TOKEN_RE = re.compile(
    r"\b(channel_?(text|message|content)|inbound|fetched|web_?content|page_?text|"
    r"untrusted|external_?text|user_?content)\b",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"fence_untrusted")


def check_fence_at_ingestion(
    files: list[Path], root: Path, *, changed_lines: dict[Path, set[int]] | None = None
) -> list[Finding]:
    """An ADDED line that appends external-sourced text to a prompt accumulator, in a file
    that never calls ``fence_untrusted``, may be missing a fence. Heuristic → WARNING.

    Diff-scoped: only considers lines in ``changed_lines`` when provided (the ``run --diff``
    path); with no diff it scans all lines of the given files. Narrow by construction to
    keep the signal high — a broad "mentions channel near message" match is pure noise."""
    findings: list[Finding] = []
    for f in files:
        if f.suffix != ".py" or not _is_under(f, root, "src", "personalclaw"):
            continue
        if f.name.startswith("test_"):
            continue
        src = _read(f)
        if _FENCE_RE.search(src):
            continue  # file fences somewhere; assume it's handled
        lines = src.splitlines()
        consider = changed_lines.get(f) if changed_lines is not None else None
        for i, line in enumerate(lines, start=1):
            if consider is not None and i not in consider:
                continue
            if _PROMPT_ACCUM_RE.search(line) and _EXTERNAL_TOKEN_RE.search(line):
                findings.append(
                    Finding(
                        check="fence-at-ingestion",
                        level=WARNING,
                        file=f,
                        line=i,
                        what="external-sourced text is appended to a prompt/context here and "
                        "this file never calls fence_untrusted",
                        why="untrusted text folded raw into a prompt lets an attacker's text "
                        "act as agent instructions (prompt injection)",
                        fix="wrap the external text with fence_untrusted; if the fence is "
                        "applied elsewhere this is a false positive (heuristic)",
                    )
                )
    return findings


# ── Check: no-naive-transcript-cut (WARNING, heuristic) ─────────────────────────

_TRUNCATE_RE = re.compile(r"(messages|transcript|journal|history)\s*\[\s*-?\d")
_WALKBACK_RE = re.compile(r"_drop_orphan_tool_results|orphan_tool|walk.?back")


def check_no_naive_transcript_cut(files: list[Path], root: Path) -> list[Finding]:
    """A slice of a messages/transcript/journal list in the compaction modules that doesn't
    reference the orphan-dropping walk-back helper may split a tool-call/result pair.
    Heuristic → WARNING."""
    findings: list[Finding] = []
    targets = {"context_compaction.py", "context_management.py"}
    for f in files:
        if f.name not in targets:
            continue
        src = _read(f)
        if _TRUNCATE_RE.search(src) and not _WALKBACK_RE.search(src):
            findings.append(
                Finding(
                    check="no-naive-transcript-cut",
                    level=WARNING,
                    file=f,
                    line=1,
                    what="a transcript/journal slice appears here without referencing the "
                    "orphan-tool-result walk-back helper",
                    why="cutting between a tool_use and its tool_result makes the next "
                    "provider request malformed (most APIs reject an unpaired tool block)",
                    fix="route truncation through _drop_orphan_tool_results (or the shared "
                    "walk-back helper) so no tool-call/result pair is split",
                )
            )
    return findings


# ── Registry + entry point ──────────────────────────────────────────────────────

_CHECKS = {
    "hook-provider-parity": check_hook_provider_parity,
    "sse-event-registered": check_sse_event_registered,
    "config-four-points": check_config_four_points,
    "app-sdk-boundary": check_app_sdk_boundary,
    "destructive-test-isolation": check_destructive_test_isolation,
    "fence-at-ingestion": check_fence_at_ingestion,
    "no-naive-transcript-cut": check_no_naive_transcript_cut,
}


def known_checks() -> set[str]:
    """The set of scanner check-ids (validate uses this to resolve rule `scanner:` refs)."""
    return set(_CHECKS)


def scan(
    files: list[Path],
    root: Path | None = None,
    *,
    changed_lines: dict[Path, set[int]] | None = None,
) -> list[Finding]:
    """Run every check over ``files``. Returns all findings (ERRORs and WARNINGs), sorted
    by (level, check, file). ``files`` should be absolute paths.

    ``changed_lines`` (path → set of changed line numbers) is threaded to line-scoped
    heuristic checks so ``run --diff`` advises only on the lines a diff actually touched.
    """
    r = root if root is not None else _repo_root()
    findings: list[Finding] = []
    for name, check in _CHECKS.items():
        if name == "fence-at-ingestion":
            findings.extend(check(files, r, changed_lines=changed_lines))  # type: ignore[call-arg]
        else:
            findings.extend(check(files, r))
    findings.sort(key=lambda f: (0 if f.level == ERROR else 1, f.check, str(f.file), f.line))
    return findings
