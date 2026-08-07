#!/usr/bin/env python3
"""Committed docs-lint + plan-hygiene baseline generator (PLATFORM-HARDENING-FLOORS §6.2).

CLAUDE.md and EXECUTION-PROTOCOL §3 both require docs to move with the change, but nothing
mechanical enforced it: docs routinely drift from code (a governance doc documenting a
matcher the code has since removed) and plan ``**Status:**`` headers drift from their own
execution logs (the 2026-08-04 audit found 25 of 66 headers wrong). This generator is the
census that makes that drift visible. It scans tracked ``docs/**/*.md`` and emits a
deterministic per-file counter of three finding kinds to a committed
``docs-lint-baseline.json``; a companion test (``tests/test_docs_lint_baseline.py``)
regenerates in-memory and asserts every per-file counter **may only shrink** — a NEW dead
link, stale citation, or stale header raises a file's count and reds CI, naming the file
and the finding; a cleanup that removes one lowers it and is welcome.

The three checks (each deliberately calibrated to UNDER-report rather than cry wolf, so a
red is always a real regression):

1. ``dead_link`` — a *relative* markdown link ``[text](target)`` whose ``target`` (after
   stripping any ``#anchor``) resolves, relative to the linking file's directory, to a repo
   path that is not tracked. External schemes (``http(s)://``, ``mailto:``, ``tel:``,
   ``ftp://``, protocol-relative ``//``), pure ``#anchor`` intra-doc links, and
   absolute (``/``-leading) targets are skipped — none of those are relative repo-file
   links. Links inside fenced code blocks and inline code spans are stripped before
   matching (DSL/example syntax like ``[field](value)`` is not a link), and only path-like
   targets (containing ``/`` or a known file extension) are considered. We do NOT validate
   that a ``#anchor`` exists inside the target file — anchor text is unstable and that would
   cry wolf; a missing *file* is the unambiguous signal.

2. ``stale_citation`` — a ``path/to/file.py:NNN`` citation in docs prose whose FILE cannot
   be found in the repository. Citations are matched with ``([\\w/.-]+\\.py):(\\d+)`` and a
   citation counts as stale ONLY when the file is missing (no tracked path equals it or ends
   with ``/<citation>``, so an abbreviated ``config/loader.py`` still resolves to
   ``src/personalclaw/config/loader.py``). A DRIFTED LINE NUMBER (file exists, the ``:NNN``
   points at a different line) is **NOT** counted: roadmap prose is full of ``file.py:NNN``
   citations whose line numbers drift constantly — SELF-VERIFICATION explicitly mandates
   "specs reference stable anchors, never line numbers" precisely because line-drift is
   chronic — so validating that every ``:NNN`` points at a semantically-correct line is
   impossible and noisy. Only a stale PATH is a hard dead citation.

3. ``stale_header`` — a plan under ``docs/roadmap/plans/`` whose ``**Status:**`` header
   matches a stale shape (``DESIGNED``/``PROPOSED``/``READY``/``NOT STARTED``) while the file
   carries a populated ``## Execution log`` containing a ``DONE`` entry: the exact "plan
   headers lie" drift the 2026-08-04 audit surfaced. The ``**Status:**`` line is parsed with
   the SAME regex ``tools/gen_roadmap_dashboard.py`` uses, so the two agree. This is a
   heuristic, not a proof — it reproduces the known audit finding on a seeded stale header;
   it does not attempt to adjudicate every real header against reality (the log and the code
   win over the header, so this ratchets rather than blocks).

⚠️  FORBIDDEN-TO-RAISE RULE — "fix the doc, not the baseline" (the whole point; do not weaken
    it): when this baseline reds CI because a counter ROSE, the fix is to FIX THE DOC —
    repair the dead link, update or remove the stale citation, or reconcile the plan header
    with its execution log — NEVER to regenerate ``docs-lint-baseline.json`` to bless the
    higher number. Regenerating to make a rising count green re-hides exactly the drift this
    census exists to surface. Regeneration is legitimate ONLY when a counter LEGITIMATELY
    SHRANK (a real doc fix landed) — and then it must happen in that same commit.

⚠️  SHIP AT THE MEASURED POPULATION, NOT AT ZERO. A never-run gate given teeth at zero is an
    outage: it would red every pre-existing dead link, stale citation, and stale header at
    once. So this tool MEASURES the current population and commits the real (non-zero) number
    as the floor; the ratchet only forbids *growth*. Driving the count down (fixing the
    docs) is a separate cleanup effort, not this atom — do not "fix" any finding this census
    reports here.

The render is DETERMINISTIC: the tracked-file set and every finding list are sorted, paths
are POSIX repo-relative, the output is ``json.dumps(..., indent=2, sort_keys=True)`` with a
trailing newline, and it carries no timestamps or absolute paths. A second run on the same
tree is byte-identical to the first.

Regenerate in place (ONLY on a legitimate shrink) with::

    python scripts/generate_docs_lint_baseline.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

# Surface-kind labels, used as the ``kind:`` prefix of every finding id.
KIND_DEAD_LINK = "dead_link"
KIND_STALE_CITATION = "stale_citation"
KIND_STALE_HEADER = "stale_header"

# A relative markdown link ``[text](target ...)``; the negative lookbehind skips images
# (``![alt](src)``). Only the destination up to whitespace or ``)`` is captured.
_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(\s*([^)\s]+)")

# Link destinations that are never relative repo-file links.
_LINK_SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "ftp://", "//", "#", "/")

# Path-like heuristic for link destinations (a real DSL token like ``value`` is skipped).
_FILE_EXT_RE = re.compile(
    r"\.(md|py|json|txt|png|jpg|jpeg|svg|gif|yml|yaml|toml|cfg|ini|sh|ts|tsx|js|jsx|"
    r"html|css|lock|sql|webp|ico|pdf|mmd)$",
    re.I,
)

# A ``file.py:NNN`` citation. Same shape the §6.2 task specifies.
_CITATION_RE = re.compile(r"([\w/.-]+\.py):(\d+)")

# ``**Status:**`` header — the SAME regex ``tools/gen_roadmap_dashboard.py`` uses.
_STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)(?:\n\n|\n##|\n\*\*)", re.S | re.M)

# Stale-shape status words: a header claiming the work is not yet done.
_STALE_STATUS_RE = re.compile(r"\b(DESIGNED|PROPOSED|READY|NOT STARTED)\b", re.I)

# A DONE entry in an execution log: a list bullet mentioning DONE. Anchored to a bullet so
# the ``<!-- ... DONE ... -->`` template comment (stripped anyway) can never match.
_DONE_ENTRY_RE = re.compile(r"(?m)^\s*[-*]\s.*\bDONE\b")

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

# Plans whose headers this census holds to the stale-shape heuristic.
_PLANS_PREFIX = "docs/roadmap/plans/"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tracked_files() -> list[str]:
    """Every git-tracked path (POSIX, sorted). The resolution universe for links and
    citations and the scan set for docs — "tracked" is the contract, and using git keeps
    the render stable regardless of untracked/generated files in a working tree."""
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(p for p in proc.stdout.splitlines() if p)


def _docs_md(tracked: list[str]) -> list[str]:
    """Tracked ``docs/**/*.md`` files (sorted)."""
    return [p for p in tracked if p.startswith("docs/") and p.endswith(".md")]


def _strip_code(text: str) -> str:
    """Blank out fenced code blocks and inline code spans so example/DSL ``[x](y)`` syntax
    inside them is not mistaken for a link (keeps line structure so nothing else shifts)."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def _pathlike(target: str) -> bool:
    return "/" in target or bool(_FILE_EXT_RE.search(target)) or target in (".", "..")


