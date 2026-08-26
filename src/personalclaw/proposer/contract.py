"""The ``ProposerBackend`` contract — "ask an outside brain one question" (EXEC-ISOLATION §4.2).

The second-opinion handoff (§4.1) has two halves: *packaging* a stalled consumer's state into a
brief, and *invoking* something else with it. This module owns the second half as a four-member
contract so every future caller that wants an outside opinion reuses one seam instead of minting
its own subprocess dance:

* :attr:`ProposerBackend.name` — the stable backend id (``"runner:gemini-cli"`` / ``"subagent"``).
* :meth:`ProposerBackend.prepare` — render the brief into runner-specific instructions. Claude
  Code wants different framing than Gemini CLI; the *dialect* knows, so the caller does not.
* :meth:`ProposerBackend.invoke` — fire it one-shot, headless, inside the sandbox class the
  stalled consumer was running in. Returns a handle, does not wait for the answer.
* :meth:`ProposerBackend.collect` — wait, then normalise into ONE :class:`ProposerResult` record
  that loops / gates / the cockpit read identically.

**The load-bearing field is** :attr:`ProposerResult.diff_verified`. A runner's final message
describes *intent*, not what landed on disk, so the acceptance test is a re-diff of the
workspace (:mod:`personalclaw.proposer.verify`) — never the runner's own claim. A backend that
cannot prove the claimed edits are on disk MUST report ``diff_verified=False``; the service then
refuses the handoff (:mod:`personalclaw.proposer.service`). This is the plan's stated mitigation
for "second-opinion runner returns confident garbage" (§12).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from personalclaw.proposer.brief import HandoffBrief
    from personalclaw.proposer.verify import DiskBaseline

#: The marker line the brief asks a proposer to emit, one per file it changed. Parsing an
#: explicit declaration beats guessing paths out of prose: a claim we cannot read is a claim
#: we treat as absent, which fails CLOSED (the handoff is rejected) rather than open.
CLAIM_MARKER = "PCLAW-EDITED:"


@dataclass(frozen=True)
class PreparedInvocation:
    """Everything needed to fire one proposer, and nothing that needs the network.

    ``argv`` is empty for backends that do not spawn a process (the ``subagent`` fallback
    hands ``prompt`` to :meth:`SubagentManager.spawn`). ``sandbox`` is the sandbox-provider
    name — the SAME class the stalled consumer ran in, because a second opinion that escapes
    the isolation of the run it is helping would be a downgrade nobody asked for.
    """

    backend: str
    runner_id: str
    prompt: str
    cwd: str
    sandbox: str = "none"
    argv: tuple[str, ...] = ()
    timeout_secs: float = 300.0
    baseline: "DiskBaseline | None" = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InvocationRef:
    """A fired-but-not-yet-collected proposer. Opaque to consumers; meaningful to its backend."""

    backend: str
    runner_id: str
    handle: str
    started_at: str
    prepared: PreparedInvocation
    error: str = ""

    @property
    def launched(self) -> bool:
        """False when the fire itself failed — :meth:`ProposerBackend.collect` short-circuits."""
        return not self.error and bool(self.handle)


@dataclass(frozen=True)
class ProposerResult:
    """The one normalised record every consumer reads, whichever backend produced it.

    ``ok`` is "the proposer ran and answered". ``diff_verified`` is "its claimed edits are
    actually on disk". They are deliberately separate: a run that succeeds and lies is
    ``ok=True, diff_verified=False``, and the plan requires that be recorded honestly rather
    than collapsed into one boolean a consumer could misread as success.
    """

    backend: str
    runner_id: str
    ok: bool
    summary: str
    diff_verified: bool
    artifacts: tuple[str, ...] = ()
    raw_ref: str = ""
    claimed_paths: tuple[str, ...] = ()
    verified_paths: tuple[str, ...] = ()
    missing_paths: tuple[str, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "runner_id": self.runner_id,
            "ok": self.ok,
            "summary": self.summary,
            "diff_verified": self.diff_verified,
            "artifacts": list(self.artifacts),
            "raw_ref": self.raw_ref,
            "claimed_paths": list(self.claimed_paths),
            "verified_paths": list(self.verified_paths),
            "missing_paths": list(self.missing_paths),
            "error": self.error,
        }


@runtime_checkable
class ProposerBackend(Protocol):
    """One way to ask an outside brain a single question. Four members, no more."""

    name: str

    async def prepare(self, brief: "HandoffBrief") -> PreparedInvocation:
        """Render *brief* into this backend's dialect and capture the disk baseline."""
        ...

    async def invoke(self, prepared: PreparedInvocation) -> InvocationRef:
        """Fire one-shot and return immediately with a handle."""
        ...

    async def collect(self, ref: InvocationRef) -> ProposerResult:
        """Wait for the answer, re-diff the workspace, and normalise the outcome."""
        ...


def parse_claimed_paths(text: str) -> tuple[str, ...]:
    """Extract the ``PCLAW-EDITED:`` claims from a proposer's final message.

    Order-preserving and de-duplicated. Anything else in the message — including a confident
    prose claim that files were changed — is NOT a claim: an unparseable claim is an absent
    claim, and an absent claim cannot be verified, so the handoff fails closed.
    """
    seen: dict[str, None] = {}
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("*-# ").strip()
        if not line.upper().startswith(CLAIM_MARKER):
            continue
        path = line[len(CLAIM_MARKER) :].strip().strip("`\"'")
        if path:
            seen.setdefault(path, None)
    return tuple(seen)
