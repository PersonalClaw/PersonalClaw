# AgentActivityFeed: the agent-worlds read contract

An **agent world** is an ambient scene of what your agents are doing right now — a
thing you glance at, not a list you read. PersonalClaw ships one first-party world
("Orbit", on the dashboard) and intends for apps to contribute others.

The platform's contribution is **this contract, not the scene**. A world is handed one
typed value and renders it. It opens no socket, calls no endpoint, and knows nothing
about the work that runs beyond what is written here.

- Contract + hook: `web/src/lib/useAgentActivity.ts`
- First-party world: `web/src/pages/dashboard/world/` (`AgentWorld.tsx` paints,
  `worldScene.ts` is the pure scene model)

## The shape

```ts
useAgentActivity(): {
  entities: AgentActivityEntity[]
  truncated: number      // how many the fold dropped to stay under MAX_ENTITIES (64)
  error: unknown         // the FOLD's own failure — never render this as silence
  loading: boolean       // true until the first fold settles
  refresh: () => void
}

AgentActivityEntity = {
  id: string             // kind-prefixed: 'session:x' | 'loop:x' | 'run:x' | 'subagent:x' | …
  kind: 'session' | 'loop' | 'run' | 'subagent' | 'trigger' | 'webhook'
  state: 'working' | 'blocked' | 'needs_input' | 'waiting_approval' | 'idle' | 'error'
  title: string          // never empty — falls back to the kind's noun
  progress?: number      // 0..1, ABSENT when unknown (see below)
  refs: { link: string; session?: string; parent?: string }
}
```

Three rules a world must honour:

1. **`progress` absent means UNKNOWN, not zero.** Draw no arc rather than an empty
   one. An empty ring reads as "0% done", which is a claim about a run nobody measured.
2. **`error` is not `entities: []`.** An empty scene means "nothing is running"; a
   failed read means "we could not look". Rendering a calm empty world while every
   fetch is 502ing is the worst thing this surface can do. Say *unknown*.
3. **`truncated > 0` must be stated.** Silently painting 64 of 300 misstates the scale
   of what is running.

## Where the data comes from — the running list, and what it does not hold