def _resolve(rel_md: str, target: str) -> str:
    """Resolve a relative link ``target`` against the linking file's directory to a POSIX
    repo-relative path (no leading ``./`` and no ``..`` left behind)."""
    base = str(PurePosixPath(rel_md).parent)
    joined = os.path.normpath(os.path.join(base, target))
    return PurePosixPath(joined).as_posix()


def find_dead_links(rel_md: str, text: str, tracked: set[str]) -> list[str]:
    """Dead relative links in one markdown file. See the module docstring, check (1)."""
    findings: set[str] = set()
    for match in _LINK_RE.finditer(_strip_code(text)):
        raw = match.group(1).strip("<>")
        if not raw or raw.startswith(_LINK_SKIP_PREFIXES):
            continue
        path = raw.split("#", 1)[0]
        if not path or not _pathlike(path):
            continue
        rel = _resolve(rel_md, path)
        if rel in tracked or any(t.startswith(rel + "/") for t in tracked):
            continue
        findings.add(f"{KIND_DEAD_LINK}:{rel}")
    return sorted(findings)


def find_stale_citations(text: str, tracked: set[str]) -> list[str]:
    """``file.py:NNN`` citations whose FILE is missing. See the module docstring, check (2).

    Line-number drift is deliberately NOT counted: only a missing PATH is a dead citation.
    """
    findings: set[str] = set()
    for match in _CITATION_RE.finditer(text):
        cited = match.group(1)
        if cited in tracked or any(t.endswith("/" + cited) for t in tracked):
            continue
        findings.add(f"{KIND_STALE_CITATION}:{cited}")
    return sorted(findings)


