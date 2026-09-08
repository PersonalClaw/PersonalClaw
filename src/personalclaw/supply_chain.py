"""Supply-chain safety — the shared install-time content scanner.

A single, vendor-neutral ``SkillScanner`` that statically inspects **staged**
(not-yet-live) community content — skills AND apps both carry executable code
(``scripts/``, ``setup`` hooks) and become reachable the moment they install, so
both run through this one gate before anything lands live.

The scanner is **pattern + structural, no LLM on the hot path** (an install must
work offline / deterministically). It returns a :class:`ScanReport` with a
:class:`Verdict`; the *decision* (commit / confirm / refuse) is the caller's,
modulated by the source's trust tier. ``dangerous`` is the load-bearing floor:
reserved for high-confidence malice (exfil-to-remote, destructive-root,
obfuscated-exec) so a calculated ``warning`` stays overridable but outright
malware never is.

Reuses ``history._SENSITIVE_TOOL_PATTERNS`` (the credential/secret path set) so
"reads ~/.aws" detection has one source of truth. Not a sandbox — static
inspection only; it reduces risk, it does not contain execution.

The DANGEROUS band is additionally scoped by EXECUTION REACHABILITY (issues #2526,
#2625) — the "Execution reachability" section below states the rule in full. In short: a
match the bundle cannot execute is *disclosed* as a ``warning`` rather than refused
outright, because it is a string (or a comment), not a payload. Default-deny throughout —
a match stays DANGEROUS unless inertness is PROVED, and proved structurally from the AST
and the tokeniser rather than from the text, so the failure mode is a false block and
never a false pass.
"""

from __future__ import annotations

import ast
import io
import json
import re
import tokenize
import unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

# One source of truth for "touches a credential/secret path" (IMDS, ~/.aws, …).
from personalclaw.history import _SENSITIVE_TOOL_PATTERNS

# Artifact-signature state carried on every report (C2). signing.py imports nothing
# from here, so this is a one-way edge.
from personalclaw.signing import SignatureInfo

# ── Verdict + report ────────────────────────────────────────────────────────


class Verdict(str, Enum):
    """Scan outcome, ascending severity. ``dangerous`` is non-overridable."""

    CLEAN = "clean"  # nothing matched
    LOW = "low"  # benign-but-notable (advisory)
    WARNING = "warning"  # ambiguous risk — overridable with explicit confirm
    DANGEROUS = "dangerous"  # high-confidence malice — terminal, no override

    @property
    def rank(self) -> int:
        return {"clean": 0, "low": 1, "warning": 2, "dangerous": 3}[self.value]


# Trust tiers a source declares; they only ever DOWNGRADE the final verdict for
# trusted provenance (a bundled skill's `curl` is not the same risk as a random
# community one). They never upgrade — a dangerous pattern stays dangerous.
class TrustTier(str, Enum):
    BUILTIN = "builtin"  # shipped with PClaw — scan advisory-only (cap at low)
    OFFICIAL = "official"  # curated registry — warnings non-blocking
    TRUSTED = "trusted"  # user-trusted registry — warnings non-blocking
    COMMUNITY = "community"  # arbitrary — full gate (the default)


class Reachability(str, Enum):
    """Whether the code a DANGEROUS match sits in can EXECUTE that match.

    FIVE states, not two, and that is the point. "We could not tell" must never render
    as "safe": a file the analysis could not read is reported as its own outcome
    (:data:`UNPARSEABLE`) and treated exactly like :data:`REACHABLE`, so an unreadable
    file can never be mistaken for a clean one by a reviewer reading the report.

    :data:`COMMENTARY` and :data:`UNREACHABLE` are kept apart for the same reason, in the
    other direction: they are proved by different evidence (a token class vs. five
    clauses over the whole bundle) and both land on WARNING, so a reviewer can tell
    "this is a comment" from "this is a literal nothing reaches".
    """

    #: Nothing was asked — the finding is not a DANGEROUS script match, or the caller
    #: scanned a bare text blob with no bundle around it to reason over.
    NOT_ANALYSED = "not_analysed"
    #: A clause refused. The match is presumed executable and stays DANGEROUS.
    REACHABLE = "reachable"
    #: Every clause held. The match is a string the bundle cannot run → WARNING.
    UNREACHABLE = "unreachable"
    #: The match lies wholly inside a ``COMMENT`` token or a docstring — text the
    #: interpreter discards at tokenisation, so it is not code at all → WARNING.
    COMMENTARY = "commentary"
    #: The file is not parseable Python, so there is no AST to reason over. Its own
    #: state, and DANGEROUS — never folded into any of the answers above.
    UNPARSEABLE = "unparseable"


@dataclass
class Finding:
    """One matched signal. ``severity`` is this finding's own classification;
    the report's verdict is the max across findings (after tier modulation).

    ``reachability`` records what the execution-reachability pass concluded about this
    match and ``reachability_reason`` the sentence a reviewer needs to check it. Both are
    disclosure, never a softener: a finding re-scored to WARNING keeps its rule, path,
    surface and evidence byte-for-byte, so the user is still told and still consents."""

    surface: str  # "script" | "manifest" | "frontmatter" | "supply_chain"
    severity: Verdict
    rule: str  # stable short id, e.g. "destructive_root"
    path: str  # staged-relative file path ("" for whole-content surfaces)
    evidence: str  # the matched snippet (truncated, for the UX)
    reachability: Reachability = Reachability.NOT_ANALYSED
    reachability_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "severity": self.severity.value,
            "rule": self.rule,
            "path": self.path,
            "evidence": self.evidence,
            "reachability": self.reachability.value,
            "reachability_reason": self.reachability_reason,
        }


@dataclass
class ScanReport:
    """The gate's output. ``verdict`` is the decision input; ``findings`` is the
    evidence the install UX surfaces ("community skill — 2 warnings").

    ``signature`` is SECURITY-HARDENING contract C2 — the artifact-signature state of
    the bundle this report describes (``signed`` / ``unsigned`` / ``invalid`` + signer).
    It is set by the install gate that verified the staged tree, NOT by the scanner: the
    scanner is content inspection, signing is provenance, and conflating them would let
    a signed bundle's verdict be read as authenticity or vice-versa. Its default is
    ``unsigned``, which is the honest answer for a report produced by a path that never
    looked (skill installs today) — never a claimed-but-unchecked ``signed``."""

    verdict: Verdict = Verdict.CLEAN
    findings: list[Finding] = field(default_factory=list)
    surfaces_scanned: list[str] = field(default_factory=list)
    tier: TrustTier = TrustTier.COMMUNITY
    signature: SignatureInfo = field(default_factory=SignatureInfo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "tier": self.tier.value,
            "findings": [f.to_dict() for f in self.findings],
            "surfaces_scanned": list(self.surfaces_scanned),
            "signature": self.signature.to_dict(),
        }

    @property
    def is_dangerous(self) -> bool:
        return self.verdict is Verdict.DANGEROUS


# ── Pattern catalog ─────────────────────────────────────────────────────────

# What may legitimately FOLLOW a destructive target for the target to still BE the whole
# target. The anchor exists to keep `rm -rf /tmp/build` and `rm -rf $HOME/.cache` out of
# the terminal tier — the target has to END there, not merely start there. But whitespace,
# `;` and end-of-input are only how a command ends when it is the whole LINE, and malice
# does not arrive that way: it arrives inside a string literal, where the next character is
# the closing quote. `os.system("rm -rf /")` was a consentable WARNING purely because a `"`
# followed the `/`, while the same call with one trailing space was terminal (#2607). So the
# set also closes on a quote (all three kinds — Python/shell, and a JS template literal), on
# `)` for `$(rm -rf /)` and `execSync(...)`, and on `\` for an escape like `"rm -rf /\n"`.
# This widens where the target may END; it does not widen what counts as a target, so the
# `/tmp/build` and `$HOME/.cache` exclusions are untouched.
_DESTRUCTIVE_TARGET_END = r"""(?:\s|;|["'`)\\]|$)"""

# High-confidence DANGEROUS patterns (terminal). Reserved for unambiguous malice
# so the non-overridable floor doesn't trap legitimate skills (risk #1).
_DANGEROUS_SCRIPT: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # destructive-root: rm -rf / , rm -rf ~ , rm -rf $HOME, rm -fr /
    (
        "destructive_root",
        re.compile(
            r"\brm\s+-[rf]{1,2}\s+(?:-[rf]{1,2}\s+)*(?:/|~|\$HOME|\*)" + _DESTRUCTIVE_TARGET_END
        ),
    ),
    # fork bomb :(){ :|:& };:
    ("fork_bomb", re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    # disk wipe: mkfs, dd of=/dev/sdX, > /dev/sda
    #
    # The word boundary is spelled PER ALTERNATIVE, not once in front of the group. It used
    # to sit outside — `\b(?:mkfs…|dd…|>…)` — which applied it to the redirect branch too,
    # and `\b` before `>` demands a WORD character immediately to the left. So the branch
    # fired only on `echo>/dev/sda`, where `>` is jammed against a word, and missed
    # `cat /dev/zero > /dev/sda` — the way this is actually written (#2610). The two
    # word-initial branches never noticed, because they satisfy a leading `\b` naturally;
    # that is what let the third stay dead code. Unlike #2607 this is a LEADING-boundary
    # bug, so it failed identically bare and embedded — no quoting involved.
    #
    # The redirect branch takes NO leading anchor. `(?:^|\s|;|&|\|)` was the other candidate
    # and is measurably worse: it re-breaks `echo>/dev/sda`, since `o` is none of those. A
    # `>` immediately left of `/dev/sd` IS the redirect regardless of what precedes it, which
    # also picks up `2>/dev/sda`, `&>/dev/sda` and the `>>` append form for free. Precision is
    # carried by the target, not the anchor: `/dev/null`, `/dev/stdout` and `/dev/tty` are
    # not in `(?:sd|nvme|disk)`, so `> /dev/null` stays out of the terminal tier.
    (
        "disk_wipe",
        re.compile(
            r"(?:\bmkfs\.\w+|\bdd\s+[^\n]*\bof=/dev/(?:sd|nvme|disk)|>\s*/dev/(?:sd|nvme|disk))"
        ),
    ),
    # pipe-to-shell exec of remote content: curl|wget … | sh/bash
    (
        "remote_exec_pipe",
        re.compile(r"\b(?:curl|wget|fetch)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b"),
    ),
    # obfuscated exec: base64 -d | sh , echo … | base64 -d | bash
    ("obfuscated_exec", re.compile(r"base64\s+(?:--decode|-d|-D)\b[^\n|]*\|\s*(?:ba|z|da)?sh\b")),
)

