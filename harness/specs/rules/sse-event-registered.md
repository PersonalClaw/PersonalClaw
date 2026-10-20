---
id: sse-event-registered
type: ai-coding-rule
statement: >
  Every event-type string published through an SSE registry (`SseRegistry.publish(key,
  event, data)`) must appear in the frontend lifecycle union that consumes that stream
  (`RUN_LIFECYCLE` in `useRunStream.ts`), or `EventSource` silently drops it.
appliesTo:
  - src/personalclaw/dashboard/sse.py
  - web/src/pages/loops/useRunStream.ts
scanner: sse-event-registered
source: >
  `EventSource` delivers only events for which a named listener is registered. A backend
  publishing a new event type the frontend never registered results in a silently missing
  UI update — no error, no log, just a state change that never renders.
expiry_condition: >
  Retire if the FE subscribes to a wildcard/`message` channel and dispatches by a payload
  field instead of registering one listener per event name.
---

# SSE event types must be registered on both ends

The loop/run stream uses one `EventSource` listener **per event-type string**
(`web/src/pages/loops/useRunStream.ts` registers a listener for each name in the
`RUN_LIFECYCLE` const array). The backend publishes named events through
`SseRegistry.publish(key, event, data)` (`dashboard/sse.py`). If the backend introduces a
new `event` name that isn't in `RUN_LIFECYCLE`, the browser's `EventSource` receives it
but has no listener bound — so it is **silently discarded**. No console error, no network
failure; the UI simply never reflects that event.

## What compliance looks like

When you add a new SSE event type on the backend, add the exact string to `RUN_LIFECYCLE`
(and handle it in the stream's handler map) in the same change. The scanner check
`sse-event-registered` compares the set of published event strings against the FE union
and flags any that only exist on one side.
