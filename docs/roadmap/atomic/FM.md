# FLUID-MOTION — atomic plans

**Source plan:** [`FLUID-MOTION`](../plans/FLUID-MOTION.md)  
**Code:** `FM`  
**Source status:** proposed

FLUID-MOTION is IN PROGRESS — **4 of 7 atoms done** (`FM-1` the physics foundation, `FM-3` the LiquidShape blob morph, `FM-5` route transitions, `FM-6` orchestrated entrances). It is a self-contained frontend motion plan (motion.ts, ui/motion/, tokens, router) that touches no cross-plan shared seams and rides existing cosmetic gates (bounciness/expressiveness/prefers-reduced-motion), so no lifecycle/migration re-scopes apply. Decomposed into 7 atoms across its 3 sessions: the physics foundation (FM-1), two independent morph primitives + their coherence pass (FM-2/3/4), route transitions and orchestrated entrances (FM-5/6), and the capstone budget proof + CI guard (FM-7). One declared cross-plan edge (DESIGN-SYSTEM-CONSISTENCY, already shipped v0.1.2) and one soft coordination edge (KNOWLEDGE-LIBRARY for a demo morph surface, with alternates available).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `FM-1` | ✅ | Physics preset system: named springs, gesture helpers, Motion tokens + author guide | `EXT:DESIGN-SYSTEM-CONSISTENCY:consistent components+tokens as the motion substrate (shipped v0.1.2)` | physics presets (snappy/smooth/fluid/playful) scale with the bounciness slider and zero out under prefers-reduced-motion; dragSpring/swipeDismiss shipped in motion.ts; new Motion tokens wired in tokenRegistry + tokens.css; docs/design/motion.md guide lets an author pick a preset and know the budget/reduced-motion constraints from the doc alone; 2-3 existing interactions (pressable/list-enter/overlay) adopt the presets with no hardcoded transitions left |
| `FM-2` | ⬜ | Morph.tsx shared-element wrapper + wire one real card→detail morph | `FM-1`, `EXT:KNOWLEDGE-LIBRARY:knowledge card→reading view surface for the demo morph (alternate surfaces — session rows→chat, loop cards→cockpit — usable if unavailable)` | a list card visibly morphs into its detail/expanded view and back via framer layout/layoutId; reduced-motion path yields instant swap; no layout thrash |
| `FM-3` | ✅ | LiquidShape.tsx fluid-blob shape morph (coral-tinted, expr()-scaled) | `FM-1` | shape morphs smoothly between states (SVG-path vs canvas-metaball decided by measurement in T2.2), integrates visually with DotGlow/WavyProgress without clashing, and honors reduced-motion (instant) and expr() scaling |
| `FM-4` | ⬜ | Coherence pass: unify Morph/LiquidShape/Disintegrate/Bud into one motion vocabulary | `FM-2`, `FM-3` | the morph family shares timing/curves and reads as one system on visual review; documented in motion.md; the card→reading morph + a liquid state transition both stay clean under prefers-reduced-motion and expressiveness=0 |
| `FM-5` | ✅ | Wire viewTransition() into hash-route changes (cosmetic-only) | `FM-1` | navigation crossfades/morphs; URL/state changes remain ungated on the transition and the frontend URL-state test still passes; reduced-motion → crossfade or none |
| `FM-6` | ✅ | Orchestrated staggered entrances for 2-3 key surfaces | `FM-1` | 2-3 key surfaces stagger their regions in via stagger() (expr()-scaled); entrances feel composed not busy; collapse cleanly under prefers-reduced-motion |
| `FM-7` | ⬜ | Motion budget proof: 60fps pass + reduced-motion/expressiveness=0 zero-motion CI guard | `FM-4`, `FM-5`, `FM-6` | 60fps verified on ChatPage + a cockpit with no jank; expressiveness=0 and prefers-reduced-motion both proven to yield instant/crossfade with zero springs; a reduced-motion assertion added to web CI where feasible; full-app motion pass holds at bounciness=1 (delightful) and bounciness=0 (calm) |

## Atom scopes

### `FM-1` — Physics preset system: named springs, gesture helpers, Motion tokens + author guide

**Status:** done (2026-08-13)

Session 1 — Physics system (T1.1–T1.3, V1); Contracts C1

**Done when:** physics presets (snappy/smooth/fluid/playful) scale with the bounciness slider and zero out under prefers-reduced-motion; dragSpring/swipeDismiss shipped in motion.ts; new Motion tokens wired in tokenRegistry + tokens.css; docs/design/motion.md guide lets an author pick a preset and know the budget/reduced-motion constraints from the doc alone; 2-3 existing interactions (pressable/list-enter/overlay) adopt the presets with no hardcoded transitions left