#: The DANGEROUS rules decided on the AST rather than on a regex (issue #2607) — see the
#: "Native destruction" section below. Every one of them is TERMINAL, exactly like the shell
#: catalog above: the owner's ruling on #2607 is that destruction spelled in the host
#: language is the same claim as destruction spelled as a shell command, so it lands in the
#: same band. One rule per destruction CLAIM, not per API spelling: three sentences a
#: non-expert can weigh, rather than one rule per function name in :mod:`shutil`.
_NATIVE_DESTRUCTION_RULES: tuple[str, ...] = (
    "destructive_delete",  # removes a path the OS or the account owns
    "destructive_walk",  # walks a whole tree and deletes what it yields
    "destructive_truncate",  # blanks a file the OS owns
)

# WARNING-band script patterns (overridable): notable but not proof of malice.
_WARNING_SCRIPT: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("eval_exec", re.compile(r"\beval\s*[\"'(]")),
    ("pipe_to_shell", re.compile(r"\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")),  # any pipe-to-shell
    ("curl_network", re.compile(r"\b(?:curl|wget)\b")),
    ("sudo_use", re.compile(r"\bsudo\b")),
    ("python_exec", re.compile(r"\b(?:os\.system|subprocess\.(?:call|run|Popen)|exec\(|eval\()")),
    ("crontab_write", re.compile(r"\bcrontab\b")),
)

# Prompt-injection signals in manifest/frontmatter prose (WARNING band).
_INJECTION_PROSE: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("injection_ignore", re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I)),
    ("injection_disregard", re.compile(r"disregard\s+(?:the\s+)?(?:above|prior|system)", re.I)),
    ("injection_coerce", re.compile(r"you\s+must\s+(?:now\s+)?(?:run|execute|call|always)", re.I)),
    ("injection_override", re.compile(r"(?:new|updated)\s+system\s+prompt\s*:", re.I)),
)

# Script file extensions worth inspecting as executable content.
_SCRIPT_EXTS = {".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".rb", ".pl", ".ps1"}
# Text surfaces scanned for injection / invisible-Unicode (skill + app manifests).
_MANIFEST_NAMES = {"skill.md", "app.json", "readme.md", "manifest.json"}
_MAX_FILE_BYTES = 512 * 1024  # don't read huge blobs into the scanner
_EVIDENCE_CAP = 120
# How the evidence window is SHAPED (the cap above is the only length bound). The
# window is anchored at the start of the matched line — so a call brings its callee
# and what it is assigned to — and, when the construct opens a bracket, walks to the
# matching close bracket so the ARGUMENTS come along. A window that merely hugged the
# matched token showed `subprocess.run(  # noqa: S603` and cut the argv off on the
# next line: the reader was warned about a subprocess and shown everything except
# what it runs. These two bound that walk on a pathological or unbalanced file.
_EVIDENCE_MAX_LINES = 8  # newlines a bracketed construct may span
_EVIDENCE_SCAN_CHARS = 600  # raw chars the bracket walk may consume
# Chars of same-line context kept BEFORE the match (`proc = ` in `proc = run(...)`),
# capped so a long left-hand side can never push the construct itself out of budget.
_EVIDENCE_LEAD_CAP = 40
# Every cut is marked with this, so a truncated snippet is never read as the whole thing.
_ELLIPSIS = "…"
# Directories that are tooling/dependency noise, not the app's own content. A git
# clone carries .git/ (whose hooks/*.sample trip the script rules — a false
# positive); node_modules/venv are vendored deps the author didn't write. Skipping
# them keeps the gate focused on first-party content (and faster).
_SKIP_DIR_NAMES = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".tox"}


# ── What a rule MEANS, in words a non-expert can act on ─────────────────────

#: The canonical plain-language gloss for every rule in the catalogs above, shared with
#: ``web/src/lib/scanFindings.ts`` (which imports this same file). Packaged — see pyproject
#: ``package-data`` — because ``personalclaw skills install`` reads it out of an installed
#: wheel, where ``web/src`` does not exist.
SCAN_RULE_GLOSS_PATH = Path(__file__).with_name("scan_rule_gloss.json")


@lru_cache(maxsize=1)
def load_scan_rule_gloss() -> dict[str, str]:
    """Every rule name mapped to one sentence about what the content can then do.

    RAISES rather than returning ``{}`` when the packaged file is missing or carries no
    rules. An empty map is indistinguishable at the call site from "no rule is glossed",
    which is the exact silence this map exists to remove — so a wheel that lost the file
    fails loudly on the refusal path instead of quietly printing bare rows again. Same
    posture as the baseline denylist: a missing packaged data file is a defect, not a
    degradation.

    Underscore keys are metadata (the file's ``_comment`` rationale — JSON cannot hold
    comments), never rules; the same convention as ``apps/token_lint_rules.json``."""
    try:
        raw = json.loads(SCAN_RULE_GLOSS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{SCAN_RULE_GLOSS_PATH} is missing or unreadable: {exc}") from exc
    gloss = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}
    if not gloss:
        raise RuntimeError(
            f"{SCAN_RULE_GLOSS_PATH} carries no rule glosses — a reachable-but-empty map "
            "would silently un-explain every finding on every consent surface"
        )
    return gloss


def rule_gloss(rule: str) -> str:
    """The sentence for ``rule``, or ``''`` for a rule this build has no gloss for.

    Returning empty rather than echoing the rule name keeps the row honest: a name
    repeated as though it were an explanation is the defect, not the fix. Mirrors
    ``ruleGloss()`` in ``web/src/lib/scanFindings.ts`` exactly — same file, same answer."""
    return load_scan_rule_gloss().get(rule, "")


def _sensitive_path_pattern() -> "re.Pattern[str]":
    """A regex matching any credential/secret path from the shared set."""
    alts = "|".join(re.escape(p) for p in _SENSITIVE_TOOL_PATTERNS)
    return re.compile(alts)


_SENSITIVE_RE = _sensitive_path_pattern()

# Shell egress tools used in the read-creds→send-out exfil idiom. (HTTP client
# libraries like requests/httpx are intentionally excluded — they're normal in
# app code; this heuristic targets the shell exfil pipeline.)
_NET_EGRESS_RE = re.compile(r"\b(?:curl|wget|fetch|nc|ncat|/dev/tcp)\b")

# A credential read and an egress call THIS many lines apart still count as the
# exfil pipeline (`C=$(cat ~/.aws/creds)` then `curl -d "$C" …`). Farther apart —
# e.g. a security test that references an address it explicitly BLOCKS, or a doc
# comment — is co-incidence, not exfil.
_EXFIL_PROXIMITY_LINES = 3


def _strip_line_comments(text: str) -> str:
    """Blank out full-line ``#``/``//`` comments so tokens that appear only in
    commentary don't trip the co-occurrence heuristics — a comment never executes.
    Conservative: only blanks lines whose first non-space char starts the comment,
    so it never touches ``${VAR#x}``, ``https://…``, or trailing inline comments.
    Line count is preserved so evidence line math stays honest."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.lstrip()
        out.append("" if (s.startswith("#") or s.startswith("//")) else line)
    return "\n".join(out)


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos)


# Zero-width + bidi-override codepoints used to hide steering text in prose.
_INVISIBLE_CHARS = {
    "​",
    "‌",
    "‍",
    "⁠",
    "﻿",  # zero-width
    "‪",
    "‫",
    "‬",
    "‭",
    "‮",  # bidi overrides
    "⁦",
    "⁧",
    "⁨",
    "⁩",  # isolates
}


_OPEN_BRACKETS = {"(": ")", "[": "]", "{": "}"}
_CLOSE_BRACKETS = frozenset(")]}")


def _unquoted_hash(line: str) -> int:
    """Index of the first ``#`` in ONE line that starts a comment, else -1. Quote-aware,
    so a ``#`` inside a string (a URL fragment, a colour escape) is not read as one."""
    quote = ""
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "#":
            return i
        i += 1
    return -1


def _drop_trailing_comment(line: str) -> str:
    """Trim a trailing ``#`` comment off ONE line of an evidence window. A lint pragma
    is commentary ABOUT the code, not the code, and inside a capped window it crowds
    out the substance. A comment-ONLY line is kept verbatim: there the comment is all
    there is to show, and it may be the matched text itself."""
    h = _unquoted_hash(line)
    if h < 0 or not line[:h].strip():
        return line
    return line[:h].rstrip()


