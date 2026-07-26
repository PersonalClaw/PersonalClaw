---
id: stream-event-duplicated-or-lost
type: triage-scenario
symptom: >
  A streamed reply renders twice, a UI update never appears, or a loop/run widget shows a
  stale or out-of-order state — the K42/K44/K45 stream-coalescer family.
appliesTo:
  - web/src/pages/chat/coalesceReducers.ts
  - web/src/pages/loops/runFold.ts
  - src/personalclaw/dashboard/sse.py
requiredRules:
  - pure-stream-folds
  - sse-event-registered
requiredProfiles:
  - replay
acceptance:
  - The behavior is reproduced by REPLAYING a recorded trace through the pure fold (not
    only by clicking the live UI), and the fixed fold replays clean.
  - "`harness run --diff` on the fix forces the `replay` profile, and the recorded scenario's baseline shows duplicate_event_rate within threshold + zero order violations."
---

# Symptom: stream event duplicated, lost, or out of order

## Probe order

1. **Reproduce by replay, not by clicking.** Record the failing stream with
   `PERSONALCLAW_TRACE_DIR=<dir>` set on the gateway, drive the failing interaction, then
   replay the captured NDJSON through the pure fold: `python -m harness replay` (backend
   metrics) and the vitest `replayFold.test.ts` (chat coalescer / run fold). A replay that
   reproduces the bug is a permanent regression; a hand-click that reproduces it is not.
2. **Is it a fold bug or a registration bug?** If the event never reaches the UI at all,
   check [[sse-event-registered]] first — an event type not in `RUN_LIFECYCLE` is silently
   dropped by `EventSource`. If the event arrives but renders wrong (twice / stale), it's a
   fold bug in `coalesceReducers.ts` / `runFold.ts` ([[pure-stream-folds]]).
3. Keep the fold pure — a side effect in the fold breaks offline replay and re-opens the
   coalescer bug class.

## Known causes + mitigations

- **K44 (answer rendered twice):** a spurious boundary reset makes the next flush PUSH
  instead of REPLACE. `adjacentDuplicateTextCount` in the replay driver catches it.
- **K42 (activity absorbed the answer):** an activity line inserted AFTER the active text
  run instead of before it. `insertActivity` inserts before the tail text run.
- **Out-of-order / lost:** a `seq` regression or gap — the backend replay metrics
  (`order_violation_count`, `reconnect_loss_count`) catch it against the baseline.

## Redaction

Traces are redacted at write via `security.redact` before landing in `harness/traces/`.
Note that `redact` catches AWS keys + exfil URLs but not every token shape — do not record
a stream you know carries an unredacted secret without checking the captured NDJSON first.
