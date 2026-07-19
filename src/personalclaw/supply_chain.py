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
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# One source of truth for "touches a credential/secret path" (IMDS, ~/.aws, …).
from personalclaw.history import _SENSITIVE_TOOL_PATTERNS

# ── Verdict + report ────────────────────────────────────────────────────────


class Verdict(str, Enum):
    """Scan outcome, ascending severity. ``dangerous`` is non-overridable."""

    CLEAN = "clean"        # nothing matched
    LOW = "low"            # benign-but-notable (advisory)
    WARNING = "warning"    # ambiguous risk — overridable with explicit confirm
    DANGEROUS = "dangerous"  # high-confidence malice — terminal, no override

    @property
    def rank(self) -> int:
        return {"clean": 0, "low": 1, "warning": 2, "dangerous": 3}[self.value]


# Trust tiers a source declares; they only ever DOWNGRADE the final verdict for
# trusted provenance (a bundled skill's `curl` is not the same risk as a random
# community one). They never upgrade — a dangerous pattern stays dangerous.
class TrustTier(str, Enum):
    BUILTIN = "builtin"      # shipped with PClaw — scan advisory-only (cap at low)
    OFFICIAL = "official"    # curated registry — warnings non-blocking
    TRUSTED = "trusted"      # user-trusted registry — warnings non-blocking
    COMMUNITY = "community"  # arbitrary — full gate (the default)


@dataclass
class Finding:
    """One matched signal. ``severity`` is this finding's own classification;
    the report's verdict is the max across findings (after tier modulation)."""

    surface: str    # "script" | "manifest" | "frontmatter" | "supply_chain"
    severity: Verdict
    rule: str       # stable short id, e.g. "destructive_root"
    path: str       # staged-relative file path ("" for whole-content surfaces)
    evidence: str   # the matched snippet (truncated, for the UX)

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "severity": self.severity.value,
            "rule": self.rule,
            "path": self.path,
            "evidence": self.evidence,
        }


@dataclass
class ScanReport:
    """The gate's output. ``verdict`` is the decision input; ``findings`` is the
    evidence the install UX surfaces ("community skill — 2 warnings")."""

    verdict: Verdict = Verdict.CLEAN
    findings: list[Finding] = field(default_factory=list)
    surfaces_scanned: list[str] = field(default_factory=list)
    tier: TrustTier = TrustTier.COMMUNITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "tier": self.tier.value,
            "findings": [f.to_dict() for f in self.findings],
            "surfaces_scanned": list(self.surfaces_scanned),
        }

    @property
    def is_dangerous(self) -> bool:
        return self.verdict is Verdict.DANGEROUS


# ── Pattern catalog ─────────────────────────────────────────────────────────
# High-confidence DANGEROUS patterns (terminal). Reserved for unambiguous malice
# so the non-overridable floor doesn't trap legitimate skills (risk #1).
_DANGEROUS_SCRIPT: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    # destructive-root: rm -rf / , rm -rf ~ , rm -rf $HOME, rm -fr /
    ("destructive_root", re.compile(r"\brm\s+-[rf]{1,2}\s+(?:-[rf]{1,2}\s+)*(?:/|~|\$HOME|\*)(?:\s|$|;)")),
    # fork bomb :(){ :|:& };:
    ("fork_bomb", re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    # disk wipe: mkfs, dd of=/dev/sdX, > /dev/sda
    ("disk_wipe", re.compile(r"\b(?:mkfs\.\w+|dd\s+[^\n]*\bof=/dev/(?:sd|nvme|disk)|>\s*/dev/(?:sd|nvme|disk))")),
    # pipe-to-shell exec of remote content: curl|wget … | sh/bash
    ("remote_exec_pipe", re.compile(r"\b(?:curl|wget|fetch)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")),
    # obfuscated exec: base64 -d | sh , echo … | base64 -d | bash
    ("obfuscated_exec", re.compile(r"base64\s+(?:--decode|-d|-D)\b[^\n|]*\|\s*(?:ba|z|da)?sh\b")),
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
_MAX_FILE_BYTES = 512 * 1024   # don't read huge blobs into the scanner
_EVIDENCE_CAP = 120
# Directories that are tooling/dependency noise, not the app's own content. A git
# clone carries .git/ (whose hooks/*.sample trip the script rules — a false
# positive); node_modules/venv are vendored deps the author didn't write. Skipping
# them keeps the gate focused on first-party content (and faster).
_SKIP_DIR_NAMES = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".tox"}


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
    "​", "‌", "‍", "⁠", "﻿",  # zero-width
    "‪", "‫", "‬", "‭", "‮",  # bidi overrides
    "⁦", "⁧", "⁨", "⁩",            # isolates
}


