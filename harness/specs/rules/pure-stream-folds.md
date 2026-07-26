---
id: pure-stream-folds
type: ai-coding-rule
statement: >
  Chat- and run-stream state derivation must stay in pure fold functions
  (`coalesceReducers.ts`, `runFold.ts`) — `(state, event) -> state` with no React, fetch,
  or mutation — so recorded event traces can be replayed through them offline.
appliesTo:
  - web/src/pages/chat/coalesceReducers.ts
  - web/src/pages/loops/runFold.ts
requiredProfiles:
  - web
  - replay
requiredTests: []
source: >
  The K42/K44/K45 stream-coalescer bug class. Keeping stream state in pure folds makes
  those regressions replayable (feed a recorded trace, assert terminal state) instead of
  only reproducible by hand. If side effects creep into the fold, replay can't drive it.
expiry_condition: never while the chat/loops streams drive UI state through these folds.
---

# Stream state lives in pure folds (so it can be replayed)

The chat message stream and the loop/run stream both derive their UI state through **pure
fold functions**:

- `web/src/pages/chat/coalesceReducers.ts` — `applyCoalescedFlush(segs, revealed,
  coalescing)` and `insertActivity(segs, text, activityKind, coalescing)`: operate on
  `Segment[]`, return new arrays, no side effects.
- `web/src/pages/loops/runFold.ts` — `foldReducer(flags, event, data?)` folds one
  lifecycle event string into new `RunFlags`; `foldRunSnapshot(loop)` derives a view model
  from a persisted snapshot; `foldRun` merges them.

Because these are pure `(state, event) -> state` functions with no React/fetch/mutation,
the Session-3 replay substrate can feed a **recorded NDJSON trace** of real events through
them and assert the terminal state matches the recording — turning the K42/K44/K45
coalescer bug class into a *replayable* regression (`replay` profile), not just a
hand-written unit test.

## What compliance looks like

Keep derivation in the fold. Never reach for `useState`/`useEffect`/`fetch` or mutate the
input inside these functions — a side effect there breaks offline replay and re-opens the
door to the coalescer bugs. New stream event handling extends `foldReducer`'s switch (and
`RUN_LIFECYCLE`, per [[sse-event-registered]]), staying pure.
