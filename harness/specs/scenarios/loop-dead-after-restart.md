---
id: loop-dead-after-restart
type: triage-scenario
symptom: >
  A loop that was running before a gateway restart doesn't resume — it sits idle, loses its
  place, or can't say what it had done / what's next after the process came back.
appliesTo:
  - src/personalclaw/loop/manager.py
  - src/personalclaw/loop/store.py
requiredRules: []
acceptance:
  - "`reap_orphaned_loops` re-arms a RUNNING/PLANNING loop on startup, and the resumed loop answers done/verified/next from persisted state (verified by `harness.resume_audit.audit_loop`)."
  - No resume path depends on an in-memory-only counter that a restart drops.
---

# Symptom: loop doesn't resume after a restart

## Probe order

1. **Is the reap running?** `reap_orphaned_loops` (loop/manager.py) is the startup sweep
   that re-arms loops persisted RUNNING (→ re-arm worker) or PLANNING (→ re-kick
   `advance_plan`). A loop stuck idle after restart usually means the reap didn't re-arm it
   (or a launch precondition failed and it was paused with a question — check for that).
2. **Audit resumability from disk.** Run the mechanical fresh-session audit:
   `harness.resume_audit.audit_loop(loop_id)`. It reconstructs the loop from persisted
   state ALONE and reports whether done/verified/next/how-to-verify are answerable. A
   `False` on any of them names the gap.
3. **Beware in-memory counters.** Findings COUNT (files on disk) is the cycle clock — it
   resumes. In-memory watchdog counters are documented as NOT resumed; a resume path must
   never depend on one to know where it was.

## Known causes + mitigations

- **Reap skipped it:** a genuinely-live worker is skipped (idempotent) — confirm the old
  session really died. A moved/deleted bound workspace pauses the loop with a question
  rather than resurrecting nothing (by design).
- **State only in memory:** if "what's next" isn't derivable from `status` + `plan` +
  `phase_status` + findings on disk, that state must be persisted — the resume-audit fails
  precisely when it isn't.

## Note

Workflow-run resume (byte-equal frontier reconstruction from the WF2 journal) is a separate,
engine-gated audit — this scenario covers the loop half, which persists fully today.