**Landed:** `physics.{snappy,smooth,fluid,playful}` on the plan's §C1 constants, all four routed
through `bouncy()` — the single place the bounciness slider enters the spring family and the single
place `prefers-reduced-motion` now returns `instant`. The alias set `springs.{gentle,snappy,bouncy}`
and the never-imported `pressable` object were DELETED rather than kept alongside (see the plan's
execution log for the full mapping — `bounce` went with them, so `physics` is the only
bounciness-scaled vocabulary left). `dragSpring()`/`dragElastic()`/`swipeDismiss()` ship with real
call sites (`Reorderable`, `Toaster`). Three new Motion tokens (`--drag-elastic`,
`--swipe-dismiss-velocity`, `--swipe-dismiss-distance`) round-trip registry → `tokens.css` →
`runtime.ts`. [`docs/design/motion.md`](../../design/motion.md) is the author guide. Adopted at five
live interactions. `web/src/design/motion.test.ts` (35 tests) pins both dials per preset, the gesture
thresholds and the token round trip; `web/src/design/motionSliders.test.tsx` (3) pins that the
generated Motion sliders render and that moving one writes `runtime`.

**Remains for later atoms (unchanged scope):** the morph primitives (`FM-2`/`FM-3`) and their
coherence pass (`FM-4`), route transitions (`FM-5`), orchestrated entrances (`FM-6`), and the 60fps
budget proof + CI zero-motion guard (`FM-7`). `FM-7` inherits a working reduced-motion assertion to
build its guard on rather than starting from a comment.

### `FM-2` — Morph.tsx shared-element wrapper + wire one real card→detail morph

**Status:** todo

Session 2 — Liquid morphing (T2.1); Contracts C2 (Morph)

**Done when:** a list card visibly morphs into its detail/expanded view and back via framer layout/layoutId; reduced-motion path yields instant swap; no layout thrash

### `FM-3` — LiquidShape.tsx fluid-blob shape morph (coral-tinted, expr()-scaled)

**Status:** done

Session 2 — Liquid morphing (T2.2); Contracts C2 (LiquidShape)

**Done when:** shape morphs smoothly between states (SVG-path vs canvas-metaball decided by measurement in T2.2), integrates visually with DotGlow/WavyProgress without clashing, and honors reduced-motion (instant) and expr() scaling

### `FM-4` — Coherence pass: unify Morph/LiquidShape/Disintegrate/Bud into one motion vocabulary

**Status:** todo

Session 2 — Liquid morphing (T2.3, V2)

**Done when:** the morph family shares timing/curves and reads as one system on visual review; documented in motion.md; the card→reading morph + a liquid state transition both stay clean under prefers-reduced-motion and expressiveness=0

### `FM-5` — Wire viewTransition() into hash-route changes (cosmetic-only)

**Status:** done (2026-08-16)

Session 3 — Route transitions (T3.1); Contracts C3

**Done when:** navigation crossfades/morphs; URL/state changes remain ungated on the transition and the frontend URL-state test still passes; reduced-motion → crossfade or none

**DONE.** `viewTransition()` had **zero call sites** before this atom — FM-1 shipped the wrapper
and nothing used it, so "navigation is instant" (§Landscape item 4) was literally an unwired
helper. It is now wired at exactly one seam: the `hashchange` listener in
`web/src/app/useHashRoute.ts`. Every navigation a user performs arrives there — a nav click via
`apply`'s push branch, and browser back/forward — so one listener crossfades all of them with no
page opting in.

**How "ungated" is structural, not promised.** The URL write (`location.hash =` /
`history.replaceState`) stays in `apply`, *outside* the transition, so the address bar and history
can never wait on an animation. The state commit is inside, so `viewTransition` was hardened to run
its callback **exactly once on every path**: no API (jsdom + any browser without View Transitions),
a `startViewTransition` that throws, and an animation that never settles (the transition object is
dropped, never awaited — the function is not `async` and returns `void`). Only the `route` (first
path segment) animates: a pushed `?open=<id>` panel, a replaced tab/filter/search keystroke and
`replace` redirects all stay instant, because fading the page under an opening panel or once per
keystroke is exactly the "motion that fights the task" the plan's soul guardrail rules out.
Reduced motion resolves to **none** (the instant swap), gated inside `viewTransition` at call time
with no `reduce` parameter for a call site to forget or overrule — FM-1's unused `reduce` argument
was deleted rather than left as a second, escapable gate.

**DEVIATION — `viewTransition`'s signature changed and it gained an error contract.** FM-1's
`viewTransition(update, reduce = false)` would have dropped the update entirely if
`startViewTransition` threw, and let any caller pass `reduce: false` over the user's OS setting.
Both were fixed in place rather than wrapped: the parameter is gone (zero callers had it) and the
throw is recovered behind a run-once latch.