def find_stale_header(rel_md: str, text: str) -> list[str]:
    """A stale-shape ``**Status:**`` header on a plan with a DONE'd execution log.

    See the module docstring, check (3). Scoped to ``docs/roadmap/plans/`` (a synthetic path
    under that prefix is how the reproduction test seeds a stale header).
    """
    if not rel_md.startswith(_PLANS_PREFIX):
        return []
    status = _STATUS_RE.search(text)
    if not status:
        return []
    line = " ".join(status.group(1).split())
    word = _STALE_STATUS_RE.search(line)
    if not word:
        return []
    if "## Execution log" not in text:
        return []
    log = _HTML_COMMENT_RE.sub("", text.split("## Execution log", 1)[1])
    if not _DONE_ENTRY_RE.search(log):
        return []
    return [f"{KIND_STALE_HEADER}:{word.group(1).upper()}"]


def _findings_for(rel_md: str, text: str, tracked: set[str]) -> list[str]:
    """All findings for one markdown file, sorted."""
    return sorted(
        find_dead_links(rel_md, text, tracked)
        + find_stale_citations(text, tracked)
        + find_stale_header(rel_md, text)
    )


def build_inventory() -> dict[str, Any]:
    """Render the full docs-lint inventory as a deterministic, JSON-safe dict.

    Shape::

        {
          "generated_from": "scripts/generate_docs_lint_baseline.py",
          "per_file": {"<relpath>": {"findings": ["kind:detail", ...], "total": N}},
          "totals": {"total": T, "by_kind": {"dead_link": N, ...}}
        }
    """
    tracked_list = _tracked_files()
    tracked = set(tracked_list)
    root = _repo_root()
    per_file: dict[str, dict[str, Any]] = {}
    by_kind: dict[str, int] = {
        KIND_DEAD_LINK: 0,
        KIND_STALE_CITATION: 0,
        KIND_STALE_HEADER: 0,
    }
    for rel_md in _docs_md(tracked_list):
        text = (root / rel_md).read_text(encoding="utf-8", errors="replace")
        findings = _findings_for(rel_md, text, tracked)
        if not findings:
            continue
        per_file[rel_md] = {"findings": findings, "total": len(findings)}
        for finding in findings:
            by_kind[finding.split(":", 1)[0]] += 1
    total = sum(bucket["total"] for bucket in per_file.values())
    return {
        "generated_from": "scripts/generate_docs_lint_baseline.py",
        "per_file": per_file,
        "totals": {"total": total, "by_kind": by_kind},
    }


def build_baseline() -> str:
    """Render the inventory as a deterministic JSON string (sorted, trailing newline)."""
    return json.dumps(build_inventory(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def regressions(baseline_per_file: dict[str, Any], current_per_file: dict[str, Any]) -> list[str]:
    """Files whose finding counter ROSE versus the baseline (shrink-only ratchet).

    Returns a sorted list of human-readable regression lines — one per file whose current
    ``total`` exceeds its committed count (a file absent from the baseline counts as 0). A
    DECREASE is never a regression: that is a doc fix and is welcome. This is the exact
    comparison the ratchet test asserts against; it lives here so the test and the generator
    share one definition of "backslide".
    """
    lines: list[str] = []
    for rel in sorted(current_per_file):
        current = int(current_per_file[rel].get("total", 0))
        baseline = int(baseline_per_file.get(rel, {}).get("total", 0))
        if current > baseline:
            new_findings = sorted(
                set(current_per_file[rel].get("findings", []))
                - set(baseline_per_file.get(rel, {}).get("findings", []))
            )
            lines.append(
                f"{rel}: docs-lint findings rose {baseline} -> {current}; "
                f"new finding(s): {new_findings}"
            )
    return lines


def baseline_path() -> Path:
    """Repo-root location of the committed ``docs-lint-baseline.json``."""
    return _repo_root() / "docs-lint-baseline.json"


def main() -> None:
    path = baseline_path()
    path.write_text(build_baseline(), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
