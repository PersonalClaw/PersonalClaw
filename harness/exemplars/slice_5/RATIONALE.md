# Slice 5 — Human-input contract and gates

**What the slice added.** The typed ask-payload model, mode-dependent gate timeouts and
`timed_out_unattended`, continuation records + durable resume tokens, the action-node
clarification → needs-input path, and default-DENY for remote-channel gates. The invariant
that matters most (WF2-R7): an unanswered gate must never silently become an approval.

**What this exemplar proves.** The two ends of a gate's life, driven against the real
controller:

- **Surfaces, doesn't wedge.** An `approval` gate nobody answers settles the run at
  `needs_input` (a stopping point, not a terminal state) carrying a typed `{"kind":
  "approval", ...}` ask payload — the thing the attention banner renders. The exemplar
  waits with `wait_for_terminal` (which returns at `needs_input`) and then `stop()`s the
  background loop, so it does not block on the run's own timeout.
- **Times out to FAILED, never to a pass.** A gate with a short `timeout_secs` that nobody
  answers reaches `FAILED` — emphatically not `COMPLETE`. A timed-out gate reading as
  approval is exactly how an unattended run would "approve" something no human ever saw.

The timeout leg uses a 1-second `timeout_secs`, so the exemplar stays well under the 30s
smoke ceiling.

**Mechanism under test:** the `gate` dispatcher + `RunController` needs-input / gate-timeout
handling (WF2-R7).