def _evidence(text: str, match: "re.Match[str]") -> str:
    s = max(0, match.start() - 20)
    e = min(len(text), match.end() + 20)
    snippet = text[s:e].replace("\n", "\\n").strip()
    return snippet[:_EVIDENCE_CAP]


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

        if staged_dir.is_dir():
            for path in sorted(staged_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel_parts = path.relative_to(staged_dir).parts
                # Skip VCS/dependency noise dirs (.git hooks etc. aren't app content).
                if any(part in _SKIP_DIR_NAMES for part in rel_parts[:-1]):
                    continue
                try:
                    if path.stat().st_size > _MAX_FILE_BYTES:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(path.relative_to(staged_dir))
                lname = path.name.lower()
                if path.suffix.lower() in _SCRIPT_EXTS or _is_under_scripts(rel):
                    surfaces.add("script")
                    findings.extend(self._scan_script(text, rel))
                if lname in _MANIFEST_NAMES or path.suffix.lower() in {".md", ".json", ".yaml", ".yml"}:
                    surface = "frontmatter" if lname in _MANIFEST_NAMES else "manifest"
                    surfaces.add(surface)
                    findings.extend(self._scan_text(text, rel, surface))

        verdict = self._aggregate(findings, tier)
        return ScanReport(
            verdict=verdict, findings=findings,
            surfaces_scanned=sorted(surfaces), tier=tier,
        )

    def scan_text(self, text: str, *, surface: str = "manifest") -> ScanReport:
        """Scan a single text blob. Community tier. ``surface="script"`` runs the
        full destructive-script ruleset (skill-install gate, S3); any other
        surface runs the prose/injection + invisible-char rules (the memory-write
        injection gate, S5)."""
        findings = (
            self._scan_script(text, "") if surface == "script"
            else self._scan_text(text, "", surface)
        )
        return ScanReport(
            verdict=self._aggregate(findings, TrustTier.COMMUNITY),
            findings=findings, surfaces_scanned=[surface], tier=TrustTier.COMMUNITY,
        )

    # ── per-surface scans ──

    def _scan_script(self, text: str, rel: str) -> list[Finding]:
        out: list[Finding] = []
        for rule, pat in _DANGEROUS_SCRIPT:
            m = pat.search(text)
            if m:
                out.append(Finding("script", Verdict.DANGEROUS, rule, rel, _evidence(text, m)))
        # exfil-to-remote: a sensitive-path read AND a network egress that sit
        # CLOSE TOGETHER (the read-creds→send-out pipeline) is high-confidence
        # malice. Scan comment-stripped text (a comment never executes) and
        # require proximity, so a doc comment mentioning IMDS or a security test
        # that references an address it explicitly BLOCKS isn't miscalled exfil.
        code = _strip_line_comments(text)
        sens_hits = list(_SENSITIVE_RE.finditer(code))
        net_hits = list(_NET_EGRESS_RE.finditer(code))
        exfil = next(
            (s for s in sens_hits for n in net_hits
             if abs(_line_of(code, s.start()) - _line_of(code, n.start())) <= _EXFIL_PROXIMITY_LINES),
            None,
        )
        # The WARNING (a credential read, no adjacent egress) still fires on the
        # raw text — reading a secret path is worth flagging even in a comment-free
        # file, and a lone read is not downgraded by stripping comments.
        sens_raw = _SENSITIVE_RE.search(text)
        if exfil is not None:
            out.append(Finding("script", Verdict.DANGEROUS, "exfil_sensitive_path", rel, _evidence(code, exfil)))
        elif sens_raw is not None and _SENSITIVE_RE.search(code) is not None:
            out.append(Finding("script", Verdict.WARNING, "reads_sensitive_path", rel, _evidence(text, sens_raw)))
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
        bidi = {c for c in text if c in _INVISIBLE_CHARS and unicodedata.category(c) == "Cf" and c in
                {"‪", "‫", "‬", "‭", "‮", "⁦", "⁧", "⁨", "⁩"}}
        zw = {c for c in text if c in _INVISIBLE_CHARS} - bidi
        out: list[Finding] = []
        if bidi:
            out.append(Finding(surface, Verdict.DANGEROUS, "bidi_override", rel,
                               "bidirectional override codepoints present"))
        if zw:
            out.append(Finding(surface, Verdict.WARNING, "zero_width_chars", rel,
                               "zero-width/invisible codepoints present"))
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
