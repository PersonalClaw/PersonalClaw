"""Content-type-aware tool-output projection (OP1).

Replaces the role of the blunt head/tail char cap (:func:`maybe_truncate`) for
*large* outputs with a projection that keeps the **meaningful** slice for the
output's type — the failing lines of a log, the changed hunks of a diff, the
shape of a JSON blob — instead of cutting the middle out blindly.

Projection ≠ truncation: truncation loses; projection **defers**. The full raw
output is retained elsewhere (the tool-result store, OP2) and the projected
preview names how to fetch it. This module is the pure, side-effect-free
*shaping* half; the store + retrieval tool live in :mod:`result_store`.

Conservative + fail-soft by design (the cardinal failure is hiding the part the
model needed — see the plan §5 risk register):
  * a result already within ``cap`` passes through **untouched** (never project);
  * an **unknown/ambiguous** type falls back to head/tail :func:`maybe_truncate`
    (today's exact behavior) — projection only *engages* for a large result of a
    *recognized* type;
  * a declared ``content_type`` (the tool told us) always beats inference.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from personalclaw.tool_providers.base import maybe_truncate

logger = logging.getLogger(__name__)

# Default cap for tool output fed back into the model before projection engages.
# Shared by native builtins + the MCP tool adapter (the app) via the SDK, so every
# tool surface projects at the same threshold.
DEFAULT_TOOL_OUTPUT_CAP = 60_000

# The recognized content types. ``generic`` is the fallback (head/tail cap).
CONTENT_TYPES = ("log", "diff", "json", "test", "csv", "markdown", "generic")

# ---------------------------------------------------------------------------
# User-teachable projection rules (TokenJuice, OP6)
# ---------------------------------------------------------------------------
# The builtin projectors cover the common cases; a user rule teaches the DISPATCH
# for a tool whose large output the sniffer would otherwise mis-read as ``generic``
# (e.g. a domain log format, a custom structured dump). A rule maps a content marker
# (regex over the output head) → one of the builtin strategies, so the proven
# projectors do the shaping — no user-authored CODE runs (declarative, safe).
#
# Registered rules are consulted BEFORE the heuristic sniff (explicit user intent
# beats inference) and are fail-soft: a rule with a bad regex is skipped + logged,
# never raising into the tool-dispatch path. Populated from AppConfig at startup via
# :func:`set_user_rules`; empty by default (today's exact behavior).


@dataclass(frozen=True)
class ProjectionRule:
    """A user-taught dispatch rule: output whose head matches ``match_regex`` is
    projected with ``strategy`` (a builtin content type). Pure data — no code."""

    name: str
    match_regex: str
    strategy: str  # a member of CONTENT_TYPES (excluding "generic")


_USER_RULES: tuple[tuple[str, "re.Pattern[str]", str], ...] = ()


def set_user_rules(rules: "list[ProjectionRule] | None") -> None:
    """Install the user's projection rules (from AppConfig). Compiles each regex once;
    a rule with an invalid regex or unknown strategy is dropped + logged (fail-soft, so
    a typo never breaks tool dispatch). Idempotent — replaces the whole set."""
    global _USER_RULES
    compiled: list[tuple[str, re.Pattern[str], str]] = []
    for r in rules or []:
        strat = str(getattr(r, "strategy", "")).strip().lower()
        pat = str(getattr(r, "match_regex", "")).strip()
        if strat not in _PROJECTORS or not pat:
            logger.debug(
                "projection rule %r skipped (bad strategy/empty regex)", getattr(r, "name", "?")
            )
            continue
        try:
            compiled.append((str(getattr(r, "name", "")), re.compile(pat, re.M), strat))
        except re.error:
            logger.warning(
                "projection rule %r has an invalid regex — skipped", getattr(r, "name", "?")
            )
    _USER_RULES = tuple(compiled)


def _match_user_rule(sample: str) -> str | None:
    """The strategy of the first user rule whose regex matches ``sample`` (or None).
    Never raises — a rule that errors at match time is skipped."""
    for name, pat, strat in _USER_RULES:
        try:
            if pat.search(sample):
                return strat
        except re.error:
            continue
    return None


@dataclass
class Projection:
    """Outcome of projecting one tool output."""

    text: str  # the projected preview (what the model sees)
    truncated: bool  # whether anything was dropped
    original_length: int | None  # raw char length when truncated (else None)
    content_type: str  # the type used to project (recognized or "generic")


# ---------------------------------------------------------------------------
# Type inference (cheap sniff; declared type always wins upstream)
# ---------------------------------------------------------------------------

_DIFF_RE = re.compile(r"^(diff --git |@@ -\d|index [0-9a-f]+\.\.|\+\+\+ |--- )", re.M)
_TEST_RE = re.compile(
    r"\b(PASSED|FAILED|\d+ passed|\d+ failed|=+ test session|FAIL\b|AssertionError)\b"
)
_JSON_LEAD_RE = re.compile(r"^\s*[\[{]")


def infer_content_type(text: str) -> str:
    """Best-effort content-type sniff. Returns a member of :data:`CONTENT_TYPES`.

    Conservative: only returns a specific type on a confident marker; anything
    ambiguous returns ``"generic"`` so projection falls back to the safe cap.
    """
    if not text:
        return "generic"
    sample = text[:4096]
    # User-taught rules win over the heuristic sniff (explicit intent beats
    # inference) — TokenJuice OP6. Fail-soft: no rules / no match → the sniff below.
    user_strategy = _match_user_rule(sample)
    if user_strategy is not None:
        return user_strategy
    # diff/patch — the most distinctive leading markers.
    if _DIFF_RE.search(sample):
        return "diff"
    # test output — pytest/unittest-ish summaries + failure markers.
    if _TEST_RE.search(sample):
        return "test"
    # json — must actually parse (a leading brace isn't enough on its own).
    if _JSON_LEAD_RE.match(sample):
        try:
            json.loads(text)
            return "json"
        except (json.JSONDecodeError, ValueError):
            pass
    # csv — a consistent delimiter across the first few lines + a header-ish row.
    if _looks_like_csv(sample):
        return "csv"
    return "generic"


def _looks_like_csv(sample: str) -> bool:
    lines = [ln for ln in sample.splitlines() if ln.strip()][:5]
    if len(lines) < 2:
        return False
    counts = [ln.count(",") for ln in lines]
    return counts[0] >= 1 and len(set(counts)) == 1  # same comma count each row


# ---------------------------------------------------------------------------
# Per-type projectors — each keeps the salient slice within ``cap``
# ---------------------------------------------------------------------------

_ERROR_LINE_RE = re.compile(r"(error|warn|fail|exception|traceback|fatal|denied|✗|❌)", re.I)


def _project_log(text: str, cap: int) -> str:
    """Head + the error/warning lines + tail + a line-count note (not a blind
    middle-cut). The signal in a long log is the error lines, wherever they are."""
    lines = text.splitlines()
    n = len(lines)
    head_n, tail_n = 40, 40
    head = lines[:head_n]
    tail = lines[-tail_n:] if n > head_n + tail_n else []
    middle = lines[head_n : n - tail_n] if tail else []
    errs = [ln for ln in middle if _ERROR_LINE_RE.search(ln)]
    # cap the error sample so a log that's ALL errors doesn't blow the budget
    err_cap = 60
    elided_errs = max(0, len(errs) - err_cap)
    errs = errs[:err_cap]
    parts: list[str] = []
    parts.extend(head)
    if errs:
        parts.append(
            f"\n…[{len(middle)} middle lines elided; {len(errs)} error/warning line(s) kept"
            + (f", {elided_errs} more errors not shown" if elided_errs else "")
            + "]…\n"
        )
        parts.extend(errs)
    elif middle:
        parts.append(f"\n…[{len(middle)} middle lines elided — no error/warning markers]…\n")
    if tail:
        parts.append("\n…tail…")
        parts.extend(tail)
    out = "\n".join(parts)
    # final safety: if the salient slice itself exceeds cap, head/tail it.
    capped, _, _ = maybe_truncate(out, cap)
    return capped


def _project_diff(text: str, cap: int) -> str:
    """Changed hunks + a +N/-M stat summary; the unchanged context is the noise."""
    add = sum(1 for ln in text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    rem = sum(1 for ln in text.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    files = len(re.findall(r"^diff --git ", text, re.M)) or len(re.findall(r"^\+\+\+ ", text, re.M))
    summary = f"[diff: {files} file(s), +{add}/-{rem}]\n"
    # Reserve room for the summary; never pass a negative budget to maybe_truncate
    # (a tiny cap shorter than the summary would otherwise underflow).
    budget = max(0, cap - len(summary)) if cap else None
    capped, _, _ = maybe_truncate(text, budget)
    return summary + capped


def _project_json(text: str, cap: int) -> str:
    """Shape (keys/types) + a sample + length — not a mid-string cut that yields
    invalid JSON. Falls back to head/tail if it doesn't parse (shouldn't, since
    inference parsed it, but declared-type json might not)."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        capped, _, _ = maybe_truncate(text, cap)
        return capped
    if isinstance(data, list):
        shape = f"[array: {len(data)} items]"
        sample = json.dumps(data[0], indent=2)[: cap // 2] if data else "(empty)"
        body = f"{shape}\nfirst item:\n{sample}"
    elif isinstance(data, dict):
        keys = list(data.keys())
        shape = "{object: " + ", ".join(f"{k}: {type(data[k]).__name__}" for k in keys[:40]) + "}"
        sample = json.dumps(data, indent=2)
        body = shape + ("\n" + sample[: cap // 2] if len(sample) > cap else "\n" + sample)
    else:
        body = json.dumps(data)
    capped, _, _ = maybe_truncate(body, cap)
    return capped


def _project_test(text: str, cap: int) -> str:
    """Failures + the summary line; elide the passing noise."""
    lines = text.splitlines()
    fail_lines = [
        ln
        for ln in lines
        if re.search(r"\b(FAIL|FAILED|ERROR|AssertionError|✗)\b", ln)
        or ln.strip().startswith(("E   ", "FAILED", "_____"))
    ]
    summary = [
        ln
        for ln in lines
        if re.search(r"\b(\d+ passed|\d+ failed|\d+ error|passed|failed)\b", ln)
        and ("=" in ln or "passed" in ln or "failed" in ln)
    ]
    tail = lines[-12:]
    parts = []
    if fail_lines:
        parts.append(f"[test output: {len(fail_lines)} failure/error line(s), {len(lines)} total]")
        parts.extend(fail_lines[:120])
    if summary:
        parts.append("\nsummary:")
        parts.extend(summary[-5:])
    if not fail_lines and not summary:
        # recognized as test but no extractable failures → tail (the summary lives there)
        parts.extend(tail)
    out = "\n".join(parts)
    capped, _, _ = maybe_truncate(out, cap)
    return capped


def _project_csv(text: str, cap: int) -> str:
    """Header + first/last rows + a row count."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 12:
        capped, _, _ = maybe_truncate(text, cap)
        return capped
    head = lines[:6]
    tail = lines[-5:]
    out = "\n".join([*head, f"…[{len(lines) - 11} more rows]…", *tail])
    capped, _, _ = maybe_truncate(out, cap)
    return capped


_PROJECTORS = {
    "log": _project_log,
    "diff": _project_diff,
    "json": _project_json,
    "test": _project_test,
    "csv": _project_csv,
}


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------


def project_output(
    text: str,
    *,
    cap: int | None,
    content_type: str | None = None,
) -> Projection:
    """Project ``text`` to a type-aware preview within ``cap`` chars.

    * ``cap`` is None or text already fits → pass through untouched (never
      project a small result).
    * ``content_type`` declared by the tool wins; else infer (conservative).
    * A recognized large type → its projector (keeps the salient slice).
    * ``generic``/unknown → head/tail :func:`maybe_truncate` (today's behavior).

    Returns a :class:`Projection`. ``content_type`` on the result reflects what
    was actually used (so the renderer + store can read it).
    """
    if cap is None or len(text) <= cap:
        # Small / uncapped: never project. Report the type (declared or sniffed)
        # so the renderer can still pick a rich view, but leave bytes untouched.
        ctype = content_type or infer_content_type(text)
        return Projection(
            text=text,
            truncated=False,
            original_length=None,
            content_type=ctype if ctype in CONTENT_TYPES else "generic",
        )

    original_length = len(text)
    ctype = content_type if content_type in CONTENT_TYPES else infer_content_type(text)
    projector = _PROJECTORS.get(ctype)
    if projector is None:
        # generic / unknown → the safe blunt cap (no regression).
        capped, _, _ = maybe_truncate(text, cap)
        return Projection(
            text=capped, truncated=True, original_length=original_length, content_type="generic"
        )
    projected = projector(text, cap)
    return Projection(
        text=projected, truncated=True, original_length=original_length, content_type=ctype
    )


def project_and_retain(
    text: str,
    *,
    session_key: str = "",
    content_type: str | None = None,
    cap: int | None,
) -> tuple[str, dict]:
    """Project ``text`` AND retain its raw for on-demand retrieval — the single
    dispatch-time discipline every tool surface shares (native builtins AND the MCP
    adapter, OP5), so no surface loses the retrievable-raw guarantee.

    Returns ``(output_text, metadata)`` where metadata carries ``content_type`` and,
    when the result was projected and a ``session_key`` is available, ``raw_ref`` — plus
    the preview names the recovery affordance (``tool_result_get(result_id="r_…")``) so
    the model can pull the dropped slice. Small / unknown → pass-through (fail-soft),
    exactly as ``project_output``."""
    from personalclaw.tool_providers import result_store

    proj = project_output(text, cap=cap, content_type=content_type)
    # meta carries the projection outcome too, so callers (e.g. _ok_capped, the MCP
    # adapter) read truncated/original_length from here instead of re-running
    # project_output a second time to recover them.
    meta: dict = {
        "content_type": proj.content_type,
        "truncated": proj.truncated,
        "original_length": proj.original_length,
    }
    if not (proj.truncated and session_key):
        return proj.text, meta
    raw_ref = result_store.store_result(session_key, text, content_type=proj.content_type)
    if not raw_ref:
        return proj.text, meta
    meta["raw_ref"] = raw_ref
    out = proj.text + (
        f"\n\n[projected {proj.content_type} output: showing {len(proj.text)} of "
        f'{proj.original_length} chars — full result: tool_result_get(result_id="{raw_ref}")]'
    )
    return out, meta