**DISCOVERY — falsification found a swallowed error.** The `catch` that recovers a refused
transition sits on the same path as an error thrown by `update` itself, so the first version
swallowed a render error and would have left a silently blank page. `update`'s own error is now
re-raised and only a refusal to *start* is recovered. Two mutations also reded nothing at first and
both were real test gaps, now closed: the route-only gate was only "tested" through `replace`
updates that never emit a `hashchange` at all, and the `flushSync` ordering property was unasserted
until a test read the DOM from *inside* the transition callback. One mutation still reds nothing by
design — deleting the `typeof startViewTransition !== 'function'` fast path, because the `catch` is
the layer that actually carries update-survival (calling `undefined` throws). It is kept as intent
and to keep a third of browsers off exception-driven control flow, and that is recorded in the
function's doc comment rather than pinned by a test.

**Rails:** `web/src/app/routeTransition.test.tsx` (14) — the three failure modes asserted on the
route STATE rather than the URL (a URL-only assertion passes with the commit trapped in a broken
transition), reduced motion, back/forward, both non-animating classes, the `applied` mirror, the
flushSync capture ordering, and that navEpoch/sub/query semantics are untouched;
`web/src/design/motion.test.ts` grows 8 for `viewTransition` itself. The named frontend URL-state
test — `tests/test_url_navigation_doctrine.py` (6) — still passes, including
`test_router_still_owns_history_mechanics`, which is what keeps the `location.hash`/`replaceState`
mechanics in the router file where this atom left them.

**Not done here:** `FM-7` still owns the 60fps budget proof and the CI zero-motion guard; it
inherits a wired route transition and a reduced-motion assertion to build on. The shared-element
*morph* half of "crossfades/morphs" is `FM-2`'s `Morph.tsx` — this atom ships the crossfade.

### `FM-6` — Orchestrated staggered entrances for 2-3 key surfaces

**Status:** done (2026-08-17)

Session 3 — orchestration (T3.2)

**Done when:** 2-3 key surfaces stagger their regions in via stagger() (expr()-scaled); entrances feel composed not busy; collapse cleanly under prefers-reduced-motion

Three surfaces adopt it, chosen for having a real BAND STACK rather than one list in one column: the
**dashboard home** (8 regions), **Discover** (intro + one region per server-authored area, 6 against
`demo-home`) and **the inbox** (2 — the source-health banner, then the queue column). `regionStagger()`
in `motion.ts` is the single decision, `stagger(expr(0.05, 0.4))`, and it is deliberately
PARAMETERLESS so no surface can pick its own cascade; its one consumer is a new pair in the existing
motion family, `ui/motion/{EntranceGroup, EntranceRegion}`, on the existing `listItemEnter` variant.

**Reduced motion is an absence, not a speed:** `regionStagger()` returns `null` and both components
render plain `<div>`s — measured in Chrome as `data-entrance="none"`, zero inline opacity/transform,
minimum computed opacity 1, zero running animations, all content still on screen. **An entrance never
gates content:** at the dashboard's first commit (minimum region opacity 0) all eight headings, 29
buttons and the composer were already in the document.

**Replay rule:** the entrance plays on the MOUNT of its `EntranceGroup` — once per navigation for a
route surface — and a re-render never replays it. The group goes above every data-dependent branch
when its regions are static, or on the loaded column when the regions ARE the data; nothing is keyed
on data. `useCachedData` holding its last value on a same-key revalidation is what makes the first
placement safe. Driven: a real Discover dismiss (POST + refetch) left the group and 5 surviving
regions as the same DOM nodes with minimum opacity 0.999.

Measured cascade on the dashboard: 40/62/104/145/196/238/281/329 ms — ~44ms apart, matching
`expr(0.05, 0.4)` at the default expressiveness 0.8. Rails: `design/motion.test.ts` +6,
`ui/motion/Entrance.test.tsx` (6), `ui/motion/Entrance.reducedMotion.test.tsx` (5, own file — framer
caches its reduced-motion probe in a module singleton), `pages/discover/entranceReplay.test.tsx` (3),
`pages/surfaceEntranceAdoption.test.ts` (8, which also records why settings' re-packing masonry and
`TaskBoard`'s drag surface are NOT candidates).

### `FM-7` — Motion budget proof: 60fps pass + reduced-motion/expressiveness=0 zero-motion CI guard

**Status:** todo

Session 3 — budget proof (T3.3, V3)

**Done when:** 60fps verified on ChatPage + a cockpit with no jank; expressiveness=0 and prefers-reduced-motion both proven to yield instant/crossfade with zero springs; a reduced-motion assertion added to web CI where feasible; full-app motion pass holds at bounciness=1 (delightful) and bounciness=0 (calm)

