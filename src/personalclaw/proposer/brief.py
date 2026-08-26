"""The one-shot handoff brief (EXECUTION-ISOLATION §4.1).

agentsystem's ``/handoff-codex`` packet, generalised: everything a fresh brain needs to make one
useful attempt at a problem another brain got stuck on, and nothing it has to ask a follow-up
question to get.

Two properties the plan calls out explicitly:

* **The diff is FRESH.** It is taken at brief-build time, not carried from whenever the run
  started — "stale diffs are worse than none", because a proposer reasoning off a diff that has
  since moved produces edits that conflict with reality.
* **Transcript excerpts are FENCED.** Verbatim errors and tool output are exactly the untrusted
  text a prompt injection rides in on, so they go through
  :func:`personalclaw.security.fence_untrusted` before a second model reads them.

Redaction is applied ONCE per field, at construction, never again over the composed markdown:
``redact_credentials`` is not idempotent across a composed line, and re-screening a rendered
document destroys field names. Each source is screened at entry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.proposer.contract import CLAIM_MARKER
from personalclaw.proposer.verify import DiskBaseline, snapshot_workspace
from personalclaw.security import fence_untrusted, redact

#: Per-excerpt cap. A stalled loop can hold megabytes of repeated failure; a proposer reading
#: the first and last of it does better than one whose context is exhausted before the ask.
_EXCERPT_MAX = 4000


def _clip(text: str, limit: int = _EXCERPT_MAX) -> str:
    """Head+tail clip — the first failure and the last are both diagnostic; the middle repeats."""
    body = text or ""
    if len(body) <= limit:
        return body
    head = body[: limit // 2]
    tail = body[-(limit // 2) :]
    return f"{head}\n… [{len(body) - limit} chars elided] …\n{tail}"


@dataclass(frozen=True)
class HandoffBrief:
    """A stalled consumer's state, packaged for exactly one outside attempt."""

    goal: str
    stuck_at: str
    ask: str
    workspace: str
    #: The runner/agent that stalled. This is the EXCLUSION key for target selection — a
    #: second opinion from the brain that just failed is not a second opinion.
    origin_runner: str = ""
    #: The sandbox-provider name the stalled consumer ran under. The proposer gets the same one.
    sandbox: str = "none"
    session_key: str = ""
    consumer: str = ""
    attempts: tuple[str, ...] = ()
    files_touched: tuple[str, ...] = ()
    baseline: DiskBaseline | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def render(self) -> str:
        """The brief as markdown. Already-redacted fields; untrusted excerpts already fenced."""
        lines = [
            "# Second-opinion handoff brief",
            "",
            "Another agent got stuck on this and is handing it to you for ONE attempt.",
            "",
            "## The goal",
            self.goal or "(not recorded)",
            "",
            "## Where it is stuck",
            self.stuck_at or "(not recorded)",
            "",
        ]
        if self.attempts:
            lines += ["## What was already tried (verbatim output from the stalled agent)", ""]
            for i, attempt in enumerate(self.attempts, 1):
                lines += [
                    f"### Attempt {i}",
                    fence_untrusted(
                        _clip(attempt),
                        source=self.origin_runner or "stalled agent",
                        source_type="agent_transcript",
                        source_id=self.session_key,
                    ),
                    "",
                ]
        lines += ["## Workspace", f"`{self.workspace}`", ""]
        if self.files_touched:
            lines += ["## Files touched so far", ""]
            lines += [f"- `{p}`" for p in self.files_touched]
            lines += [""]
        base = self.baseline
        if base is not None and base.git_status:
            lines += [
                "## Fresh `git status --porcelain` (taken when this brief was written)",
                "",
                "```",
                _clip(base.git_status),
                "```",
                "",
            ]
        if base is not None and base.git_diff:
            lines += [
                "## Fresh `git diff` (taken when this brief was written)",
                "",
                "```diff",
                _clip(base.git_diff, 20000),
                "```",
                "",
            ]
        lines += [
            "## The ask",
            self.ask or "Make the smallest change that unblocks this, then stop.",
            "",
            "## How to report back (required)",
            "",
            "Make the edits directly on disk in the workspace above. Then end your reply with",
            "one line per file you changed, in exactly this form:",
            "",
            "```",
            f"{CLAIM_MARKER} relative/path/to/file.py",
            "```",
            "",
            "Every path you list is re-read from disk and compared byte-for-byte against the",
            "state before you ran. A path you list that did not actually change causes this",
            "whole handoff to be REJECTED, so list only what you really edited — and if you",
            "made no edits, list nothing and say why.",
        ]
        return "\n".join(lines)

    def write(self, directory: str) -> str:
        """Write the rendered brief to a unique file under *directory*; return its path."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        stamp = os.urandom(4).hex()
        path = target / f"handoff-brief-{stamp}.md"
        atomic_write(str(path), self.render())
        return str(path)


def build_brief(
    *,
    goal: str,
    stuck_at: str,
    ask: str = "",
    workspace: str = "",
    origin_runner: str = "",
    sandbox: str = "none",
    session_key: str = "",
    consumer: str = "",
    attempts: tuple[str, ...] = (),
    files_touched: tuple[str, ...] = (),
    metadata: dict[str, str] | None = None,
) -> HandoffBrief:
    """Package a stalled consumer's state, taking a FRESH disk baseline as a side effect.

    The baseline is both the brief's ``git status``/``git diff`` prose *and* the pre-fire digest
    table the acceptance test re-reads. One snapshot serves both, so the diff a proposer reads
    and the diff we verify against are the same moment in time — they cannot drift apart.
    """
    ws = workspace or os.getcwd()
    baseline = snapshot_workspace(ws, paths=files_touched)
    return HandoffBrief(
        goal=redact(goal or ""),
        stuck_at=redact(stuck_at or ""),
        ask=redact(ask or ""),
        workspace=ws,
        origin_runner=origin_runner or "",
        sandbox=sandbox or "none",
        session_key=session_key or "",
        consumer=consumer or "",
        attempts=tuple(redact(a) for a in attempts if a),
        files_touched=tuple(files_touched),
        baseline=baseline,
        metadata=dict(metadata or {}),
    )