What runs is read from **one** list, `GET /api/running` (`src/personalclaw/running_now.py`,
[work phases](work-phases.md#the-running-list)): every kind of live work, each once. The
gateway has already folded a loop's workers and planner into their loop, a run-backed
loop into one row, and marked a row `blocked` when it is queued for a slot, retrying or
waiting on a busy model. The other public sources add only what the list does not hold:

| Source | Contributes | Notes |
| --- | --- | --- |
| `GET /api/running` | every piece of live work, and the just-finished tail | a chat's turn, a helper, a loop, a workflow run, a trigger's fire, a webhook's turn |
| `GET /api/chat/sessions` | the quiet chats behind it | archived sessions and a loop's workers and planner (`origin: 'loop'`) are excluded; capped |
| `GET /api/loops` | the loops that are not running, and a running loop's cycle budget | a loop on the list is the list's |
| `GET /api/approvals` | which of a row's waits are approvals | a row's `waiting` names approvals and agents' questions alike |

A row's state is what waits on you for it first: an approval in its `waiting` is
`waiting_approval`, any other wait (an agent's question) `needs_input`. Then the gateway's
`blocked`, then its phase. "Busy" painted over "one click away from continuing" is the
failure the surface exists to prevent: a run whose step's tool waits on you still says
`running` in its own phase.

A just-finished row paints how it ended: a failure is `error`, a finish rests as `idle`.

### Loop status → world state

A loop the running list does not hold keeps its status's state. The 12-member
`UnifiedLoopStatus` collapses **here and nowhere else**, so every world agrees on what
"stuck" looks like.

| loop status | world state |
| --- | --- |
| `intake` `planning` `review` `running` | `working` |
| `needs_input` `blocked` `stagnant` | `needs_input` |
| `failed`, or `complete` with an `error_message` | `error` |
| `ready` `paused` `stopped` `complete` | `idle` |

`blocked` and `stagnant` land on `needs_input` on purpose — the dashboard's ActiveWork
widget already treats both as "in flight or awaiting them", and a stalled run painted
as calm `working` is precisely the lie an ambient surface must not tell.

### Phase → world state

A row's phase is the work's last `work_phase` payload ([work phases](work-phases.md)), and
a chat's row is its `run_state`. Its state is the phase's `world` in
`web/src/lib/workPhase.ts`, the one table of what each phase looks like. A quiet chat is
`idle`.

| phase | world state |
| --- | --- |
| `thinking` `searching` `tool` `planning` `delegating` `streaming` `listening` | `working` |
| `queued` `retrying` `waiting_model` | `blocked`: live and held up, never painted as working |
| `approval` | `waiting_approval` |
| `input` | `needs_input` |
| `error` | `error` |
| `done` `idle` | `idle` |

## WS envelopes are SIGNALS, NEVER PAYLOADS

This is the load-bearing invariant, inherited verbatim from the DashboardLive contract
(`web/src/pages/dashboard/DashboardLive.tsx`).

`running_changed`, `sessions`, `update_progress`, `approval`, `approval_resolved` and
`refresh` each **nudge a debounced refetch** (600 ms, so a burst collapses into one
request). The gateway rings `running_changed` at most once per 150 ms window, for a row
added or removed or a phase or line that changed, and never for a running turn's liveness
re-send, so a phase frame of its own is no signal here. Not one field is ever read off an
envelope.
A socket reopening after a drop forces a full catch-up refetch; a visibility-gated
60 s poll covers the rest.

The buddy's presence (`#/buddy`, `web/src/lib/buddy/host.ts`) reads the same way:
`running_changed`, `waiting_changed` and `listening` each ring one read of the running list,
the waiting list and whether the microphone is live, a burst in one turn of the event loop is
one read, and no field is read off the frame. Its machine's inputs are those levels, diffed
against what it last showed.
In the buddy's own window the doorbell is the desktop shell's (`desktop/doorbell.js`): the
main process's one owner socket, signed in with the shell's session, rings the menu bar's read and
the buddy's page (a ring over the window's bridge carries nothing), so the page holds no socket of
its own. A burst inside 100 ms is one read; while the socket is down it retries with backoff and the
menu bar reads every 5 s. It raises a hidden buddy window only for a waiting item whose
`interrupt` is `deliver`.

Why it matters: envelope shapes drift per producer, several producers emit the same
type with different fields, and a stale process can emit a contradicting one. A surface
that renders envelope payloads renders whichever producer spoke last.

The rail is `web/src/lib/agentActivitySignals.test.tsx`. It delivers a `running_changed`
carrying deliberately **misleading** `phase`, `line`, `title`, `progress` and
`entities` fields and asserts the rendered scene came from the refetch — paired with a
positive control that the refetch demonstrably happened, because "no bad field
appeared" is vacuously true for a hook that did nothing at all.

## What a world may and may not do

A world **receives** `AgentActivityFeed` and renders it. It must not:

- call an endpoint, open a socket, or import `lib/api`;
- consume any other data hook (`useDashboardLive`, `useCachedData`, …);
- take host-specific props — a world is handed a contract, not wired to a page.

`web/src/pages/dashboard/world/agentWorldRender.test.ts*` enforces all of this
structurally against the first-party world's source, because a behavioural test cannot
see a fetch in a branch it did not reach.

### Motion

- **Smooth state interpolation.** An entity whose state changes MOVES to its new ring
  and crossfades its tone; it never teleports. The ease is a critically-damped
  approach, so a 144 Hz display and a 30 Hz one settle in the same wall-clock time.
- **`prefers-reduced-motion: reduce` is a STATIC LAYOUT** — not a slower animation.
  Orbit schedules **no animation frame at all** and paints one settled frame with
  `pulse` and `speed` at zero, at the same positions and tones the animated scene rests
  at (reduced motion must not cost information). Audited with a positive control that
  the animated path *does* schedule frames.
- **Rendering tier.** Orbit is a high-craft **canvas 2D** scene (layered additive glow,
  per-node tone crossfade, eased orbits) — the "high-craft canvas" half of a
  "WebGL/shader-grade OR high-craft canvas" choice. A `webgl` tier was drafted and
  removed rather than shipped untested: a shader pipeline cannot be exercised in jsdom,
  so it would have been a declared tier with an unverified runtime — a black rectangle
  for anyone whose shader compile failed. With no drawing context at all (headless, or
  canvas blocked by an extension) the world degrades to a DOM list of the same scene,
  never a blank box.

### Accessibility

A moving dot field is invisible to assistive tech. The canvas is `role="img"` named by
`sceneSummary()`, and the same sentence is rendered **visibly** beneath it, so nobody
depends on seeing the animation to know what is running.

## Coordination note — app-contributed worlds

**This is a doc note, not code.** Nothing in this repository loads a third-party world
today, and this section is the forward hook for the app platform.

When app-contributed worlds land there, the contract above is the seam and needs no
change. What the app platform has to add:

1. **A `world` UI-module kind** in the manifest's existing UI seam. A world is a
   render-only module: it declares no `permissions.api` and no `permissions.events`,
   because it makes no request and subscribes to nothing. That is the whole point of
   folding the data here — a world is the *cheapest possible* app contribution to
   consent to.
2. **`AgentActivityFeed` passed IN, not fetched.** The host owns the single
   `useAgentActivity()` instance and hands the value to the module. Two worlds mounted
   at once must not become two sockets and eight polls.
3. **A picker, and the same host chrome.** Which world renders is a user choice; the
   host keeps the `role="img"` name, the visible summary sentence, the empty/unknown
   states and the reduced-motion decision, so a third-party world cannot opt out of the
   accessibility or motion contract by omission. Pass `reducedMotion` in as a flag
   rather than letting each module ask the media query — an app that forgets to ask
   would animate for a user who asked it not to.
4. **No new provider type is needed for the read path.** An entity *provider* — an app
   contributing its own entities INTO the feed — is a separate, larger question: it
   widens `AgentActivityKind`, which every existing world switches on. Take it as its
   own change, after the render-only kind ships.

## Adding a field

Add it to `AgentActivityEntity`, populate it in the relevant `fold*` function, and give
it a row in the fold's test. **Never add a fetch to a world instead** — a field on the
contract is available to every world at once, including app-contributed ones; a fetch
in one world is available to nobody and breaks the seam.
