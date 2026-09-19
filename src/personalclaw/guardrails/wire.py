"""What actually went on the wire — the outbound-prompt record (#3166).

The redaction seam and the recording seam are at opposite ends of one call. The scan runs in
:meth:`personalclaw.guardrails.model_call.ModelCallGuard._prescan`, the last thing before the
provider, and it returns the possibly-substituted text *to the provider*. The caller that
journals the prompt — the workflow engine's ``infer``/judge dispatchers — holds the prompt it
composed, which is the text BEFORE that substitution. So every replay, eval and judge bench read
text the model never saw, with nothing anywhere saying a substitution happened.

This module is the one-way channel that closes the gap, and its shape is dictated by two hazards
already measured in this codebase:

* **Redacting twice is not an option.** ``security.redact_credentials`` is idempotent on its own
  direct output but NOT on a composed ``key: value`` line built from an already-redacted value —
  it rewrites ``api_key: [REDACTED: credential]`` into ``[REDACTED: credential] credential]`` and
  takes the field name with it. So the fix cannot be "scan again where we record"; the scan has to
  stay at exactly one chokepoint and PUBLISH what it produced.
* **A redacted read is never a write source.** Nothing here round-trips: the recorder carries the
  wire text forward into the journal, and no path reads a redacted body back into a write.

**Why a caller-bound MUTABLE recorder rather than a plain ``ContextVar.set`` from inside the
guard.** The guard's ``stream()`` is an async generator and the controller runs each node in its
own ``asyncio.Task``; a ``Task`` gets a COPY of the context, so a value set deep inside is not
reliably visible to the code that has to journal it. Binding the object OUTSIDE and mutating it
inside works in every direction, because a context copy copies the reference, not the object.
Reads of the var are also concurrency-safe for the same reason each task has its own copy: two
nodes dispatched in parallel bind two different recorders.

Fail-safe when nothing is bound (a test injecting its own ``completion``, a provider resolved
without the guard): ``captured`` stays ``False`` and the caller keeps journaling the prompt it
composed. That is consistent rather than lax — no guard means no substitution either.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class WirePrompt:
    """The outbound prompt as the provider received it, plus why it differs from the input.

    ``text`` is the post-scan body: what ``scan_outbound`` returned and the provider was handed.
    It is deliberately the ONLY body here — carrying the pre-scan text alongside it would put the
    unredacted prompt back into the object the journal writes from, which is the defect.
    """

    #: The text handed to the provider. Empty when the call was BLOCKED (nothing was sent).
    text: str = ""
    #: True once the guard published a scan outcome for this call. Distinguishes "the prompt went
    #: out unchanged" from "no guard was in the path", which a bare empty ``text`` cannot.
    captured: bool = False
    #: True when the scan actually SUBSTITUTED something, i.e. the wire text differs from the
    #: composed prompt. False in ``warn`` mode even with findings, because warn sends the original.
    redacted: bool = False
    #: True when the scan REFUSED the call (``block`` mode). No prompt reached any provider, so
    #: there is nothing whose persistence would agree with a wire.
    blocked: bool = False
    #: Finding CLASSES only — ``credential``, ``email``, ``phone``, ``exfil_url``, ``injection``.
    #: Never a matched value: this rides into a ledger row a refiner reads, and the whole point of
    #: the substitution is that the value does not get written down.
    categories: tuple[str, ...] = ()
    #: How many findings the scan counted, for a legible "3 substitutions" surface.
    findings: int = 0
    #: How many times the guard published on this call. A typed-output miss retries ONE corrected
    #: prompt, and the compaction ladder can re-send a shortened one — the LAST publication is the
    #: text the provider actually answered, and a count >1 is why the journal and the node's own
    #: composed prompt may legitimately differ by more than a redaction.
    sends: int = field(default=0)


_CURRENT: ContextVar[WirePrompt | None] = ContextVar("personalclaw_wire_prompt", default=None)


@contextlib.contextmanager
def capture_wire_prompt() -> Iterator[WirePrompt]:
    """Bind a fresh :class:`WirePrompt` for the duration of one model call.

    Wrap the await that reaches a provider, then read the yielded object AFTER it returns::

        with capture_wire_prompt() as wire:
            text = await complete_with_compaction(fn, prompt, use_case=uc)
        journalled = wire.text if wire.captured else prompt
    """
    record = WirePrompt()
    token = _CURRENT.set(record)
    try:
        yield record
    finally:
        _CURRENT.reset(token)


def record_outbound(
    text: str,
    *,
    original: str,
    findings: int = 0,
    categories: tuple[str, ...] = (),
    blocked: bool = False,
) -> None:
    """Publish what the scan produced for the call in flight. A no-op when nothing is bound.

    Never raises: this is a recording channel, and a fault here must not be able to stop an
    outbound model call. ``redacted`` is derived by COMPARING, not by trusting the mode — a
    ``redact``-mode call whose text held nothing substitutable is not a redacted call, and
    labelling it one would put a "this was altered" badge on every prompt.
    """
    record = _CURRENT.get()
    if record is None:
        return
    try:
        record.captured = True
        record.sends += 1
        record.blocked = bool(blocked)
        record.findings = int(findings)
        record.categories = tuple(categories)
        # A blocked call sent nothing, so it has no wire text. Cleared rather than left at the
        # original: the caller journals from this object, and handing it the unredacted prompt of
        # a call that was refused for carrying a credential is the leak in a different costume.
        record.text = "" if blocked else text
        record.redacted = (not blocked) and text != original
    except Exception:  # noqa: BLE001 — observability must never break a model call
        return


def current_wire_prompt() -> WirePrompt | None:
    """The recorder bound for the call in flight, or ``None``. For tests and diagnostics."""
    return _CURRENT.get()