def _construct_end(text: str, match: "re.Match[str]") -> tuple[int, bool]:
    """Where the construct the match names ends, and whether that end is a CUT.

    For a call this is the closing bracket of the argument list, so the argv travels
    with the callee; otherwise it is the end of the matched line. ``clipped`` is True
    when the walk hit its line/char budget before the bracket closed, so the caller
    marks the snippet truncated rather than passing a fragment off as the whole call."""
    line_end = text.find("\n", match.end())
    line_end = len(text) if line_end < 0 else line_end
    # The bracket the match itself opened (`exec(`), else the next non-blank char
    # (`subprocess.run` matches without its paren).
    i = match.end() - 1 if text[match.end() - 1 : match.end()] in _OPEN_BRACKETS else match.end()
    while i < len(text) and text[i] in " \t":
        i += 1
    if text[i : i + 1] not in _OPEN_BRACKETS:
        return line_end, False
    limit = min(len(text), match.end() + _EVIDENCE_SCAN_CHARS)
    lines_left = _EVIDENCE_MAX_LINES
    depth = 0
    quote = ""
    j = i
    while j < limit:
        c = text[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "#":  # skip the comment wholesale — its quotes are not code
            nl = text.find("\n", j)
            j = len(text) if nl < 0 else nl
            continue
        elif c in _OPEN_BRACKETS:
            depth += 1
        elif c in _CLOSE_BRACKETS:
            depth -= 1
            if depth <= 0:
                return j + 1, False
        elif c == "\n":
            lines_left -= 1
            if lines_left <= 0:
                return j, True
        j += 1
    return limit, True


def _evidence(text: str, match: "re.Match[str]") -> str:
    """The user-facing snippet for one finding: ``L<line>: <code>``.

    Shaped so a reader can tell WHAT the flagged code does — for a call, the callee
    AND its arguments, not just the token that matched — and where to find it. Folded
    to one line because the consent dialog renders it inline (a literal ``\\n`` in a
    security disclosure is noise, not a newline), and any cut is marked with ``…``.
    This decides only how much surrounding code is SHOWN; it never changes what
    matched or whether a rule fired."""
    line_start = text.rfind("\n", 0, match.start()) + 1
    prefix = f"L{_line_of(text, match.start()) + 1}: "
    end, clipped = _construct_end(text, match)
    head_raw = text[line_start : match.start()]
    body_raw = text[match.start() : max(end, match.end())]
    # When the match sits INSIDE a comment the comment IS the evidence, so trimming
    # comments would delete the finding; a `#` before the match on its line is the tell.
    if _unquoted_hash(head_raw) < 0:
        body_raw = "\n".join(_drop_trailing_comment(ln) for ln in body_raw.split("\n"))
    head = " ".join(head_raw.split())
    body = " ".join(body_raw.split())
    sep = " " if head and head_raw[-1:].isspace() else ""
    if len(head) > _EVIDENCE_LEAD_CAP:
        head = _ELLIPSIS + head[-(_EVIDENCE_LEAD_CAP - 1) :]
    room = _EVIDENCE_CAP - len(prefix) - len(head) - len(sep)
    if len(body) > room or clipped:
        body = body[: max(0, room - 1)].rstrip() + _ELLIPSIS
    return f"{prefix}{head}{sep}{body}".rstrip()


# ── Execution reachability — scoping the DANGEROUS band (issue #2526) ───────
#
# THE DEFECT. The DANGEROUS rules above match a string SHAPE and are blind to whether
# anything executes that string. So a bundle whose test fixture holds a real attack
# string — the string that proves its input validation refuses the attack — was judged
# identically to a bundle that runs it. DANGEROUS is terminal and non-consentable, so
# the fixture permanently blocked the install: `spec-builder` and `ops`, the two most
# security-conscious bundles in the suite, could not be installed AT ALL, while a
# bundle that never tested its validation shipped clean. The rule as written taught
# authors to delete their adversarial tests or obfuscate the strings until the scanner
# stopped recognising them — both of which make the ecosystem less safe.
#
# WHY REACHABILITY AND NOT THE TWO EASIER FIXES. #2526 offered three directions.
#
#   * "Skip test files" was rejected there on the grounds that a `test_*.py` ships
#     inside the bundle and is importable, so exempting it "would create a place to
#     park a payload". THAT OBJECTION DOES NOT APPLY HERE, and the distinction is the
#     whole basis of this code: parking a payload requires something that can execute
#     it. A string nothing can execute is not a payload — it is a string. The predicate
#     below is therefore stated on the AST and NEVER on a filename, so naming a file
#     `test_evil.py` buys an attacker exactly nothing (asserted, in the reachability
#     suite's attack table).
#   * "Downgrade a match on a test surface to WARNING" was rejected because it leans on
#     the install-consent dialog, and #2513 documents that surface as currently
#     defective (an explicit `"network": false` printed as "not declared", cron cadence
#     rendered as raw crontab, the rule never glossed). Turning a terminal block into a
#     consentable warning the user may not reliably read trades a false-positive block
#     for a real-payload pass. Reachability scoping ELIMINATES the false positive
#     instead of downgrading it: the two blocked bundles stop matching the terminal
#     band because their fixtures genuinely cannot run.
#   * "Make authors build adversarial strings at runtime" was rejected because the
#     workaround is exactly the obfuscation an attacker already uses (`"rm -" + "rf /"`
#     scores CLEAN today), so it filters honest authors and nobody else — and it
#     degrades the fixtures, whose entire job is to be read.
#
# THE RULE. A DANGEROUS match is re-scored to WARNING — never lower, never dropped —
# iff L0 holds, or ALL FIVE of L1-L5 do. Default-deny: fail a clause, or fail to
# evaluate one, and the finding stays DANGEROUS. The failure mode is a false BLOCK
# (annoying, and fixable by the author) and never a false PASS.
#
#   L0  COMMENTARY, NOT CODE (issue #2625). Every match of this rule lies wholly inside a
#       ``COMMENT`` token or a docstring. That is decided from :mod:`tokenize` and the
#       AST, never from the text — see the note below on why that distinction is the
#       whole basis of this clause. L0 short-circuits: it does not consult L2-L5, because
#       those clauses all ask whether a STRING THE FILE HOLDS can be handed to an
#       interpreter, and a comment is not a string the file holds. The tokeniser throws
#       it away before the compiler ever sees it, so no sink, no import edge and no
#       top-level call in the bundle can reach it.
#   L1  LITERAL, NOT CODE. The file parses as Python and every match of this rule that
#       the scanner could have reported lies wholly inside a ``str`` constant (or, per
#       L0, inside commentary — so documenting an inert fixture next to it does not
#       revoke the fixture's own downgrade). A `.sh` file is never eligible: its text IS
#       its program. Decided on AST spans, not on text.
#   L2  NO EXECUTION SINK IN THE FILE. The file contains no sink that could receive a
#       string: no ``os.system``/``popen``/``exec*``/``spawn*``/``fork``, no
#       ``eval``/``exec``/``compile``/``__import__``, no ``subprocess`` call whose
#       ``shell`` is anything but a literal ``False``, and every spawn site's argv[0] is
#       a literal program name that is not a shell, not an interpreter and not a path.
#       This is the clause that does the work, and it is deliberately stated at MODULE
#       scope rather than on the literal's argument position. "Is this literal passed to
#       a sink?" is defeated by two lines (`c = "rm -rf / " + x; os.system(c)`), so the
#       question asked is the answerable one: can this file hand ANY string to an
#       interpreter? If it can, the literal is treated as reachable.
#   L3  NO EXPORT. Nothing else in the bundle can lift the literal out: no other module
#       imports it, no sibling names its file or module stem in a string, no script or
#       config in the bundle mentions it, and ``app.json`` does not declare it. Prose is
#       deliberately excluded — a README saying "run pytest test_provider.py" is
#       documentation, and counting it would make L3 unsatisfiable for every bundle that
#       documents its own test command.
#   L4  THE GRAPH IS TRUSTWORTHY. L3 is a static claim, so it is void if the bundle can
#       rewrite its own import graph at runtime. Any ``eval``/``exec``/``compile``/
#       ``__import__``/``importlib``/``runpy``/``pickle``/``marshal``/``ctypes``, any
#       ``getattr`` with a computed name, any ``import *``, any ``sys.path`` mutation —
#       or any Python/loader file the walk could not read — ANYWHERE in the bundle
#       revokes the downgrade for the whole bundle.
#   L5  IMPORTING IT IS A NO-OP. Unreachable is not never-imported: a test runner, or a
#       curious user, may import the module. Its top level must contain no call outside
#       a small pure allowlist, so the payload cannot fire on import.
#
# WHY L0 IS ON THE TOKENISER AND NOT ON THE TEXT (issue #2625). "Skip lines that start
# with ``#``" is a one-liner and it is the wrong fix: it is defeated by anything that
# merely LOOKS like a comment, which is exactly the payload parking spot #2526 refused to
# create. ``X = "#"`` opens a string, and every ``#`` to the end of that string is inside
# a ``str`` token, not a comment — a text scan reading the next line as commentary would
# hand an attacker a way to hide live code behind a fake one. A tokeniser cannot be fooled
# that way: it reports the token class the compiler will actually see, so a line that
# tokenises as code is code no matter what it looks like. The scanner already had one
# available. (:func:`_strip_line_comments` and :func:`_on_comment_only_line` remain text
# heuristics, deliberately: they only ever NARROW what the ``exfil_sensitive_path`` rule
# may match, so being fooled there costs recall on a rule, never the floor.)
#
# RESIDUAL, STATED NOT HIDDEN. Two shapes, both narrower than the rule they replace.
#
#   * A bundle can ship a module of pure inert data that nothing references and get its
#     literal disclosed-and-consentable instead of refused. That is a real reduction in
#     the floor for that one shape. The counterweight is measured, not asserted: the
#     shipped band already scores ``"rm -" + "rf /"`` as CLEAN and ``os.system("rm -rf
#     /")`` as WARNING, so the capability given up is refusing the attacker who wrote
#     their payload as a plain literal AND left it unreachable AND did not obfuscate it.
#   * Under L0, a bundle that performs STRING SURGERY ON ITS OWN SOURCE — reads a sibling
#     file, strips the ``#``, and ``exec``s the result — could promote a comment back to
#     code, and L0 does not consult L4's bundle-wide graph-trust check to stop it. L4
#     exists to protect L3's static "nothing imports it" claim, and L0 makes no such
#     claim, so extending it here would refuse the honest author for a threat that does
#     not need a comment: a bundle that can ``exec`` arbitrary text can spell the payload
#     itself, and its ``exec`` is separately flagged. It is a real hole all the same, and
#     it is the reason L0 is stated on a token class rather than on a filename or a line.
#
# Either way the finding survives: rule id, path, evidence and a required click, all
# unchanged. See ``tests/security/test_scanner_reachability.py``.

#: argv[0] values that make the spawned process an interpreter OF ITS ARGUMENTS. A
#: string handed to any of these becomes code, so a file with such a spawn site can run
#: any literal it holds. The shell names are the same family the ``remote_exec_pipe`` /
#: ``obfuscated_exec`` rules above already recognise (``(?:ba|z|da)?sh``); a rail in the
#: reachability suite pins this set against the pattern catalog so the two cannot drift.
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

#: Attribute names that execute a string (or a program) without going through
#: ``subprocess``. ``os.system`` and ``os.popen`` are what the ``python_exec`` rule
#: above already names; the ``exec*``/``spawn*``/``fork`` family is the same capability
#: spelled differently, and default-deny means the whole family counts.
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
#: ``eval``/``exec`` are what the ``eval_exec`` and ``python_exec`` rules name; ``compile``
#: and ``__import__`` are the same capability one call earlier.
_DYNAMIC_BUILTINS = frozenset({"eval", "exec", "compile", "__import__"})

#: Modules whose mere presence means the import graph, or a byte string, can become code.
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

#: Call prefixes that spawn a process. ``asyncio.create_subprocess_shell`` takes a
#: command STRING and is separately fatal, which the shell check below catches.
_SPAWN_FUNCS = ("subprocess.", "asyncio.create_subprocess_")

#: ``subprocess`` names that are exceptions or constants, not spawns — ``ops``'s test
#: module references ``subprocess.TimeoutExpired`` and spawns nothing, and calling that
#: an execution sink would be false.
_SUBPROCESS_NON_SPAWN = frozenset(
    {"TimeoutExpired", "CalledProcessError", "SubprocessError", "PIPE", "STDOUT", "DEVNULL"}
)

#: Non-Python surfaces that can LOAD OR RUN a file, and so count as a reference under L3.
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

#: A re-scored finding never lands below this. WARNING keeps it on the consent surface
#: with rule, path and evidence intact; the trust tier then modulates it exactly as it
#: modulates any other warning (no special case, and no path to CLEAN).
_REACH_FLOOR = Verdict.WARNING

#: The states that earn :data:`_REACH_FLOOR`. Both are POSITIVE PROOFS of inertness and
#: neither is a "we could not tell" — those land on REACHABLE or UNPARSEABLE and keep the
#: terminal severity. Listed once so the two proofs cannot drift apart in severity.
_INERT_STATES = frozenset({Reachability.UNREACHABLE, Reachability.COMMENTARY})


@dataclass
class _FileFacts:
    """What one Python file's AST says about executing strings and reaching modules."""

    parsed: bool
    str_spans: list[tuple[int, int]]  # (start, end) text offsets of every ``str`` constant
    #: L0 evidence: text offsets of every region the interpreter discards — each
    #: ``COMMENT`` token, and each module/class/function docstring.
    commentary_spans: list[tuple[int, int]]
    imports: set[str]
    string_literals: set[str]
    dynamic: set[str]  # L4 evidence: names that make the import graph untrustworthy
    sinks: set[str]  # L2 evidence: sites in THIS file that could run a string
    top_level_calls: set[str]  # L5 evidence: what importing this module would call


def _offset_table(text: str) -> list[int]:
    """Line-start offsets, so an AST or :mod:`tokenize` ``(row, col)`` becomes a flat
    offset.

    Split on the UNIVERSAL NEWLINES CPython itself recognises — ``\\n``, ``\\r\\n``,
    ``\\r`` — and nothing else. Deliberately not :meth:`str.splitlines`, which also breaks
    on ``\\f`` and ``\\u2028``: CPython's tokeniser treats those as ordinary characters, so
    a file containing one would produce a table that disagreed with the row numbers being
    looked up in it, by an amount the file's author chooses. That is fine for a heuristic
    and not fine for a table a span check stands on.
    """
    starts = [0]
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\r":
            i += 2 if text[i + 1 : i + 2] == "\n" else 1
        elif c == "\n":
            i += 1
        else:
            i += 1
            continue
        starts.append(i)
    return starts


def _docstring_nodes(tree: ast.AST) -> Iterator[ast.Constant]:
    """Every module/class/function docstring node — the ``str`` the compiler lifts into
    ``__doc__`` and never evaluates as an expression."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            yield first.value


def _commentary_spans(text: str, tree: ast.AST) -> list[tuple[int, int]] | None:
    """L0: text offsets of every region of ``text`` the interpreter throws away — each
    ``COMMENT`` token, and each docstring — or ``None`` when that cannot be DETERMINED.

    ``None`` is not "no comments". It means the file did not tokenise, or a span the
    lexer reported did not match the bytes it claims to cover, and the caller must then
    treat the file as unanalysable: the entire claim L0 makes is that the determination is
    structural, so a file whose structure could not be read gets no benefit from it.

    Every span is verified against the token's own text before it is trusted, so a line
    table that drifted from the lexer for any reason revokes the whole answer rather than
    silently pointing a "this is a comment" span at some other part of the file.
    """
    starts = _offset_table(text)

    def span(tok: tokenize.TokenInfo) -> tuple[int, int] | None:
        try:
            start = starts[tok.start[0] - 1] + tok.start[1]
            end = starts[tok.end[0] - 1] + tok.end[1]
        except IndexError:
            return None
        return (start, end) if text[start:end] == tok.string else None

    spans: list[tuple[int, int]] = []
    strings: dict[int, list[tokenize.TokenInfo]] = {}
    try:
        # ``newline=""`` so readline splits on the same universal newlines as the table
        # above and translates nothing — the offsets must address the ORIGINAL bytes.
        for tok in tokenize.generate_tokens(io.StringIO(text, newline="").readline):
            if tok.type == tokenize.COMMENT:
                got = span(tok)
                if got is None:
                    return None
                spans.append(got)
            elif tok.type == tokenize.STRING:
                strings.setdefault(tok.start[0], []).append(tok)
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None
    # A docstring is a STRING token the AST says is the first statement of a scope. Matched
    # by ROW and then verified by VALUE, because the AST's column offsets are UTF-8 byte
    # counts while the lexer's are character counts; a row with two string tokens on it, or
    # a value that does not round-trip, claims nothing rather than guessing.
    for node in _docstring_nodes(tree):
        row = strings.get(node.lineno, [])
        if len(row) != 1:
            continue
        try:
            if ast.literal_eval(row[0].string) != node.value:
                continue
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        got = span(row[0])
        if got is not None:
            spans.append(got)
    return spans


def _within(regions: Iterable[tuple[int, int]], probes: Iterable[tuple[int, int]]) -> bool:
    """True when EVERY probe span lies wholly inside at least one region. Vacuously true
    for no probes, which is why every caller checks it has probes first."""
    regions = list(regions)
    return all(any(s <= start and end <= e for s, e in regions) for start, end in probes)


def _argv0_of(call: ast.Call) -> tuple[bool, str | None]:
    """``(pinned, program)`` for a spawn site's argv[0].

    ``pinned`` is False whenever the program name is not a plain string literal — the
    default-deny answer, because an argv assembled at runtime can name anything. A bare
    command STRING rather than a list is never pinned either: that form is only ever run
    through a shell."""
    if not call.args:
        return False, None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return False, first.value
    if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
        head = first.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return True, head.value
    return False, None


def _spawn_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """``(prefixes, bare_names)`` under which this file can reach a spawn API.

    An import alias must not be a way out: ``import subprocess as sp`` and
    ``from subprocess import run`` are the same capability as ``subprocess.run``, spelled
    so a prefix match on the literal text ``"subprocess."`` would miss it. Collected in
    its own pass so the answer does not depend on AST walk order.
    """
    prefixes: set[str] = set(_SPAWN_FUNCS)
    bare: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "subprocess" and alias.asname:
                    prefixes.add(f"{alias.asname}.")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in {"subprocess", "asyncio"}:
                continue
            for alias in node.names:
                if alias.name in _SUBPROCESS_NON_SPAWN:
                    continue
                if root == "asyncio" and not alias.name.startswith("create_subprocess_"):
                    continue
                bare.add(alias.asname or alias.name)
    return prefixes, bare


def _note_call(
    node: ast.Call, facts: _FileFacts, spawn_prefixes: set[str], spawn_bare: set[str]
) -> None:
    """Record what one call site means for L2 (can it run a string?) and L4."""
    name = ast.unparse(node.func)
    if name == "getattr" and (len(node.args) < 2 or not isinstance(node.args[1], ast.Constant)):
        facts.dynamic.add("getattr with a computed name")
    # A bare call to an execution primitive — `from os import system; system(cmd)` — is
    # the same sink as `os.system`, and reaches it without ever forming an Attribute.
    if isinstance(node.func, ast.Name) and node.func.id in _EXEC_ATTRS:
        facts.sinks.add(f"{node.func.id}()")
        return
    if name.startswith("subprocess.") and name.split(".")[-1] in _SUBPROCESS_NON_SPAWN:
        return
    if not (name.startswith(tuple(sorted(spawn_prefixes))) or name in spawn_bare):
        return
    if name.endswith("create_subprocess_shell"):
        facts.sinks.add(name)
        return
    shell = next((kw.value for kw in node.keywords if kw.arg == "shell"), None)
    if shell is not None and not (isinstance(shell, ast.Constant) and shell.value is False):
        facts.sinks.add(f"{name}(shell=…)")
        return
    pinned, program = _argv0_of(node)
    if not pinned:
        facts.sinks.add(f"{name} with an argv the source does not pin")
        return
    leaf = (program or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "/" in (program or "") or leaf in _SHELL_ARGV0 or leaf.endswith((".sh", ".bash", ".py")):
        facts.sinks.add(f"{name} spawning {program!r}")


def _analyse_python(text: str) -> _FileFacts:
    """AST facts for one Python file. An unparseable file returns ``parsed=False`` and
    nothing else — the caller must treat that as its own outcome, never as "no sinks"."""
    unreadable = _FileFacts(False, [], [], set(), set(), set(), set(), set())
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):  # ValueError: NUL bytes / oversized literals
        return unreadable
    commentary = _commentary_spans(text, tree)
    if commentary is None:
        # It parsed but would not tokenise, or the lexer and the line table disagreed.
        # Same answer as a parse failure: the structure could not be read (L0/L1).
        return unreadable
    facts = _FileFacts(True, [], commentary, set(), set(), set(), set(), set())

    starts = _offset_table(text)
    spawn_prefixes, spawn_bare = _spawn_names(tree)

    def off(lineno: int, col: int) -> int:
        return starts[lineno - 1] + col

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            facts.string_literals.add(node.value)
            if node.end_lineno is not None and node.end_col_offset is not None:
                facts.str_spans.append(
                    (off(node.lineno, node.col_offset), off(node.end_lineno, node.end_col_offset))
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                facts.imports.update({root, alias.name})
                if root in _DYNAMIC_MODULES:
                    facts.dynamic.add(f"imports {root}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            facts.imports.add(root)
            for alias in node.names:
                facts.imports.add(alias.name)
                if alias.name == "*":
                    facts.dynamic.add("star import")
            if root in _DYNAMIC_MODULES:
                facts.dynamic.add(f"imports {root}")
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC_BUILTINS:
            facts.dynamic.add(f"{node.id}()")
            facts.sinks.add(f"{node.id}()")
        elif isinstance(node, ast.Attribute):
            if node.attr in _EXEC_ATTRS:
                facts.sinks.add(f".{node.attr}")
            if node.attr == "path" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                # `sys.path.insert(...)` makes any static import resolution a guess.
                facts.dynamic.add("sys.path mutation")
        if isinstance(node, ast.Call):
            _note_call(node, facts, spawn_prefixes, spawn_bare)

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


def _is_loader_name(name: str) -> bool:
    """True for a non-Python file that could load or run code (and so counts under L3)."""
    return Path(name).suffix.lower() in _LOADER_SUFFIXES or name in _LOADER_NAMES


def _manifest_tokens(manifest_text: str | None) -> set[str]:
    """Every module-ish token ``app.json`` names, so a DECLARED entry point is never
    mistaken for an unreferenced file just because no ``import`` statement mentions it.
    An unreadable manifest yields ``{"*"}`` — assume every file is named (default-deny)."""
    if manifest_text is None:
        return set()
    try:
        data: Any = json.loads(manifest_text)
    except ValueError:
        return {"*"}
    out: set[str] = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_./-]*", json.dumps(data)):
        out.update({token, token.split(":")[0], Path(token).stem})
    return out


class _BundleReach:
    """The per-bundle reachability analysis: built once from the texts the scan already
    read, then queried per DANGEROUS finding.

    Built from the walk's own bytes rather than by re-reading the tree, so the analysis
    answers for exactly the content the scanner judged — there is no second read for a
    payload to change under.
    """

    def __init__(
        self,
        *,
        py_texts: dict[str, str],
        loader_blobs: Iterable[str],
        manifest_text: str | None,
        opaque: Iterable[str],
    ) -> None:
        self._py_texts = py_texts
        self._facts = {rel: _analyse_python(text) for rel, text in py_texts.items()}
        self._loader_blob = "\n".join(loader_blobs)
        self._manifest = _manifest_tokens(manifest_text)
        # L4: a Python or loader file the walk could not read (oversize, unreadable) is a
        # hole in the graph, not an absence of edges.
        self._untrustworthy: str | None = next(
            (f"{rel}: too large or unreadable to analyse" for rel in sorted(opaque)), None
        )
        if self._untrustworthy is None:
            self._untrustworthy = next(
                (
                    f"{rel}: {sorted(f.dynamic)[0]}"
                    for rel, f in sorted(self._facts.items())
                    if f.dynamic
                ),
                None,
            )

    # ── L3 ──

    def _referenced_elsewhere(self, rel: str) -> str | None:
        stem = Path(rel).stem
        name = Path(rel).name
        for other, facts in sorted(self._facts.items()):
            if other == rel:
                continue
            if stem in facts.imports:
                return f"{other} imports it"
            if stem in facts.string_literals or rel in facts.string_literals:
                return f"{other} names it in a string"
            if name in facts.string_literals:
                return f"{other} names it in a string"
        if "*" in self._manifest:
            return "app.json is unreadable, so every file is assumed declared"
        if stem in self._manifest:
            return "app.json names it"
        if name in self._loader_blob or f"{stem} " in self._loader_blob:
            return "a script or config in the bundle names it"
        return None

    # ── the decision ──

    def decide(self, finding: Finding) -> tuple[Reachability, str]:
        """L0-L5 for one DANGEROUS finding, plus the sentence that justifies it."""
        if finding.rule in _NATIVE_DESTRUCTION_RULES:
            # The native family (#2607) is decided on CALL SITES, so L0 and L1 answer
            # themselves and the clauses below have nothing left to weigh. Commentary never
            # gets here at all: `ast.parse` discards a comment and never yields a Call for a
            # docstring, so the same text commented out produces NO finding rather than a
            # re-scored one — strictly stricter than the WARNING a regex match would earn.
            # And a call that does exist is code, never a string the bundle merely holds, so
            # no clause below could lower it. Said out loud here rather than left to
            # `_rule_spans` finding no regex to re-run, which would report "no locatable
            # span" — the right answer for the wrong reason, on a disclosure a reviewer reads.
            return (
                Reachability.REACHABLE,
                "match is a call site resolved from the AST, not a string the bundle "
                "merely holds — a call is code (L1)",
            )
        rel = finding.path
        if not rel.endswith(".py"):
            return Reachability.REACHABLE, "not Python — a script's text is its program (L1)"
        facts = self._facts.get(rel)
        text = self._py_texts.get(rel)
        if facts is None or text is None:
            return Reachability.REACHABLE, "file was not read as Python (L1)"
        if not facts.parsed:
            return Reachability.UNPARSEABLE, "file does not parse as Python (L1)"
        spans = _rule_spans(text, finding.rule)
        if not spans:
            # No span means the pass cannot say WHERE the objection is, so it declines to
            # have an opinion. The invisible-character rules land here by construction:
            # a bidi override is a rendering attack, not a string a bundle merely holds.
            return Reachability.REACHABLE, "rule has no locatable span (L1)"
        if _within(facts.commentary_spans, spans):
            # L0 is decided on the token class, so it needs nothing from L2-L5: a comment
            # is not a string this file holds, and the tokeniser discards it before the
            # compiler sees it. A file that merely LOOKS like it comments the match out
            # never lands here — the lexer, not the text, said this is commentary.
            return (
                Reachability.COMMENTARY,
                "every match is inside a comment or docstring, which the interpreter "
                "discards at tokenisation — not code (L0)",
            )
        if not _within([*facts.str_spans, *facts.commentary_spans], spans):
            return Reachability.REACHABLE, "match is code, not a string literal (L1)"
        if facts.sinks:
            return (
                Reachability.REACHABLE,
                f"file can execute a string: {sorted(facts.sinks)[0]} (L2)",
            )
        exported = self._referenced_elsewhere(rel)
        if exported:
            return Reachability.REACHABLE, f"literal is reachable — {exported} (L3)"
        if self._untrustworthy:
            return (
                Reachability.REACHABLE,
                f"bundle can rewrite its own graph — {self._untrustworthy} (L4)",
            )
        impure = sorted(facts.top_level_calls - _INERT_TOP_LEVEL_CALLS)
        if impure:
            return Reachability.REACHABLE, f"module runs {impure[0]} on import (L5)"
        return (
            Reachability.UNREACHABLE,
            "inert literal: not code, this file cannot execute a string, nothing in the "
            "bundle reaches it, and importing it runs nothing (L1-L5)",
        )


def _rule_spans(text: str, rule: str) -> list[tuple[int, int]]:
    """Every span in ``text`` this rule could have reported, re-derived by re-running the
    rule's own regex.

    ALL of them, not just the one the finding names. ``_scan_script`` reports the first
    match per rule, but a second match of the same rule sitting in CODE means the file
    really can execute that shape — so L1 must see every candidate or it would clear a
    file on the strength of its most innocent occurrence.

    ``exfil_sensitive_path`` is derived from two regexes over comment-stripped text. The
    span that matters for "is this a literal" is the credential path, located here in the
    RAW text so the offsets line up with the AST; matches on a full-line comment are
    dropped because ``_scan_script`` strips those before the rule can see them, so they
    are not candidates for this finding at all.
    """
    if rule == "exfil_sensitive_path":
        return [
            (m.start(), m.end())
            for m in _SENSITIVE_RE.finditer(text)
            if not _on_comment_only_line(text, m.start())
        ]
    for name, pattern in _DANGEROUS_SCRIPT:
        if name == rule:
            return [(m.start(), m.end()) for m in pattern.finditer(text)]
    return []


def _on_comment_only_line(text: str, pos: int) -> bool:
    """True when ``pos`` sits on a line whose first non-space char opens a comment — the
    same predicate :func:`_strip_line_comments` uses, evaluated on raw offsets."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    stripped = text[start : len(text) if end < 0 else end].lstrip()
    return stripped.startswith("#") or stripped.startswith("//")


def _scope_by_reachability(
    findings: list[Finding],
    *,
    py_texts: dict[str, str],
    loader_blobs: list[str],
    manifest_text: str | None,
    opaque: list[str],
) -> list[Finding]:
    """Annotate every DANGEROUS script finding with its execution reachability, and
    re-score the provably-inert ones — :data:`_INERT_STATES` — to :data:`_REACH_FLOOR`
    (WARNING).

    Order and count are preserved exactly; rule, path, surface and evidence are never
    touched. Nothing is ever raised and nothing is ever dropped — the only edit is
    DANGEROUS → WARNING on positive proof, which keeps the finding on the consent surface.

    The analysis is built only when there is a DANGEROUS script finding to ask about, so a
    clean bundle pays nothing, and once per scan rather than once per finding.
    """
    if not any(f.severity is Verdict.DANGEROUS and f.surface == "script" for f in findings):
        return findings
    reach = _BundleReach(
        py_texts=py_texts,
        loader_blobs=loader_blobs,
        manifest_text=manifest_text,
        opaque=opaque,
    )
    out: list[Finding] = []
    for finding in findings:
        if finding.severity is not Verdict.DANGEROUS or finding.surface != "script":
            out.append(finding)
            continue
        state, reason = reach.decide(finding)
        severity = _REACH_FLOOR if state in _INERT_STATES else finding.severity
        out.append(
            replace(finding, severity=severity, reachability=state, reachability_reason=reason)
        )
    return out


# ── Native destruction — the DANGEROUS band spelled in the host language (#2607) ──
#
# THE DEFECT. Every rule in `_DANGEROUS_SCRIPT` is a SHELL string: `rm -rf /`, the fork bomb,
# `mkfs`/`dd`, `curl|sh`, `base64 -d|sh`. The terminal tier assumed malice arrives as a shell
# command, so the same destruction expressed in Python — the language the bundle is written
# in — was not merely un-refused, it was CLEAN. Measured on `main`, at the community tier,
# each in its own one-file bundle: `shutil.rmtree(os.path.expanduser("~"))`, `shutil.rmtree(
# "/")`, a `Path("/").rglob("*")` loop calling `unlink`, and `open("/etc/hosts", "w")` all
# scored `clean` with ZERO findings, while `rm -rf /` in a `.sh` next to them was terminal.
# A bundle that deletes the user's home directory installed without a single finding.
#
# THE TIER IS THE OWNER'S RULING, NOT AN INFERENCE. #2607 direction 3 left the severity open
# ("that is a judgement call about the floor and belongs to the owner"). The ruling is that
# native destruction gets the SAME TERMINAL severity as shell destruction, so these rules sit
# in the DANGEROUS band and the floor in `_aggregate` applies to them unchanged — no tier,
# builtin included, downgrades one.
#
# WHY THE SEVERITY TURNS ON THE TARGET AND NOT ON THE CALL. `shutil.rmtree` is a legitimate
# call; cleanup code uses it constantly, and every one of the 64 shipped bundles that calls it
# passes a VARIABLE holding a staging directory. Refusing the call would refuse them all. So
# the claim these rules make is the one the shell band already makes: `rm -rf /tmp/build` is
# not terminal and `rm -rf /` is, and the difference is the target. `_classify_path_literal`
# is that line, and it is drawn to the SAME vocabulary — `/`, `~`, `$HOME` — plus the
# OS-owned trees whose destruction is the same claim (`/etc`, `/usr`, `/System`, …), minus the
# temp roots and character devices that are written every day (`/tmp`, `/var/folders`,
# `/dev/null`). A target the analysis cannot resolve to one of those is NOT flagged: that is a
# recall limit, stated here rather than papered over, and it is what keeps the floor off the
# shipped corpus (measured: 64 bundles, 0 newly DANGEROUS).
#
# WHY THE AST AND NOT A REGEX. This repo has been bitten repeatedly by text scans that match a
# MENTION rather than a call — including a grep that matched a docstring denying the symbol.
# `shutil.rmtree` appears in prose, in a comment warning against it, in a test asserting it is
# never called, and in a denylist of forbidden writers (`design-critique/test_provider.py`
# ships exactly such a list). A regex cannot tell those from a call; `ast` does not have to
# try. So the rule is stated on call targets, resolved through import aliases, and a file that
# does not parse yields NOTHING here rather than a guess.
#
# HOW THIS MEETS THE REACHABILITY SCOPING (#2605, #2625) RATHER THAN GOING AROUND IT. Every
# finding below is `surface="script"` and DANGEROUS, so `_scope_by_reachability` annotates it
# like any other. `_BundleReach.decide` answers REACHABLE for the whole family, and that is
# the only honest answer: L0 and L1 ask whether the match is commentary or a string the file
# merely holds, and a call site is neither. The two directions matter separately —
#   * a commented-out or docstringed destruction produces NO finding at all, which is STRICTER
#     than the WARNING a re-scored regex match would earn: `ast.parse` discards commentary
#     before the rule can see it, so there is nothing to re-score;
#   * a live call is code, so no clause can lower it, and it stays terminal.
# That asymmetry is the #2605 principle applied, not evaded: a commented-out removal really is
# strictly less executable than a live one, and here the gap is the whole finding.

#: Whole-ACCOUNT targets, spelled as a path literal. `$HOME` is here for the same reason
#: `destructive_root` names it: it is how the target is written, and `os.path.expandvars`
#: turns it into the real thing. There is deliberately NO matching set for the filesystem
#: root: every spelling of it (`/`, `//`, `/.`, `/./`, `/etc/..`) normalises to `/` inside
#: `_classify_path_literal`, so a literal set would be dead code — a mutation emptying it
#: survived the whole suite, which is exactly how that was found.
_HOME_LITERALS = frozenset({"~", "~/", "~/.", "$HOME", "${HOME}", "%USERPROFILE%"})
#: Environment keys that name the account root.
_HOME_ENV_KEYS = frozenset({"HOME", "USERPROFILE"})
#: A bare Windows drive root (`C:`, `C:\`, `C:/`) — the one root spelling POSIX
#: normalisation cannot reach, because it does not start with `/`.
_DRIVE_ROOT_RE = re.compile(r"[A-Za-z]:[\\/]{0,2}")

#: OS-owned trees. Destroying one of these is the same claim as destroying `/` — the machine
#: does not boot, or the account does not log in — so they earn the same band. Deliberately
#: NOT "every absolute path": `/data/cache` and `/srv/../opt/x` resolve to ordinary places, and
#: a bundle writing under one is doing its job.
_SYSTEM_TREES: tuple[str, ...] = (
    "/Applications",
    "/Library",
    "/System",
    "/Users",
    "/Volumes",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib64",
    "/opt",
    "/private/etc",
    "/private/var",
    "/proc",
    "/root",
    "/sbin",
    "/srv",
    "/sys",
    "/usr",
    "/var",
)
#: Carved back OUT of the trees above, by longest match. These are the paths ordinary code
#: deletes and truncates all day — the scratch roots, and the character devices where a write
#: destroys nothing. Precision lives here: without `/dev/null` every `open("/dev/null", "w")`
#: would be terminal, and `/var/folders` is where macOS puts `TemporaryDirectory()`.
_TRANSIENT_PATHS: tuple[str, ...] = (
    "/dev/fd",
    "/dev/null",
    "/dev/random",
    "/dev/shm",
    "/dev/stderr",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/tty",
    "/dev/urandom",
    "/dev/zero",
    "/private/tmp",
    "/private/var/folders",
    "/private/var/tmp",
    "/tmp",
    "/var/folders",
    "/var/tmp",
)

#: Calls that hand their first argument through unchanged as a path, so the target is one
#: level in. `os.path.expanduser`/`expandvars` are here rather than special-cased: they take
#: the literal that names the target and the classifier already knows `~` and `$HOME`.
_PATH_PASSTHROUGH = frozenset(
    {
        "os.fspath",
        "os.path.abspath",
        "os.path.expanduser",
        "os.path.expandvars",
        "os.path.normpath",
        "os.path.realpath",
        "pathlib.Path",
        "pathlib.PosixPath",
        "pathlib.PurePath",
        "pathlib.PurePosixPath",
        "str",
    }
)
#: Path METHODS that hand the receiver through unchanged. `.parent` is deliberately absent —
#: it CHANGES the path, and guessing which way is worse than not guessing.
_PATH_PASSTHROUGH_METHODS = frozenset({"absolute", "expanduser", "resolve"})
#: Calls that answer with the account root, or read it out of the environment.
_HOME_FUNCS = frozenset({"pathlib.Path.home", "pathlib.PurePath.home", "Path.home"})
_ENV_READ_FUNCS = frozenset({"os.environ.get", "os.getenv"})

#: Module-level functions that REMOVE a path, keyed by their canonical dotted name so an
#: import alias (`import shutil as sh`, `from shutil import rmtree`) is not a way out.
_DELETE_FUNCS = frozenset({"os.remove", "os.removedirs", "os.rmdir", "os.unlink", "shutil.rmtree"})
#: Path METHODS that remove. Matched on the attribute name alone, because inside
#: `for p in Path("/").rglob("*")` there is no literal to resolve and `p` is whatever the walk
#: yielded. `remove` is deliberately ABSENT: `list.remove` is not a filesystem call, and
#: including it would read every list mutation inside a loop as a deletion.
_DELETE_METHODS = frozenset({"removedirs", "rmdir", "rmtree", "unlink"})

#: Calls that open a file for writing (so a `mode` argument decides truncation) and the
#: methods/functions that truncate outright.
_OPEN_FUNCS = frozenset({"codecs.open", "io.open", "open"})
_TRUNCATE_FUNCS = frozenset({"os.truncate"})
_TRUNCATE_METHODS = frozenset({"write_bytes", "write_text"})

#: Walks that enumerate a whole subtree. `glob`'s recursive form needs `recursive=True` AND a
#: `**` in the pattern to be unbounded, so both are checked rather than assumed.
_WALK_FUNCS = frozenset({"os.fwalk", "os.walk"})
_WALK_METHODS = frozenset({"rglob", "walk"})
_GLOB_FUNCS = frozenset({"glob.glob", "glob.iglob"})

#: How far the target resolver will follow wrappers and single-assignment aliases. A bound,
#: not a judgement: a chain longer than this is not a shape anyone writes, and an unbounded
#: walk over a hostile AST is a denial of service on the install path.
_TARGET_DEPTH = 6


def _longest_root(path: str, roots: tuple[str, ...]) -> str | None:
    """The longest entry of ``roots`` that ``path`` equals or lies under, else ``None``.

    Compared on path SEGMENTS (``/etc`` matches ``/etc`` and ``/etc/hosts``, never
    ``/etcetera``), and longest-wins so a carve-out can be nested inside a tree —
    ``/var/folders`` has to be able to beat ``/var``."""
    hit: str | None = None
    for root in roots:
        if (path == root or path.startswith(f"{root}/")) and (hit is None or len(root) > len(hit)):
            hit = root
    return hit


def _classify_path_literal(raw: str) -> str | None:
    """``"root"`` | ``"home"`` | ``"system"`` for a destructive target, else ``None``.

    THE PRECISION LINE for the whole native family, and the same line the shell band already
    draws. ``..`` segments are resolved lexically rather than refused, because ``/etc/..``
    IS the root and a rule that could not see that would be defeated by two characters."""
    if raw in _HOME_LITERALS:
        return "home"
    if _DRIVE_ROOT_RE.fullmatch(raw):
        return "root"
    if not raw.startswith("/"):
        return None
    parts: list[str] = []
    for segment in raw.split("/"):
        if not segment or segment == ".":
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    norm = "/" + "/".join(parts)
    if norm == "/":
        return "root"
    transient = _longest_root(norm, _TRANSIENT_PATHS)
    system = _longest_root(norm, _SYSTEM_TREES)
    if transient is not None and (system is None or len(transient) >= len(system)):
        return None
    return "system" if system is not None else None


def _dotted_name(node: ast.expr) -> str | None:
    """The dotted name a ``Name``/``Attribute`` chain spells, or ``None`` for anything else.

    Built by hand rather than with :func:`ast.unparse` so the answer is ``None`` — not a
    string that merely looks like a name — for `f().attr`, a subscript, or a literal."""
    parts: list[str] = []
    cursor: ast.expr = node
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if not isinstance(cursor, ast.Name):
        return None
    parts.append(cursor.id)
    return ".".join(reversed(parts))


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """Every local name mapped to the canonical dotted path it refers to.

    ``import shutil as sh`` → ``{"sh": "shutil"}``; ``from shutil import rmtree as rt`` →
    ``{"rt": "shutil.rmtree"}``. Without this the rules would read ``shutil.rmtree`` and
    miss ``rt``, which is one line of evasion. Relative imports are skipped: a
    bundle-local module is not the stdlib no matter what it re-exports, and pretending to
    resolve it would put a name in the map that means something else."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                out[alias.asname or root] = alias.name if alias.asname else root
        elif isinstance(node, ast.ImportFrom) and not node.level:
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                out[alias.asname or alias.name] = f"{module}.{alias.name}" if module else alias.name
    return out


def _target_names(node: ast.expr) -> Iterator[str]:
    """Every name an assignment target binds, through tuple/list/star unpacking."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, ast.Starred):
        yield from _target_names(node.value)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _target_names(element)


def _bound_names(node: ast.AST) -> Iterator[str]:
    """Every name ONE node binds, by any form Python has for binding one.

    Exhaustive on purpose: this feeds the "bound exactly once" count, and a binding form
    left out of it would let a name that really does change be resolved as if it could not."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            yield from _target_names(target)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        yield from _target_names(node.target)
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        yield from _target_names(node.target)
    elif isinstance(node, ast.withitem):
        if node.optional_vars is not None:
            yield from _target_names(node.optional_vars)
    elif isinstance(node, ast.Delete):
        for target in node.targets:
            yield from _target_names(target)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            yield alias.asname or alias.name.split(".")[0]
    elif isinstance(node, ast.ExceptHandler):
        if node.name:
            yield node.name
    elif isinstance(node, (ast.Global, ast.Nonlocal)):
        yield from node.names
    elif isinstance(node, ast.arguments):
        for arg in (*node.posonlyargs, *node.args, *node.kwonlyargs):
            yield arg.arg
        for maybe in (node.vararg, node.kwarg):
            if maybe is not None:
                yield maybe.arg


def _single_bindings(tree: ast.AST) -> dict[str, ast.expr]:
    """Names bound EXACTLY ONCE in the file, mapped to the expression bound to them.

    The only dataflow this family does, and the weakest claim that closes the obvious
    evasion: ``home = os.path.expanduser("~")`` then ``shutil.rmtree(home)`` is the same
    payload as the one-liner, and a rule that reads only the one-liner is defeated by
    pressing Enter. So a name is resolved only when the whole file binds it once — a second
    assignment, an augmented assignment, a loop target, a `with … as`, a comprehension, a
    parameter, a `del`, an `except … as`, a `global`, or an import of the same name drops it
    entirely. One binding in the whole file means there is exactly one scope to be wrong
    about, which is why this needs no scope tracking to be sound."""
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        for name in _bound_names(node):
            counts[name] = counts.get(name, 0) + 1
    out: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target: ast.expr = node.targets[0]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
        else:
            continue
        value = node.value
        if isinstance(target, ast.Name) and value is not None and counts.get(target.id) == 1:
            out[target.id] = value
    return out


@dataclass(frozen=True)
class _NativeCtx:
    """What resolving a target in ONE file needs: its import aliases and its stable names."""

    aliases: dict[str, str]
    bindings: dict[str, ast.expr]


def _canonical_name(node: ast.expr, ctx: _NativeCtx) -> str:
    """``node``'s dotted name with its import alias resolved, or ``""`` when it has none.

    Longest-prefix, so ``osp.expanduser`` under ``import os.path as osp`` becomes
    ``os.path.expanduser`` rather than being left unresolvable."""
    dotted = _dotted_name(node)
    if dotted is None:
        return ""
    parts = dotted.split(".")
    for i in range(len(parts), 0, -1):
        target = ctx.aliases.get(".".join(parts[:i]))
        if target is not None:
            return ".".join([target, *parts[i:]])
    return dotted


def _target_class(node: ast.expr | None, ctx: _NativeCtx, depth: int = 0) -> str | None:
    """The destructive class of the path ``node`` denotes, or ``None``.

    ``None`` is the answer for everything the analysis cannot resolve to a named target —
    a variable that changes, a runtime join, an argument. That is the direction that costs
    RECALL rather than precision, which is the right way round for a terminal band that
    blocks installs."""
    if node is None or depth > _TARGET_DEPTH:
        return None
    if isinstance(node, ast.Constant):
        return _classify_path_literal(node.value) if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return _target_class(ctx.bindings.get(node.id), ctx, depth + 1)
    if isinstance(node, ast.Subscript):
        keyed = isinstance(node.slice, ast.Constant) and node.slice.value in _HOME_ENV_KEYS
        return "home" if keyed and _canonical_name(node.value, ctx) == "os.environ" else None
    if not isinstance(node, ast.Call):
        return None
    name = _canonical_name(node.func, ctx)
    if name in _HOME_FUNCS:
        return "home"
    if name in _ENV_READ_FUNCS:
        first = node.args[0] if node.args else None
        keyed = isinstance(first, ast.Constant) and first.value in _HOME_ENV_KEYS
        return "home" if keyed else None
    if name in _PATH_PASSTHROUGH:
        return _target_class(node.args[0] if node.args else None, ctx, depth + 1)
    if isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_PASSTHROUGH_METHODS:
        return _target_class(node.func.value, ctx, depth + 1)
    return None


def _delete_target(node: ast.Call, ctx: _NativeCtx) -> ast.expr | None:
    """The path a removal call would remove, or ``None`` when this is not a removal."""
    if _canonical_name(node.func, ctx) in _DELETE_FUNCS:
        return node.args[0] if node.args else None
    if isinstance(node.func, ast.Attribute) and node.func.attr in _DELETE_METHODS:
        return node.func.value
    return None


def _truncating_mode(node: ast.Call, position: int) -> bool:
    """True when this call's ``mode`` is a literal that TRUNCATES what is already there.

    ``w`` and only ``w``: ``a`` appends, ``x`` refuses to clobber, ``r`` reads. A mode the
    source does not pin is not treated as truncating — the default is ``r``, and guessing
    otherwise would make every `open(p)` in the corpus a candidate."""
    mode: ast.expr | None = node.args[position] if len(node.args) > position else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    return isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "w" in mode.value


def _truncating_target(node: ast.Call, ctx: _NativeCtx) -> ast.expr | None:
    """The path a truncating write would blank, or ``None`` when this is not one."""
    name = _canonical_name(node.func, ctx)
    if name in _TRUNCATE_FUNCS:
        return node.args[0] if node.args else None
    if name in _OPEN_FUNCS:
        return node.args[0] if node.args and _truncating_mode(node, 1) else None
    if isinstance(node.func, ast.Attribute):
        if node.func.attr in _TRUNCATE_METHODS:
            return node.func.value
        if node.func.attr == "open" and _truncating_mode(node, 0):
            return node.func.value
    return None


def _walk_class(node: ast.expr, ctx: _NativeCtx) -> str | None:
    """The destructive class of the root an UNBOUNDED recursive walk starts from, else
    ``None``. A bounded walk (`Path(x).glob("*.md")`) is not one, whatever its root."""
    if not isinstance(node, ast.Call):
        return None
    name = _canonical_name(node.func, ctx)
    if name in _WALK_FUNCS:
        return _target_class(node.args[0] if node.args else None, ctx)
    if name in _GLOB_FUNCS:
        recursive = next((kw.value for kw in node.keywords if kw.arg == "recursive"), None)
        pattern = node.args[0] if node.args else None
        if not (isinstance(recursive, ast.Constant) and recursive.value is True):
            return None
        if not (isinstance(pattern, ast.Constant) and isinstance(pattern.value, str)):
            return None
        # The directory part of the pattern, and NO fallback when there isn't one. A pattern
        # with nothing before its first `*` is relative to the working directory, and reading
        # that as `/` made `glob.glob("**/*.pyc", recursive=True)` + `os.remove(p)` — a `.pyc`
        # cleanup loop — terminal. Found by re-reading this branch, not by a fixture, which is
        # why the fixture now exists.
        return _classify_path_literal(pattern.value.split("*")[0])
    if not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr in _WALK_METHODS:
        return _target_class(node.func.value, ctx)
    if node.func.attr == "glob":
        pattern = node.args[0] if node.args else None
        deep = isinstance(pattern, ast.Constant) and "**" in str(pattern.value)
        return _target_class(node.func.value, ctx) if deep else None
    return None


def _walk_class_within(node: ast.expr, ctx: _NativeCtx) -> str | None:
    """:func:`_walk_class` for the walk anywhere inside ``node`` — so wrapping the walk in
    ``sorted(...)`` or ``list(...)``, which is how it is usually written, still counts."""
    for inner in ast.walk(node):
        if isinstance(inner, ast.expr):
            found = _walk_class(inner, ctx)
            if found is not None:
                return found
    return None


def _deletes_within(nodes: Iterable[ast.AST], ctx: _NativeCtx) -> bool:
    """True when any of ``nodes`` contains a removal call, at any depth."""
    return any(
        isinstance(inner, ast.Call)
        and (
            _canonical_name(inner.func, ctx) in _DELETE_FUNCS
            or (isinstance(inner.func, ast.Attribute) and inner.func.attr in _DELETE_METHODS)
        )
        for root in nodes
        for inner in ast.walk(root)
    )


def _ast_evidence(text: str, node: ast.AST) -> str:
    """The user-facing snippet for one AST finding: ``L<line>: <source>``.

    Cut on LINE boundaries and never on columns. :mod:`ast` reports ``col_offset`` as a
    UTF-8 BYTE count, so a line with non-ASCII text before the call would make a
    column-derived slice point at the wrong characters — and this string is what a reviewer
    reads to check the finding. Shares :func:`_evidence`'s caps and its ``…`` mark, so a
    native finding and a regex finding read the same on the consent surface."""
    lines = text.splitlines()
    first = max((getattr(node, "lineno", 1) or 1) - 1, 0)
    last = min(getattr(node, "end_lineno", None) or first + 1, first + _EVIDENCE_MAX_LINES)
    prefix = f"L{first + 1}: "
    body = " ".join(" ".join(lines[first:last]).split())
    room = _EVIDENCE_CAP - len(prefix)
    if len(body) > room:
        body = body[: max(0, room - 1)].rstrip() + _ELLIPSIS
    return f"{prefix}{body}".rstrip()


def _scan_native_destruction(text: str, rel: str) -> list[Finding]:
    """The native-destruction family over one Python source text.

    At most one finding per rule — the same "first match per rule" shape ``_scan_script``
    uses for the regex catalog — emitted in :data:`_NATIVE_DESTRUCTION_RULES` order so a
    report is deterministic.

    A file that does not parse yields NOTHING. That is a recall limit and not a pass: the
    text catalogs still judge every byte of it, and the reachability pass still reports
    ``unparseable`` for whatever they matched. Guessing at a broken parse tree is how a
    scanner ends up flagging a docstring that forbids the very call it names."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):  # ValueError: NUL bytes / oversized literals
        return []
    ctx = _NativeCtx(aliases=_import_aliases(tree), bindings=_single_bindings(tree))
    hits: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            if _walk_class_within(node.iter, ctx) and _deletes_within(node.body, ctx):
                hits.setdefault("destructive_walk", node)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            if any(_walk_class_within(gen.iter, ctx) for gen in node.generators):
                produced: list[ast.AST] = [test for gen in node.generators for test in gen.ifs]
                produced.extend(
                    [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
                )
                if _deletes_within(produced, ctx):
                    hits.setdefault("destructive_walk", node)
        elif isinstance(node, ast.Call):
            if _target_class(_delete_target(node, ctx), ctx):
                hits.setdefault("destructive_delete", node)
            if _target_class(_truncating_target(node, ctx), ctx):
                hits.setdefault("destructive_truncate", node)
    return [
        Finding("script", Verdict.DANGEROUS, rule, rel, _ast_evidence(text, hits[rule]))
        for rule in _NATIVE_DESTRUCTION_RULES
        if rule in hits
    ]


# ── The scanner ─────────────────────────────────────────────────────────────


class SkillScanner:
    """Static content gate over a STAGED directory (skill or app).

    ``scan(staged_dir, tier)`` walks the tree, classifies each surface, and
    returns a :class:`ScanReport`. The same instance is stateless + reusable; a
    module-level :data:`default_scanner` is provided for convenience.
    """

    def scan(self, staged_dir: Path, tier: TrustTier = TrustTier.COMMUNITY) -> ScanReport:
        staged_dir = Path(staged_dir)
        findings: list[Finding] = []
        surfaces: set[str] = set()
        # Kept for the reachability pass below, from the walk's OWN bytes: the analysis
        # must answer for exactly the content the rules judged, with no second read for a
        # payload to change under.
        py_texts: dict[str, str] = {}
        loader_blobs: list[str] = []
        manifest_text: str | None = None
        opaque: list[str] = []  # Python/loader files the walk could not read (L4)

        if staged_dir.is_dir():
            for path in sorted(staged_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel_parts = path.relative_to(staged_dir).parts
                # Skip VCS/dependency noise dirs (.git hooks etc. aren't app content).
                if any(part in _SKIP_DIR_NAMES for part in rel_parts[:-1]):
                    continue
                rel = str(path.relative_to(staged_dir))
                text: str | None = None
                try:
                    if path.stat().st_size <= _MAX_FILE_BYTES:
                        text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = None
                if text is None:
                    # An unread file (oversize, unreadable) is a HOLE in the reachability
                    # graph, not an absence of edges — but only when it is a file that
                    # could hold or run code. A skipped .png proves nothing either way.
                    if path.suffix.lower() == ".py" or _is_loader_name(path.name):
                        opaque.append(rel)
                    continue
                lname = path.name.lower()
                if path.suffix.lower() == ".py":
                    py_texts[rel] = text
                elif _is_loader_name(path.name):
                    loader_blobs.append(text)
                if rel == "app.json":
                    manifest_text = text
                if path.suffix.lower() in _SCRIPT_EXTS or _is_under_scripts(rel):
                    surfaces.add("script")
                    findings.extend(self._scan_script(text, rel))
                if lname in _MANIFEST_NAMES or path.suffix.lower() in {
                    ".md",
                    ".json",
                    ".yaml",
                    ".yml",
                }:
                    surface = "frontmatter" if lname in _MANIFEST_NAMES else "manifest"
                    surfaces.add(surface)
                    findings.extend(self._scan_text(text, rel, surface))

        findings = _scope_by_reachability(
            findings,
            py_texts=py_texts,
            loader_blobs=loader_blobs,
            manifest_text=manifest_text,
            opaque=opaque,
        )
        verdict = self._aggregate(findings, tier)
        return ScanReport(
            verdict=verdict,
            findings=findings,
            surfaces_scanned=sorted(surfaces),
            tier=tier,
        )

    def scan_text(self, text: str, *, surface: str = "manifest") -> ScanReport:
        """Scan a single text blob. Community tier. ``surface="script"`` runs the
        full destructive-script ruleset (skill-install gate, S3); any other
        surface runs the prose/injection + invisible-char rules (the memory-write
        injection gate, S5).

        NO reachability scoping here, by construction: a bare blob has no bundle around
        it, so non-reachability cannot be proved and the default-deny answer stands. A
        DANGEROUS blob stays DANGEROUS."""
        findings = (
            self._scan_script(text, "")
            if surface == "script"
            else self._scan_text(text, "", surface)
        )
        return ScanReport(
            verdict=self._aggregate(findings, TrustTier.COMMUNITY),
            findings=findings,
            surfaces_scanned=[surface],
            tier=TrustTier.COMMUNITY,
        )

    # ── per-surface scans ──

    def _scan_script(self, text: str, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for rule, pat in _DANGEROUS_SCRIPT:
            m = pat.search(text)
            if m:
                out.append(Finding("script", Verdict.DANGEROUS, rule, rel, _evidence(text, m)))
        # Destruction spelled in the HOST LANGUAGE rather than as a shell command (#2607) —
        # decided on the AST, so Python only. The gate is stated as "not some OTHER language"
        # rather than "is `.py`", because every other `_SCRIPT_EXTS` member NAMES a language
        # whose syntax `ast.parse` refuses, while an extension-LESS file names nothing: a
        # `scripts/setup` with a python shebang is Python, and requiring the suffix would have
        # left exactly the payload parking spot this rule family exists to close. `""` covers
        # the bare-blob surface too, where the caller has no filename to offer at all.
        if Path(rel).suffix.lower() in {"", ".py"}:
            out.extend(_scan_native_destruction(text, rel))
        # exfil-to-remote: a sensitive-path read AND a network egress that sit
        # CLOSE TOGETHER (the read-creds→send-out pipeline) is high-confidence
        # malice. Scan comment-stripped text (a comment never executes) and
        # require proximity, so a doc comment mentioning IMDS or a security test
        # that references an address it explicitly BLOCKS isn't miscalled exfil.
        code = _strip_line_comments(text)
        sens_hits = list(_SENSITIVE_RE.finditer(code))
        net_hits = list(_NET_EGRESS_RE.finditer(code))
        exfil = next(
            (
                s
                for s in sens_hits
                for n in net_hits
                if abs(_line_of(code, s.start()) - _line_of(code, n.start()))
                <= _EXFIL_PROXIMITY_LINES
            ),
            None,
        )
        # The WARNING (a credential read, no adjacent egress) still fires on the
        # raw text — reading a secret path is worth flagging even in a comment-free
        # file, and a lone read is not downgraded by stripping comments.
        sens_raw = _SENSITIVE_RE.search(text)
        if exfil is not None:
            out.append(
                Finding(
                    "script", Verdict.DANGEROUS, "exfil_sensitive_path", rel, _evidence(code, exfil)
                )
            )
        elif sens_raw is not None and _SENSITIVE_RE.search(code) is not None:
            out.append(
                Finding(
                    "script",
                    Verdict.WARNING,
                    "reads_sensitive_path",
                    rel,
                    _evidence(text, sens_raw),
                )
            )
        for rule, pat in _WARNING_SCRIPT:
            m = pat.search(text)
            if m:
                out.append(Finding("script", Verdict.WARNING, rule, rel, _evidence(text, m)))
        out.extend(self._scan_invisible(text, rel, "script"))
        return out

    def _scan_text(self, text: str, rel: str, surface: str) -> list[Finding]:
        out: list[Finding] = []
        for rule, pat in _INJECTION_PROSE:
            m = pat.search(text)
            if m:
                out.append(Finding(surface, Verdict.WARNING, rule, rel, _evidence(text, m)))
        out.extend(self._scan_invisible(text, rel, surface))
        return out

    def _scan_invisible(self, text: str, rel: str, surface: str) -> list[Finding]:
        # Bidi overrides are a known steering/spoofing vector → dangerous;
        # zero-width chars are suspicious but lower-confidence → warning.
        bidi = {
            c
            for c in text
            if c in _INVISIBLE_CHARS
            and unicodedata.category(c) == "Cf"
            and c in {"‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"}
        }
        zw = {c for c in text if c in _INVISIBLE_CHARS} - bidi
        out: list[Finding] = []
        if bidi:
            out.append(
                Finding(
                    surface,
                    Verdict.DANGEROUS,
                    "bidi_override",
                    rel,
                    "bidirectional override codepoints present",
                )
            )
        if zw:
            out.append(
                Finding(
                    surface,
                    Verdict.WARNING,
                    "zero_width_chars",
                    rel,
                    "zero-width/invisible codepoints present",
                )
            )
        return out

    # ── aggregation ──

    @staticmethod
    def _aggregate(findings: list[Finding], tier: TrustTier) -> Verdict:
        if not findings:
            return Verdict.CLEAN
        worst = max((f.severity for f in findings), key=lambda v: v.rank)
        # The DANGEROUS floor is non-negotiable — no tier, not even builtin, ever
        # downgrades outright malice (the load-bearing guarantee).
        if worst is Verdict.DANGEROUS:
            return Verdict.DANGEROUS
        # Trusted provenance downgrades only the lower bands: a bundled skill's
        # warnings are advisory (cap at low); a trusted/official registry's
        # warnings are non-blocking.
        if tier is TrustTier.BUILTIN:
            return Verdict.LOW if worst.rank > Verdict.LOW.rank else worst
        if tier in (TrustTier.OFFICIAL, TrustTier.TRUSTED) and worst is Verdict.WARNING:
            return Verdict.LOW
        return worst


def _is_under_scripts(rel: str) -> bool:
    parts = Path(rel).parts
    return "scripts" in parts or "hooks" in parts or "bin" in parts


# Convenient shared instance.
default_scanner = SkillScanner()


def scan_dir(staged_dir: Path, tier: TrustTier = TrustTier.COMMUNITY) -> ScanReport:
    """Module-level helper — scan a staged directory with the default scanner."""
    return default_scanner.scan(staged_dir, tier)
