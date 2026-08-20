# FLUID-MOTION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/FM.md`](../atomic/FM.md) as 7 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Fluid Motion — Liquid Morphing & Motion Physics

**Status:** DESIGNED — created 2026-07-18 (roadmap rev 10; owner ask: liquid morphing and motion physics animation improvements)
**Created:** 2026-07-18
**Wave:** 3 — the polish layer; lands on the consistency baseline (plan 51) so motion animates coherent components, not drifting ones.
**Depends on:** DESIGN-SYSTEM-CONSISTENCY (51 — consistent components + tokens are the substrate; motion without consistency amplifies drift). Builds directly on the shipped motion system.
**Scope:** the motion system is already sophisticated (`motion.ts` with physics springs, a `bounciness`/`expressiveness` runtime, `viewTransition()`, framer-motion 11, a `ui/motion/` library). This plan pushes it toward the brand's stated ceiling — **liquid morphing** (shared-element/shape morphs between states) and **deeper motion physics** (gesture-driven springs, fluid layout transitions) — while keeping it *budgeted* (the `expressiveness`/`bounciness` sliders scale it, reduced-motion zeroes it). **Soul guardrail (straight from `PRODUCT.md`):** "playful within discipline" — motion is real but budgeted; the task always wins; every animation has a reduced-motion alternative and all springy personality collapses to instant/crossfade under `prefers-reduced-motion`. No motion that delays a user action or fights readability. This is the "earned, tunable playfulness" the product already promises — delivered, not invented.

---

## Context (code recon, 2026-07-18)

- **The motion foundation is strong** (`web/src/design/motion.ts`): `spring`, `bounce`, a `bouncy(stiffness, dampingAtPlayful, calmDamping)` that reads `runtime.bounciness`, `ease`, `duration`, `pressable`, `springs`, `stagger()`, `messageEnter`/`overlayEnter`/`thinkingPulse`/`listItemEnter` variants, `expr(max, floor)` + `exprHeavy()` (expressiveness scaling), and **`viewTransition(update, reduce)`** (the View Transitions API wrapper). framer-motion `^11`. `ui/motion/`: `Bud`, `Disintegrate`, `Expandable`, `Reorderable`, `ContextMenu`. `tokens.css`: motion curves (`--ease-emphasized*`), motion multipliers read by canvas, `@keyframes` (status-pulse, skeleton-shimmer, text-shimmer, blueprint-draw/breathe/scan), and a global `prefers-reduced-motion` reset (`tokens.css:374`). `tokenRegistry` has a `Motion` group.
- **What's missing for "liquid morphing + physics":** (1) **shared-element / layout morphing** between routes and states (framer's `layoutId`/`layout` is available but not systematically used — cards don't morph into detail views, list items don't morph into their expanded form); (2) **liquid/blob shape morphing** (the coral identity + `DotGlow`/`WavyProgress` hint at it, but there's no reusable fluid-shape morph primitive); (3) **gesture-driven physics** (drag/swipe with spring return — `Reorderable` exists but gesture physics aren't a general capability); (4) **route transitions** (navigation is instant; `viewTransition` exists but isn't wired to the router); (5) the physics constants aren't a documented, tokenized *system* an author reaches for.

## Design

- **S1 — Motion physics as a system:** formalize the physics layer in `motion.ts` — a documented set of named spring presets (snappy/smooth/fluid/bouncy) all scaled by `bounciness`, gesture-spring helpers (drag-with-spring-return, swipe-to-dismiss with velocity), and `expr()`-scaled intensities — surfaced in the `Motion` token group + a `docs/design/motion.md` author guide. No new dep (framer-motion 11 covers it). Everything routes through the reduced-motion + slider gates already in place.
- **S2 — Liquid morphing primitives:** (a) **shared-element morph** — a `<Morph layoutId>` wrapper (framer `layout`/`layoutId`) so a list card morphs into its detail/expanded view and back (knowledge cards→reading view, session rows→open chat, loop cards→cockpit); (b) **fluid-shape morph** — a reusable liquid/blob primitive (SVG path or canvas metaball morphing between shapes, coral-tinted, `expr()`-scaled) for state transitions, loading→loaded, and ambient surfaces; (c) integrate with the existing `Disintegrate`/`Bud` so the morph vocabulary is coherent, not parallel.
- **S3 — Route transitions + orchestration + budget proof:** wire `viewTransition()` into the hash router so navigation crossfades/morphs (respecting reduced-motion + the URL-state doctrine — the transition is cosmetic, never state); orchestrated entrance sequences for key surfaces (a page's regions stagger in via `stagger()`); and a **budget proof** — a motion-performance pass (60fps target, no jank on the big pages; `expressiveness=0` and `prefers-reduced-motion` both produce instant/crossfade with zero springs) verified and CI-guarded where feasible.

## Contracts & Interfaces (extends `motion.ts` + `ui/motion/`; conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Physics presets (`web/src/design/motion.ts` additions; all bounciness/expressiveness-scaled)
```typescript
export const physics = {
  snappy: () => bouncy(520, 30, 40),   // quick, minimal overshoot
  smooth: () => bouncy(320, 34, 38),   // default UI
  fluid:  () => bouncy(180, 26, 34),   // liquid, generous settle
  playful:() => bouncy(420, 14, 34),   // max overshoot at bounciness=1
}
export function dragSpring(): Transition   // gesture return with velocity
export function swipeDismiss(velocity: number): { dismiss: boolean; transition: Transition }
// ALL read runtime.bounciness/expressiveness and collapse under prefers-reduced-motion (existing gates).
```

### C2 — Morph primitives (`web/src/ui/motion/`)
```typescript
// Morph.tsx — shared-element morph
<Morph layoutId="knowledge-item-42" reduce={prefersReducedMotion}>...</Morph>
// LiquidShape.tsx — fluid blob morph between shape states
<LiquidShape from="circle" to="squircle" active={loaded} intensity={expr(1)} />
```
Both honor reduced-motion (→ instant swap/crossfade) and `expr()`. Registered in `ui/motion/index.ts`.

### C3 — Route transition (`web/src/app/` router integration)
`viewTransition()` (existing) wrapped around hash-route changes; a `reduce` path (crossfade or none) under reduced-motion. **The transition is cosmetic only** — URL/state changes are not gated on it (URL-state doctrine intact; the frontend URL test must still pass).

### Integration points
- **Extends:** `motion.ts` (physics presets), `ui/motion/*` (Morph, LiquidShape), `tokens.css`/`tokenRegistry` Motion group (new tokens), the router (route transitions).
- **Consumed by:** knowledge library (49 — card→reading morph), session management (50 — row→chat morph), loop cockpits, ambient surfaces (20 — liquid state transitions), onboarding (43 — orchestrated entrance).
- **Depends on:** 51 (consistent components to morph between).
- **Gates (existing, reused):** `runtime.bounciness`, `runtime.expressiveness`, `prefers-reduced-motion` — no new gate; motion is never on a lifecycle gate (it's cosmetic).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — Physics system

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `physics` presets + gesture helpers (`dragSpring`, `swipeDismiss`) in `motion.ts`, all slider+reduced-motion-gated; new Motion tokens in the registry | `web/src/design/motion.ts`, `tokenRegistry.ts`, `tokens.css` | presets scale with the bounciness slider; `prefers-reduced-motion` → zero spring (test both extremes) |
| T1.2 | `docs/design/motion.md` author guide: when to use which preset, the budget rules, the reduced-motion contract | new doc | an author can pick a preset + know the constraints from the doc alone |
| T1.3 | Adopt presets in 2-3 existing interactions (pressable, list enter, overlay) replacing ad-hoc transitions — proof the system works without regressions | those components | motion identical-or-better; no hardcoded transitions left in the touched components |
| V1 | Validation: bounciness slider 0→1 visibly scales the presets; reduced-motion zeroes them; 60fps on the touched surfaces | — | holds |

### Session 2 — Liquid morphing

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `Morph.tsx` shared-element wrapper (framer `layout`/`layoutId`) + reduced-motion path; wire one real morph (knowledge card → reading view, coordinate with plan 49) | `web/src/ui/motion/Morph.tsx`, a consuming page | the card visibly morphs into its detail view + back; reduced-motion → instant; no layout thrash |
| T2.2 | `LiquidShape.tsx` fluid-blob morph (SVG path or canvas metaball, coral-tinted, `expr()`-scaled) + reduced-motion path | `web/src/ui/motion/LiquidShape.tsx` | shape morphs smoothly between states; integrates visually with `DotGlow`/`WavyProgress` (coherent, not clashing) |
| T2.3 | Coherence pass: ensure Morph/LiquidShape/Disintegrate/Bud form one vocabulary (shared timing/curves), documented in `motion.md` | `ui/motion/index.ts`, `motion.md` | the morph family reads as one system (visual review) |
| V2 | Validation: the card→reading morph + a liquid state transition feel native and budgeted; reduced-motion + expressiveness=0 both clean | — | holds |

### Session 3 — Route transitions + orchestration + budget proof (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Wire `viewTransition()` into hash-route changes (cosmetic-only; URL/state ungated; reduced-motion → crossfade/none) | `web/src/app/` router | navigation crossfades/morphs; the frontend URL-state test still passes; reduced-motion respected |
| T3.2 | Orchestrated entrances for 2-3 key surfaces (regions stagger via `stagger()`, `expr()`-scaled) | those pages | entrances feel composed, not busy; collapse cleanly under reduced-motion |
| T3.3 | Budget proof: a motion-performance pass (60fps on ChatPage + a cockpit; profile for jank) + assert `expressiveness=0` and reduced-motion both yield instant/crossfade with zero springs; add a reduced-motion assertion to web CI where feasible | perf profiling, a CI check | 60fps verified; both "off" states proven zero-motion; regression guard in CI |
| V3 | Validation: full-app motion pass at bounciness=1 (delightful), bounciness=0 (calm), reduced-motion (still); performance clean | — | holds |

## Owner tasks (real world)
1. **Taste-drive the motion** at every slider setting (S1-S3) — liquid morphing and physics are deeply subjective; the presets' feel is yours to tune (the plan makes them tunable precisely so you can). Budget ~30 min per session dialing constants.
2. Confirm the **default** bounciness/expressiveness for new users (the sliders exist; the default is a brand call — proposal: mid, so the personality is visible but calm).
3. Sign off that motion **never delays an action** in your daily use — if any transition feels like it's in the way, that's a bug to cut, not tune.

## Risks & open questions
- **Motion as the "AI-generated feel" tell** — the design skill warns extra animation can read as generated; the mitigation is the budget discipline + orchestration-over-scatter (one considered morph beats ten scattered effects). Restraint is in the plan's soul guardrail.
- **Performance on the mega-pages** — Morph/layout animations can thrash; T3.3's budget proof is the gate; if a surface can't hit 60fps, it gets a simpler transition, not a dropped frame.
- **Open:** canvas metaball vs SVG-path for `LiquidShape` — decide in T2.2 by measuring (canvas scales better for many shapes; SVG is simpler for one); no premature choice.

## Execution log

### 2026-08-13 — `FM-1` (S1: T1.1–T1.3, V1; contract §C1) — **DONE**

`physics.{snappy, smooth, fluid, playful}` ships on §C1's constants verbatim, and all four are
`bouncy()` springs — which makes `bouncy()` the single seam where the bounciness slider enters the
spring family and the single place reduced motion collapses it. `dragSpring()`, `dragElastic()` and
`swipeDismiss(velocity, offset)` ship with real call sites. `docs/design/motion.md` is the author
guide. Two rails: `web/src/design/motion.test.ts` (35 tests — the presets, the dials, the token round
trip) and `web/src/design/motionSliders.test.tsx` (3 — the generated Design-panel sliders actually
render, and moving one is what writes `runtime`, closing the loop the done-when names).

**DEVIATION — preset-name reconciliation, and what §C1 collided with.** The recon that opened this
atom assumed the presets were new; three of the four *names* already existed on other objects, so
adding `physics` beside them would have put two names on one decision. Resolved by DELETING the
losers in the same change (no aliases, no re-exports — the pre-1.0 clean break), because one of them
declared itself an alias set in its own doc comment ("These are aliases/companions to the
spring/bounce tiers above"):

| Deleted | → | Replacement | Constants before → after (stiffness / damping playful→calm) |
|---|---|---|---|
| `springs.gentle` | → | `physics.fluid` | `spring.spatialSlow` 200/26 **unscaled** → 180 / 26→34 **scaled** |
| `springs.snappy` | → | `physics.snappy` | `spring.spatialFast` 800/34 **unscaled** → 520 / 30→40 **scaled** |
| `springs.bouncy` | → | *(nothing — zero call sites)* | successor is `physics.playful` |
| `bounce.subtle` | → | `physics.snappy` | 520 / 26→40 → 520 / 30→40 (near-identical) |
| `bounce.playful` | → | `physics.playful` | 600 / 16→42 → 420 / 14→34 |
| `bounce.lift` | → | `physics.playful` | 300 / 22→34 → 420 / 14→34 — **the one LOSSY row** |
| `bounce.settle` | → | `physics.fluid` | 220 / 24→30 → 180 / 26→34 (near-identical) |
| `pressable` | → | *(nothing — zero call sites)* | its contents were a dead duplicate of `Button`'s inline `whileTap`/`whileHover`; the real press now takes `physics.snappy` |

`springs` had 2 call sites and one member nobody imported, so retiring it was ~2 lines. `bounce` had
~35, and retiring it too was the judgement call: keeping it would have left `bounce.playful` and
`physics.playful` side by side, which is exactly the dual vocabulary this reconciliation exists to
remove. Every edit was a single identifier; `grep -r 'bounce\.\|springs\.' web/src` is empty of code
hits. The lossy row is `lift` (a float-up entrance): mapping it to `playful` keeps its overshoot but
quickens it (300 → 420 stiffness) — the alternative, `smooth`, is critically damped and would have
deleted the bounce entirely, which is the worse loss for an entrance. Five entrances are affected
(`StreamingIndicator`, `DialogShell`, `UpdateProgressOverlay`, `Composer`, `Modal`); retuning is now
four numbers in `motion.ts` rather than a sweep, which is owner task #1's whole point.

Kept, and NOT a third vocabulary: `spring.{spatialDefault, spatialFast, spatialSlow, effects}` — the
raw unscaled tiers, and `effects` is the critically damped transition a fade must use. The rule the
guide states is `spatial/character → physics.*`, `opacity/colour → spring.effects`. A closed-set test
asserts `physics` has exactly four members and that `springs`/`bounce`/`pressable` are not exported
again.

**DISCOVERY — the reduced-motion claim was NOT true, and a comment said it was.** `motion.ts` and
`runtime.ts` both asserted that reduced motion "overrides everything to near-static" via the root
`<MotionConfig reducedMotion="user">`. That root wrapper is real (`App.tsx` mounts it) and it does
neutralise transform/layout animation — but it does **not** touch the transition objects, so every
preset still resolved to a live spring, and any preset applied to a non-transform property (opacity,
a `filter`) still animated. §C1's "ALL … collapse under prefers-reduced-motion (existing gates)" was
therefore describing a gate that did not exist for the springs themselves. `bouncy()` now returns
`instant` (`{ type: 'tween', duration: 0 }`) when the query matches, which zeroes all four presets
plus `dragSpring()` and both `swipeDismiss()` branches. The explicit `type: 'tween'` is load-bearing:
four call sites spread a preset and override `stiffness`, and without a declared type the leftover
`stiffness` lets Framer infer a spring again — the collapse would leak straight back through the
spread. Pinned by its own test.

**DISCOVERY — a variant that carries a preset freezes it at import.** `overlayEnter` held
`transition: bounce.playful` in a module-level object literal, so the getter ran ONCE at import: every
menu, popover and context menu in the app used whatever bounciness (and whatever reduced-motion
answer) was true at module load and ignored the slider for the rest of the session. `overlayEnter`
and `listItemEnter` are now Framer dynamic variants (`animate: () => ({ … })`), resolved per
animation. This is the defect class the "scale with the slider" done-when would otherwise have passed
on a technicality — the preset scaled, but the surface using it did not.

**Adoption (T1.3) — five live interactions, no hardcoded transitions left in them:**
`Button` press+hover and `IconButton` press → `physics.snappy`; `overlayEnter` → `physics.playful`;
`listItemEnter` → `physics.smooth` (was a hardcoded `{ duration, ease }` tween); `Reorderable`'s drop
settle → `dragSpring()`; `Toaster`'s swipe-to-dismiss → `swipeDismiss()` + `dragElastic()`, which
deletes its `info.offset.x > 80 || info.velocity.x > 500` literals in favour of the two tokens. The
toast now also leaves on the gesture's own accelerating curve rather than a spring (an element that
is leaving must not overshoot back into the surface it left); a timer expiry keeps the calm spring.
`Discover`'s deck transition was `{ ...bounce.settle, ...spring.spatialDefault }` — the second spread
overwrote every field of the first, so the tier contributed nothing; it is now `physics.fluid`.

**DEVIATION — `swipeDismiss` takes a second argument.** §C1 declares
`swipeDismiss(velocity: number)`. Velocity alone cannot express a deliberate slow haul all the way
across the card, which the surface being adopted (`Toaster`) already supported and which reads as
broken when it stops working. The shipped signature is
`swipeDismiss(velocity: number, offset = 0)` — a superset, so §C1's call shape stays valid — and the
two thresholds are OR'd, both from tokens.

**DISCOVERY — token round trip: `--bounciness`, `--expressiveness` and `--dot-size` were registered
with no `tokens.css` default at all.** The registry says its defaults mirror `tokens.css`; for those
three there was nothing to mirror. Harmless today (nothing in CSS reads them) but it is exactly the
half-wired shape this atom's own tokens must avoid, so all three are now declared. The new rail
asserts EVERY scalar token carrying a `runtimeKey` is declared in `tokens.css` **with a matching
default** and has its key present in `runtime` — which is what caught them.

**New Motion tokens** (registry `Motion` group → `tokens.css` → `runtime.ts`, read by the gesture
helpers, never `getComputedStyle`): `--drag-elastic` (0.9), `--swipe-dismiss-velocity` (500 px/s),
`--swipe-dismiss-distance` (80px). No new slider UI was needed or added — the Motion group is
generated from the registry, which is also why the bounciness slider the done-when refers to already
existed.

**Falsified, both directions:** deleting `bouncy()`'s reduced-motion gate reddened 8 tests (the four
preset zero-outs, the spread-leak guard, both variants' reduced-motion half, `dragSpring`);
neutralising the bounciness interpolation reddened 8 (the four per-preset scaling tests, the
read-at-animation-time test, both variants, `dragSpring`). Restored: 35/35.

**Gate:** `make lint` rc 0 · `npm run typecheck:web` clean · full `npx vitest run` **218 files /
2146 tests passed, 0 failed** (216/2108 + this atom's 38) · `npm run build` rc 0 · full
`pytest -q --timeout=600` **19001 passed / 30 skipped / 12 xfailed / 0 failed** (baseline; no Python
touched). No visual snapshot updated — `web/e2e/visual.spec.ts` is not wired into any CI job (the web
job runs typecheck, vitest, build and `smoke:render` only), and spring constants do not move a
settled screenshot.

**Live validation (V1) — measured on the BUILT bundle**, isolated home `/private/tmp/fm1-live/home`,
loopback-only gateway, never `~/.personalclaw`. All five Motion sliders render in
Settings → Design → Backdrop & motion, each named with its own named reset
("Reset Drag elasticity"), reading `1.00×` / `0.80×` / `0.90×` / `500.0px/s` / `80px` — the `px/s`
unit exists because a unitless scalar renders as a multiplier and "500.00×" is nonsense for a
velocity. Then the SAME overlay (the composer's model popover, which is `overlayEnter` →
`physics.playful`) sampled frame by frame while opening:

| State | scale path | peak | distinct sampled values |
|---|---|---|---|
| bounciness **1** | 0.96 → overshoot → 1 | **1.0128** | 34 |
| bounciness **0** (moved via the real slider) | 0.96 → 1 | **1.0002** | 19 |
| `prefers-reduced-motion` | 0.96 → 1 | **1.0000** | **2** — an instant swap |

Gesture, driven with real pointer events: a toast hauled 144px at ~375px/s (deliberately UNDER the
500px/s flick threshold) followed the finger 124px — `dragElastic()`'s 0.9 — and dismissed on the
DISTANCE threshold, the case §C1's velocity-only signature would have missed. A 40px slow drag under
both thresholds sprang back to `x=0` and the toast survived, so the control discriminates rather than
always firing. Zero console errors across the pass.

**Not in this atom, by scope:** `expressiveness=0`/`bounciness=0`/reduced-motion zero-motion CI
guard and the 60fps pass are `FM-7`, which now inherits a working assertion instead of a comment.

### 2026-08-16 — `FM-5` (S3: T3.1; contract §C3) — **DONE**

§Landscape item 4 said "navigation is instant; `viewTransition` exists but isn't wired to the
router". It was more literal than that: `viewTransition()` had **zero call sites** anywhere in the
repo — FM-1 shipped the wrapper and nothing consumed it. It is now wired at exactly one seam, the
`hashchange` listener in `web/src/app/useHashRoute.ts`. Every navigation a user performs lands
there — a nav click through `apply`'s push branch, and browser back/forward — so a single listener
crossfades all of them and no page opts in. The curve and duration are declared in `tokens.css`
under `::view-transition-old/new(root)` so a page change moves on the app's own
emphasized-decelerate curve rather than the UA's default; that rule is taste, the JS is the
mechanism.

**§C3's "cosmetic only" is structural here, not a promise in a comment.** The URL write
(`location.hash =` / `history.replaceState`) stays in `apply`, *outside* the transition, so the
address bar and history can never wait on an animation. The route commit is inside it, so
`viewTransition` was hardened to run its callback **exactly once on every path**: no API (jsdom, and
any browser without View Transitions), a `startViewTransition` that throws, and an animation that
never settles — the transition object is dropped rather than awaited, and the function is
deliberately not `async` so `await`ing it is not expressible. `flushSync` on the commit is required
rather than defensive: the browser captures the "after" frame as soon as the callback returns, while
React 18 would commit a plain `setState` later on the scheduler, so without it both snapshots are
the old frame and the crossfade animates nothing.

**Only the `route` animates**, and that gate is load-bearing rather than decorative. A pushed
`?open=<id>` detail panel reaches the same `hashchange` seam a nav click does, so without the route
comparison the whole page would fade underneath an opening panel; replaced tab/filter/search
updates and `replace` redirects stay instant for the same reason (the soul guardrail's "no motion
that delays a user action or fights readability"). Reduced motion resolves to **none** — the instant
swap — gated inside `viewTransition` at call time.

**DEVIATION — FM-1's `reduce` parameter was deleted, not passed.** `viewTransition(update, reduce)`
let any call site pass `reduce: false` straight over the user's OS setting, and it had zero callers
to migrate. The reduced-motion read moved inside the function so there is one gate, unforgettable
and un-overridable, rather than a second escapable one at each call site. FM-1's version also
dropped the update entirely when `startViewTransition` threw; that is now recovered behind a
run-once latch.

**DISCOVERY — falsification found a swallowed render error.** The `catch` recovering a refused
transition sits on the same path as an error thrown by `update` itself, so the first version
swallowed a render error and would have left a silently blank page with nothing in the console.
`update`'s own error is now re-raised and only a refusal to *start* is recovered. Two mutations also
reded nothing on the first pass and both were genuine test gaps rather than redundant code: the
route-only gate was only exercised through `replace` updates, which never emit a `hashchange` at
all, and the `flushSync` ordering property was unasserted until a test read the DOM from *inside*
the transition callback (where the compositor stands). One mutation still reds nothing by design —
deleting the `typeof startViewTransition !== 'function'` fast path — because the `catch` is the layer
that actually carries update-survival: calling `undefined` throws a `TypeError` the catch recovers.
The guard is kept as a statement of intent and to keep roughly a third of browsers off
exception-driven control flow on every navigation, and that reasoning is recorded in the function's
doc comment instead of being pinned by a test that cannot exist.

**Rails.** `web/src/app/routeTransition.test.tsx` (14) asserts the three failure modes on the route
STATE rather than the URL — the URL write is outside the transition by construction, so a URL-only
assertion passes even with the commit trapped inside a broken transition — plus reduced motion,
back/forward, both non-animating classes, the `applied` mirror across a return to the starting
route, the flushSync capture ordering, and that navEpoch/sub/query semantics are untouched.
`web/src/design/motion.test.ts` grows 8 for `viewTransition` itself. The frontend URL-state test the
done-when names, `tests/test_url_navigation_doctrine.py` (6), still passes — including
`test_router_still_owns_history_mechanics`, which is what keeps the `location.hash`/`replaceState`
mechanics in the router file where this atom left them. `docs/design/motion.md` §6 documents the
one rule (a view transition may never gate a state change) with the wrong/right pair.

**Not in this atom, by scope:** the shared-element *morph* half of §C3's "crossfades/morphs" is
`FM-2`'s `Morph.tsx`; this atom ships the crossfade. `FM-7` still owns the 60fps budget proof and
the zero-motion CI guard, and now inherits a wired route transition to measure.

### 2026-08-17 — `FM-6` (S3: T3.2; contract §C3) — **DONE**

Three surfaces now orchestrate their entrances, and they were chosen for having a real BAND STACK
rather than one list in one column: **the dashboard home** (`pages/dashboard/DashboardPage.tsx`, 8
regions — the launcher, the sub-`lg` Hero Pulse strip, the two prime grid rows and the four
single-widget bands), **Discover** (`pages/discover/DiscoverPage.tsx`, the intro plus one region per
server-authored area — 6 measured against `demo-home`), and **the inbox**
(`pages/inbox/InboxPage.tsx`, 2 — the source-health banner, then the queue column: context first,
then the work). The inbox has exactly two regions because that is its whole body; the other two are
where the cascade actually reads.

**One mechanism, and the surfaces do not own it.** `regionStagger()` in `motion.ts` is the single
decision — `stagger(expr(0.05, 0.4))`, and **`null` under `prefers-reduced-motion`**. It is
deliberately PARAMETERLESS: a `step` argument would let each page pick its own cascade and no test
could notice the drift, so "every surface cascades identically" is checkable instead of
aspirational (a rail asserts `regionStagger.length === 0`, and that `regionStagger` has exactly one
consumer in the whole frontend). The consumer is one new pair in the existing motion family,
`ui/motion/Entrance.tsx` — `EntranceGroup` (the orchestrator, replacing the column div a page
already had, so the entrance costs no extra DOM) and `EntranceRegion` (one band, on
`listItemEnter`, the same variant a list row uses). No second stagger, no second variant.

**Reduced motion is an ABSENCE, and the branch is structural.** `regionStagger()` returning `null`
makes both components render plain `<div>`s — no variants, no hidden initial state, no transition,
nothing for a "fast cascade" tier to hide in. Measured in Chrome with the media query forced on:
all three surfaces reported `data-entrance="none"`, zero inline `opacity`/`transform`, minimum
computed opacity **1**, `getAnimations().length === 0`, and every heading and control still on
screen (8/2/5 regions, 29/16/20 buttons). A region reads that decision from the group via context
rather than re-asking, which is load-bearing rather than tidy: a region that answered for itself
could end up variant-driven under a plain parent if the query flipped between the two renders, and a
variant-driven region with nothing to propagate the `animate` label sits at `opacity: 0` **forever**.
The context default is `false`, so forgetting the group costs the entrance, never the content.

**The replay rule: an entrance plays on the MOUNT of its group, and nothing re-renders it.** For a
route surface that is once per navigation (`App.tsx` keys the route wrapper on the route). A
WebSocket push, a `refresh()`, a filter change or an opening panel are re-renders and are free. Two
placement rules carry it, and they are what a call site can get wrong: put the group ABOVE every
data-dependent branch when its regions are static (the dashboard, the inbox), or ON the loaded
column when the regions ARE the data (Discover's areas); and never key a group or a region on data.
`lib/useCachedData` is what makes the first sufficient — a same-key revalidation HOLDS the last
value instead of dropping to `undefined`, so a refresh cannot flip a surface back through its
skeleton and remount the group underneath. Both halves were driven, not argued: an inbox
query-refinement left `minOpacity 0.999` with byte-identical region nodes, and a real Discover
dismiss (POST + refetch, one area emptied and dropped) left `minOpacity 0.999`, the same group node
and 5 of the surviving regions the same DOM nodes.

**DEVIATION — settings home was rejected as the third surface, on a measured mechanism.**
`SettingsHome`'s masonry re-packs on every commit and re-parents blocks between columns, so a
staggered block would REMOUNT and replay its entrance on any resize or async card load. `TaskBoard`
was rejected too (a `LayoutGroup` + `AnimatePresence` drag surface — an entrance would compete with
the shared-layout animation a dragged card rides). Both reasons are recorded in
`pages/surfaceEntranceAdoption.test.ts` so a later pass does not re-derive them. Also NOT wrapped:
`PinnedTiles` — it renders `null` on an empty registry, and a region wrapper around nothing is still
a flex item, so it would spend a `gap-2xl` of blank space on every install with no pinned tiles
(and the tile band is AMBIENT-SURFACES' surface).

**DISCOVERY — the first browser measurement was a harness artifact that read as a defect.** Sampling
`element.style.opacity` showed every region jumping 0.00 → 1.00 with no intermediate values, i.e. a
pop rather than a fade. framer-motion animates opacity through **WAAPI**, so the inline style holds
its `initial` string for the whole animation and only the COMPUTED value moves; re-measured,
`getComputedStyle` glides 0.008 → 0.16 → … → 0.93 with `translateY` 7.18px → 0.57px and
`getAnimations().length === 1`. An earlier run was throttled too — the tab was backgrounded, so rAF
ran at 30-130ms and the trace was unusable; `document.hasFocus()` is the cheap tell. Two
measurements, two different reasons to believe a working entrance was broken.

**Measured, in Chrome, on the real dashboard:** region cascade starts at 40 / 62 / 104 / 145 / 196 /
238 / 281 / 329 ms — deltas 22, 42, 41, 51, 42, 43, 48 ms against the predicted `expr(0.05, 0.4)` =
**44ms** at the default expressiveness 0.8. Inbox 40ms apart, Discover 41/45/46/38/51. The whole
dashboard cascade spans 289ms and the last region settles at 748ms. **Content is not gated:** at the
first commit (t=27ms, minimum region opacity 0) all eight section headings, 29 buttons and the
composer were already in the document. At 390×844 every surface measured **0px** overflow on the
document, `<main>`, and the group, with no region wider than its group — the `min-w-0` the four
single-widget regions carry is what preserves that (the region is now the column's flex item, and a
flex item defaults to `min-width: auto`).

**Rails.** `design/motion.test.ts` grows 6 for `regionStagger()` (the null, the expr() linearity via
a midpoint check, the 0.044 landing, the call-time read, the zero-arity); `ui/motion/Entrance.test.tsx`
(6) — the group declares its branch, the variant demonstrably reaches the regions (they carry an
inline `opacity: 0` at mount only because the group's label propagated, which is the same propagation
`staggerChildren` rides), content and a control usable on the first commit, node identity stable
across a re-render, and an orphan region renders plain; `ui/motion/Entrance.reducedMotion.test.tsx`
(5) in its own file with the `matchMedia` stub at MODULE SCOPE, because framer caches its probe in a
module singleton and a later stub is inert; `pages/discover/entranceReplay.test.tsx` (3) drives the
real surface through a dismiss; `pages/surfaceEntranceAdoption.test.ts` (8) names the three adopters
with a region floor each, forbids a second group or a hand-rolled `stagger(` on any of them, and
pins `regionStagger`'s consumer set.

**Falsification.** Deleting the reduced-motion branch reddened 4 assertions across two files
(`expected { …(2) } to be null`, and `data-entrance` `"staggered"` where `"none"` was required).
Gating a region's children on `onAnimationComplete` reddened both "never gates content" tests
(`Unable to find an element with the text: first region`) — and the failure dump is also the proof
the variant is live: `style="opacity: 0; transform: translateY(8px);"`. Keying Discover's group on
`data.visible_count` reddened the replay rail with the flicker rendered literally in the diff — the
regions' computed opacity back at `0` and `translateY(8px)` after the refetch.

**Not in this atom, by scope:** `FM-7` owns the 60fps proof and the CI zero-motion guard, and now
inherits an `expr()`-scaled entrance and a reduced-motion assertion on three surfaces to measure.
The `ListRow`/`TipRow`/launcher-chip `delay: Math.min(index * 0.03, …)` idiom is a LIST-ITEM stagger
and was left alone: it is pre-existing, it is not a region cascade, and converting it is an
app-wide sweep, not this atom.

### 2026-08-18 — `FM-3` (S2: T2.2; contract §C2 `LiquidShape`) — **DONE**

`web/src/ui/motion/LiquidShape.tsx` ships the fluid-blob morph, exported from the `ui/motion` barrel.
Three tiers, mirroring `Disintegrate`: BOLD (`exprHeavy`) morphs on `physics.fluid` and runs a slow
idle breathe; REFINED drops the breathe **entirely** — no driver, not a slower one, per `exprHeavy`'s
contract; REDUCED-MOTION renders a plain `<path>` with no motion value and no spring at all. Coral
comes from `var(--color-primary)`, and every amplitude rides `expr()` — `intensity` is documented as
a plain 0..1 base that the primitive scales itself, so a call site cannot forget the knob (and must
not pass `expr(1)`, which would scale twice).

**THE OPEN QUESTION IS CLOSED BY MEASUREMENT: SVG path, not canvas metaball.** §Risks asked for this
to be decided in T2.2 "by measuring … no premature choice". Three implementations of the same
silhouette (one blob, 160x160 CSS px, 16 control points) were driven in real headed Chromium for 240
measured frames each after 30 warm-up frames — an SVG path whose `d` is recomputed per frame, an
honest per-pixel canvas-2D metaball (density field thresholded into `ImageData`), and the cheap
canvas blur+contrast merge. Per-frame JS work, median:

| shapes | SVG path | canvas metaball | canvas blur+contrast |
|---|---|---|---|
| 1 | 0.1 ms | 0.2 ms | 0.1 ms |
| 4 | 0.1 ms | 1.1 ms | 0.1 ms |
| 16 | 0.1 ms | 1.9 ms | 0.1 ms |
| 64 | 0.6 ms | 7.6 ms | 0.3 ms |
| 1 · 20x CPU throttle | 0.0 ms | 1.0 ms | 0.0 ms |
| 4 · 20x CPU throttle | 0.0 ms | **10.9 ms** | 0.1 ms |
| 16 · 20x CPU throttle | 2.3 ms | **39.9 ms** (239 of 240 frames over 20 ms) | 0.1 ms |

**The plan's premise was falsified.** "Canvas scales better for many shapes" is true for point/particle
fields — which is what `DotGlow` actually is — but a metaball is a DENSITY FIELD, so its cost is
per-pixel over its own area and it is the variant that fails first. It is 12.7x the SVG path at 64
shapes unthrottled, and on a 20x-throttled CPU it already misses 60fps at **four** shapes and
collapses completely at sixteen. It never wins at any count, so there is no crossover to trade off.
At the count this primitive is actually used at (one, a few) both fit the budget on a fast machine —
which means the tie-break was never going to be performance, and the weak-machine floor is what
separates them.

**Two findings about the measurement itself, recorded because they nearly produced a fake result.**
(1) The first pass was NON-DISCRIMINATING: on a 120Hz display every variant sat at an 8.3 ms rAF
interval, vsync-limited, so the frame metric could not tell the implementations apart at all. The
numbers above come from adding CPU throttling and a long-frame count. (2) A JS-work probe is BLIND to
raster: the blur+contrast variant is the cheapest in JS at every count and was nonetheless **the only
variant to miss frames unthrottled** (rAF p95 16.7 ms, worst 17.4 ms at 64 shapes) because its price
is paid in the compositor. Anyone re-deciding this must measure frame intervals, not just JS.

**On deleted scope, said out loud:** the chosen implementation is pure Motion + SVG geometry, so it
touches neither WebGL nor the gooey filter that `Disintegrate.tsx:19` records as deleted. The
blur+contrast variant IS that deleted family, and it was measured rather than assumed — it loses on
frame intervals as well as on lineage, so nothing was re-added quietly.

**A DEFECT FOUND IN THE BROWSER, not in tests.** The first browser pass filled the blob at a single
opacity and it read as a poster-weight coral slab beside `WavyProgress`'s hairline coral stroke and
`DotGlow`'s soft luminous field — a weight clash, which is exactly the done-when clause about
integrating without clashing. Fixed by grading the fill core→edge through a `radialGradient` whose two
stops are the SAME theme var at two opacities (no second color, no hex). The gradient id comes from
`useId()`, sanitized for a `url(#…)` fragment, and a rail asserts each instance points at its OWN id —
a mismatch there renders an invisible blob that would be green on every other assertion.

**Falsification found a false-green in my own rail.** The `expr()` test originally asserted only that
the bold and refined blobs were different strings. Replacing the `expr()` call with a raw constant
left it GREEN, because the two renders still differed via the `exprHeavy` breathe (on at 1, off at 0) —
it was measuring the tier gate while claiming to measure the amplitude. It now measures the amplitude
directly, as the distance between `blob` and `circle` at the same setting (the breathe is
shape-independent, so it cancels), and asserts that distance is strictly monotonic in the knob and
lands at expr()'s 0.35 floor when refined. The mutation now reds with
`expected 8.31 to be greater than 8.31`. **Note the token lint does NOT guard this**: it policies raw
hex and raw px inside inline `style={{}}`, and this component has no inline-style px at all — its
geometry is viewBox units, the same category `WavyProgress` is exempted for. The rail is the only
guard on `expr()` here.

**Rails** (15 tests): `LiquidShape.test.tsx` (12) — barrel reachability by identity, a vacuity floor on
the geometry, per-shape distinctness, the expr()/intensity amplitude, the tier attribute, the
decoration contract, gradient-id wiring; `LiquidShape.reducedMotion.test.tsx` (3) — `matchMedia`
stubbed at module scope before any render (framer-motion caches the probe in a module singleton), the
instant branch with a positive control, a synchronous state change with no `waitFor`, and a no-drift
assertion that catches a breathe driver surviving reduced motion.

**Validated in a real browser**, both themes, via a temporary harness (deleted, not committed) that
rendered five `LiquidShape`s beside a live `DotGlow` and two `WavyProgress` bars under the app's real
providers and tokens. Coral resolved per theme — `rgb(255,107,91)` dark, `rgb(200,69,46)` light. Bold
tier: silhouette drifts (breathe live). Refined tier (expressiveness 0.4): no drift in either theme —
the heavy effect is genuinely dropped. Reduced motion: all five on `instant/reduced`, no drift, and
the geometry changed within one frame of flipping `active`. Every pass carried a positive control
(five mounted shapes, real path length, a resolved non-`var()` fill, a painted DotGlow canvas). One
`pageerror` seen initially was **my driver's own** — `addInitScript` touching
`document.documentElement` before the document existed — and disappeared once guarded; zero component
errors.

**ADOPTION IS DEFERRED TO `FM-4`, deliberately.** This atom ships the primitive with no product call
site, which is why the barrel export is the only thing keeping it reachable and why a rail asserts it
by identity. No decorative motion was bolted onto a product surface on executor taste.

**Not in this atom, by scope:** `FM-7` owns the 60fps budget proof and the CI zero-motion guard — the
numbers above are an implementation-choice measurement, NOT that proof. `FM-4` owns unifying
Morph/LiquidShape/Disintegrate/Bud into one vocabulary and documenting it in `motion.md`. **The feel
is not claimed settled** — every constant lives in one named `TUNING` block at the top of the file for
the owner's taste pass (owner task 1), and the shape vocabulary is deliberately small (circle /
squircle / blob) so that pass has few knobs to fight.

### 2026-08-18 — `FM-2` (S2: T2.1; contract §C2 `Morph`) — **DONE**

`web/src/ui/motion/Morph.tsx` ships the shared-element wrapper, exported from the `ui/motion` barrel.
Two branches and nothing else: motion-allowed renders `motion.div layoutId={id}` on
`morphTransition()`; reduced-motion renders a **plain `<div>` with no `layoutId` at all**, so the swap
is instant rather than quick and the projection machinery never runs. `layoutId` only — **deliberately
no `layout`** — because `layout` would additionally animate each end's own size changes, which on a
grid means every filter, scroll and hover re-measures every card.

**DEVIATION — surface.** T2.1 names knowledge card → reading view; the atom's dep allows alternates.
Wired instead to the **artifacts library**: `ArtifactGrid`'s card ⇄ `ArtifactsSection`'s full-page
`ArtifactViewer`, on `artifact-<slug>`. Reason, measured before choosing: a morph needs its two ends to
swap in ONE commit in BOTH directions, and almost every list⇄detail in this app is a `SidePanel` peek
where the row and the panel are co-mounted (knowledge, inbox, tasks, skills) — a shared id with both
ends alive is a different and wrong animation. `#/knowledge` → `#/knowledge/item/<id>` does swap, but
`KnowledgeListPage` is keyed on `navEpoch`, so Back remounts it and its rows arrive a fetch later —
forward would morph and **Back would not**. `ArtifactsSection` renders `slug ? viewer : grid` and holds
`artifacts` above the swap, so both directions are same-commit with data already in hand. Route
transitions do not interfere: `FM-5`'s View Transition is gated on `route`, and this is a `sub` change.

**Evidence (driven in Chrome against a seeded dev home, 6 artifacts, 1440x900).** Forward: the viewer
appears at **296x201** — the card's own 294x200 — and grows to 1244x844 over ~330ms with a spring
overshoot to scale 1.0085, settling to `transform: none`. Back: the card starts at the viewer's exact
box (196,56,1244,844) and lands on **(212,229,294,200)**, its resting box, to the pixel. Reduced
motion (`matchMedia` stubbed before first paint): all six cards report `data-morph="none"`, and across
every sampled frame for 500ms in both directions the set of transforms is exactly `["none"]` and the
set of `style` attributes exactly `[null]` — instant, not fast. The motion-allowed run of the same
probe produced 68 distinct transform matrices, which is what makes that a measurement and not a
tautology.

**Layout thrash — measured, not asserted.** Over a trace covering one open + one Back: **CLS 0.00**,
**zero** forced-reflow markers, 37 `Layout` events totalling **15.28ms** (max 8.14ms) and 224
`UpdateLayoutTree` totalling 35.29ms across 14.25s — and inside the forward morph's window the 16
`Layout` events sit in three bursts (commit, the viewer's content arriving, settle) against ~219
committed frames, so the animation itself re-measures nothing. A/B on the same build with the morph
off (reduced-motion branch) attributes **one** frame to the morph: forward max frame 34.7ms vs 10.7ms
(1 frame over 16.7ms vs 0), that frame being the commit where the viewer mounts. Returning to the grid
costs more than the morph does and costs it either way — morph-off's worst frame is *worse* (96.7ms vs
60.1ms) because six lazy sandboxed iframe previews remount. Zero long tasks on either path.

**KNOWN, MEASURED, NOT FIXED HERE — the forward flight starts 157px too high.** The opening morph
matches the card's size and horizontal centre (359 vs 360) but its vertical centre is off by exactly
**157px**, which is exactly the height of the toolbar row above the grid. Cause: React removes the
toolbar in the same commit that mounts the viewer, so by the time Framer snapshots the *exiting* card
it has already reflowed upward by the toolbar's height. The Back direction is exact because the
exiting viewer has no sibling to lose. The clause holds — it visibly morphs both ways — but it reads
as growing from above the card you clicked. The fix is structural (keep the exiting subtree in flow,
e.g. an `AnimatePresence mode="popLayout"` around the page swap), which is `FM-4`'s coherence scope,
not a constant to dial.

**Not in this atom, by scope:** `FM-4` owns unifying Morph/LiquidShape/Disintegrate/Bud into one
vocabulary (and the 157px snapshot fix above); `FM-7` owns the 60fps proof and the CI zero-motion
guard, and now inherits a numeric baseline to measure against. **The feel is not claimed settled** —
`MORPH = { stiffness, stiffnessBonus, floor }` is one named block at the top of the file for the
owner's taste pass (owner task 1).

### 2026-08-18 — `FM-4` (S2: coherence pass; contract §C2) — **PARTIAL**, atom stays `todo`

The unification and both reduced-motion/`expressiveness=0` proofs are done and driven. The atom is **not**
flipped because its `done_when` names "**a liquid state transition** … stays clean", and `LiquidShape` still has
**no product call site** — the clause is verified for the primitive (jsdom rails + the source rail +
`familySpring` collapsing to `instant`) but is not observable in the product, so it cannot be driven.

⚠️ **ADOPTION IS NOW UNOWNED.** `FM-3` deferred it to `FM-4` (line 488 above); `FM-4` declines it because
adopting means choosing a product surface to decorate, which is a taste decision outside a coherence pass and
outside the fence. It needs an owner call or its own atom — otherwise `LiquidShape` stays a primitive nobody
reaches, which is the inert-surface shape this repo keeps finding.

`web/src/ui/motion/vocabulary.ts` is the family's ONE timing vocabulary, and the four primitives now
contain **no timing arithmetic at all**. `MORPH_FAMILY` holds five numbers (three bases + one
stiffness bonus + one floor, plus the tween's `refinedScale`); `familySpring(base)`, `familyTween(heavy)`
and `familyFade()` are the only transitions any member gets. `Morph.MORPH` and `morphTransition()` are
DELETED into it (clean break — no alias kept), and the barrel exports the vocabulary instead.

**What the coherence pass actually found — the family disagreed about what the user's knob means.**
`Morph` was `190 + expr(70, 0.4)` and `Bud` was `260 - expr(70, 0.4)`: identical magnitude, identical
floor, **opposite sign**. Under `bouncy()`'s fixed absolute damping (26 at the default bounciness 1)
that is not a stylistic difference — dialling expressiveness UP made `Morph` tauter (ζ 0.88→0.82,
quicker + more overshoot) and made `Bud` *slacker* (232→198 stiffness, ζ 0.853→0.923, slower + LESS
overshoot). `Bud`'s own comment claimed "a touch more overshoot when expressiveness is bold", which
was **false in the shipped code**. One sign convention now: bold = tauter, everywhere. Three further
divergences closed: `Disintegrate` rode a raw `[0.4, 0, 0.2, 1]` (the literal Material standard curve
`motion.ts` explicitly disowns: "PersonalClaw curves — smooth, NOT the literal Material M3 values")
plus three unnamed durations (0.34 / ×0.7 / 0.18 / 0.2) → `ease.emphasized` + `duration.medium` +
`spring.effects`; `LiquidShape` rode the bare `physics.fluid`, the one member the knob left temporally
untouched → `familySpring(MORPH_FAMILY.state)`; and `Bud` was the one member that did NOT self-gate
reduced motion, delegating to the root `MotionConfig reducedMotion="user"` — which neutralizes framer
TRANSFORMS but keeps animating `borderRadius`, still installs the projection node `layout` asks for,
and left no attribute to assert. `Bud` now self-gates and exposes `data-bud`, matching `data-morph` /
`data-liquid-shape`.

**Bases are ordered by TRAVEL, and the ordering is a rail:** `flight` 190 (a card crossing the page) <
`state` 200 (a silhouette changing composure in place) < `spawn` 240 (a panel off a button). Feel
deltas, stated because they are taste and not settled: `Bud` moves 232/198 (refined/bold) → 268/292,
i.e. quicker at both ends and now monotone with the knob; `LiquidShape` 180 flat → 228/262;
`Disintegrate`'s dissolve 0.34→0.30s bold, 0.238→0.21s refined, and its reversal 0.18s→`spring.effects`.

**"Reads as one system" is asserted where it is decidable, not screenshotted.**
`ui/motion/vocabulary.test.ts` (16 cases) proves both halves: a SOURCE rail — each member imports
`./vocabulary`, may import only `expr`/`exprHeavy` (amplitude) from `design/motion` and never
`physics`/`spring`/`ease`/`duration` (timing), writes no `stiffness:`, no raw `duration: <number>` and
no raw `ease: [...]`, passes no custom `exprHeavy()` threshold, and self-gates `useReducedMotion()`;
and a RESOLVED rail — all three springs carry `physics.fluid`'s damping+mass, the bonus is the same
magnitude and same SIGN for every base, and the bases hold their travel order. It carries a vacuity
floor (four files found, each >1000 chars, each with an `export function`) because every source
assertion is a "does NOT contain".

**The two off-switches, measured SEPARATELY (they are not the same switch).** Driven in Chrome at
1920x900 against THIS worktree's bundle (verified by chunk hash `index-Bq_aC5i5.js`), seeded dev home
at `/private/tmp/fm4-wt/.dev-home`, 6 artifacts.
· **`prefers-reduced-motion`** (`matchMedia` stubbed via `initScript` before first paint; the stub is
inert on a hash-only navigation — it needs a real document load): all 6 cards report
`data-morph="none"`, and over **73 frames / 600ms** after clicking a card the set of computed
transforms is exactly `["none"]` and the set of inline `style` attributes exactly `[null]` — instant,
not fast. The `Bud` in the task form's "Add prerequisite" picker reports `data-bud="instant"`, a plain
`DIV`, `style` attribute `null` across **61 frames / 500ms**, computed transform exactly `["none"]`.
Positive controls throughout: the reading view rendered its content, the bud's box is 1692x131, its
search input rendered and `autoFocus` landed inside it.
· **`expressiveness = 0`** (set via `localStorage.appearance.scalars['--expressiveness']`, reduced
motion OFF): the morph is **still animating** — `data-morph="shared"`, 55 distinct transform matrices
over 182 frames, overshoot to scale 1.0022, settled at 758ms. At expressiveness 1 the same probe gives
41 matrices, overshoot 1.0105, settled at **610ms**. So bold is 148ms quicker and overshoots ~5x more,
and 0 is the FLOOR, not the off switch. `Bud` at the default 0.8: `data-bud="grown"`, scaleY 0.12 →
overshoot 1.0254 (33 frames above 1) → settles 0.9994, `border-radius` pill→`--radius-md` — the
overshoot its docblock always claimed and never had at bold.

**Falsification — every load-bearing property broken on the live line, red observed, restored from a
file copy (`git diff` over `ui/motion/` empty afterwards).** (1) `Bud`'s `if (reduce)` → `if (false)`:
*"Expected the element to have attribute data-bud=\"instant\" / Received data-bud=\"grown\""*.
(2) `floor: 0.4` → `0`: *"expected 190 to be greater than 190"* (and note the sibling `bold > refined`
case still passes at floor 0 — which is why the floor needs its own assertion). (3a) `stiffness: 260`
re-added to `Bud`: *"Bud.tsx sets its own stiffness: expected … not to match /stiffness:/"*. (3b) the
raw Material curve restored in `Disintegrate`: *"Disintegrate.tsx hardcodes a duration: expected … not
to match /duration:\s*[\d.]/"*. (4) `base + expr(...)` → `base - expr(...)`: three reds —
*"expected -61.6 to be greater than 0"*, *"flight inverts the knob: expected 120 to be greater than
162"*, *"expected 162 to be 218"*.

**`FM-2`'s 157px defect — DIAGNOSED and ATTRIBUTED to the pixel, deliberately NOT fixed. Handed to a
plan that owns the page swap.** Reproduced: at 1440x900 the forward flight's start box is
`Δcy = -157` from the card's box with `Δcx = 0` and the size exact. **Positive control on the
diagnosis:** at 1920x900 the toolbar wraps to 113px and the offset becomes exactly `-113` — the offset
IS the toolbar's height, not a constant, and the snapshot's `y` is always the content area's top (72),
i.e. the card measured as if the toolbar were absent. The suggested fix was tried: an
`AnimatePresence mode="popLayout"` around `ArtifactsSection`'s `slug ? viewer : grid` swap, both
branches `motion.div` with `exit={{opacity:0}}` on `familyFade()`. **It fixes the geometry exactly —
measured `Δcy = 0, Δcx = 0`, the viewer starting at the card's box `1064,185,414,200` and growing from
there — and it breaks the swap.** `AnimatePresence` keeps the exiting grid mounted, so the SAME
`layoutId` is live at both ends at once, which is precisely the "both ends alive = a different and
wrong animation" hazard `FM-2` recorded: the exiting grid never unmounted (7 `[data-morph]` nodes still
in the DOM after settle, header actions rendered three times, and a frame where every box measured
0x0). Trading a 157px start offset for a page that keeps its old content is strictly worse, so it was
reverted from a file copy. **The fix is not a coherence-pass change**: every variant that keeps the
toolbar's height in flow long enough for the snapshot either keeps the other `layoutId` end alive or
animates the toolbar's collapse against the morph. It needs either a framer-level pre-mutation
snapshot or a restructure where the toolbar is not a flow sibling of the grid — i.e. an owner scope
decision about the page swap, not a constant to dial.

**PARTIAL — the one clause not fully met.** The done_when's "a liquid state transition stays clean"
was validated for the PRIMITIVE (jsdom rails + the source rail + `familySpring` collapsing to
`instant`), **not driven in a browser**, because `LiquidShape` still has **no product call site** —
`FM-3` deferred adoption to this atom, and adopting it means editing a product surface outside this
atom's fence (`ui/motion/**`, `design/motion.ts`, `docs/design/motion.md`, the one morph-hosting page).
No decorative motion was bolted onto a surface on executor taste, per `FM-3`'s own standing reason.
**Adoption is still open and now belongs to whoever takes the next Fluid-Motion atom.**

**Gate:** `npm ci` clean · `npm run typecheck --workspace web` clean · full `npm test --workspace web`
· `npm run build --workspace web` clean · `make lint` clean. Docs: `docs/design/motion.md` §5b ("The
morph family — one vocabulary") carries the three rules, the "adding a fifth member" contract, and the
two-off-switches section.

### 2026-08-20 — `FM-4` (S2: coherence pass; contract §C2) — **DONE**, closing the PARTIAL above

The 2026-08-18 entry left one clause open: `done_when` names "**a liquid state transition** … stays
clean", and `LiquidShape` had **no product call site**, so the clause was verified for the primitive
but could not be driven. That entry also recorded ⚠️ *"ADOPTION IS NOW UNOWNED"* and asked for an
owner call. This closes both.

**The owner call, and why it is not executor taste.** The plan's own consumer mapping (line 60) names
*"ambient surfaces (20 — liquid state transitions)"*, and its example (line 51) is
`active={loaded}` — loading→loaded. The pinned ambient dashboard tile (`dashboard/PinnedTiles.tsx`)
is that surface in DOM. The alternatives were ruled out **structurally, not by preference**:
`dashboard/world/AgentWorld.tsx`, the other live ambient surface, is a **canvas painter** and cannot
host an SVG React child. `pages/loops/LoopCockpitPage.tsx` **does exist** (1,284 lines, carries
phase/running state) and is a real second candidate — deliberately deferred, recorded in the rail so
a later pass is not told it is absent. An earlier draft of this session's brief claimed no loop
cockpit existed; that was wrong (`pages/loop/` is a different, smaller directory).

`from="blob"` → `to="squircle"` is the primitive's own vocabulary for unsettled→settled, `active`
reads as *settled* so `from`/`to` keep their stated direction, `intensity` is a plain `0.5` (the
primitive applies `expr()` itself), tint is the theme default.

**🔴 The clause could not be met by the obvious signal, and only a browser showed it.** Keyed on
body-presence alone the silhouette **never transitions**: on localhost the artifact resolves before
the silhouette first mounts — measured, **0 of 276 sampled frames** caught the tile in its loading
state — so the blob was a shape that had always already settled. Every jsdom test passed. Settled
therefore means *body present AND nothing re-reading it*, which a user can watch by pressing Refresh.

**That exposed a real defect in the shared SWR hook, which is why this diff reaches beyond
`ui/motion/**`.** `useCachedData` sets `loading` **only when nothing is cached**
(`if (seeded === undefined) setLoading(true)`) — correct for gating `if (!data) return <Skeleton/>`,
but it means the hook could not express "showing stale data, refetch in flight" to any of its 68
consumers. An additive `revalidating` flag now does; `loading` is untouched.

**And driving it found an outage-class bug the whole test suite was blind to.** `refresh` was a fresh
closure every render, so a consumer that lists it as a dependency — `useEffect(() => { if (reloadKey)
refresh() }, [reloadKey, refresh])`, shipped in `PinnedTiles` — re-ran that effect on every render,
called refresh, refetched, re-rendered, and looped. Measured in Chrome: **289,116 requests and
`net::ERR_INSUFFICIENT_RESOURCES`**, after which every artifact fetch failed and the tile sat in its
loading state permanently. The effect is correctly dependency-listed, which is what makes an unstable
identity a trap rather than a smell. `refresh` is now a stable `useCallback`. The regression test is
in `useCachedData.test.ts`; with the fix reverted it does not merely fail, it **kills the vitest
worker** (6 of 9 tests never complete) — the loop is unbounded.

**The two off-switches, measured SEPARATELY in Chromium at 1440x900** against a gateway serving this
worktree's own bundle (verified by chunk hash `index-7VIbRrDV.js`), isolated dev home, a real seeded
artifact pinned to the Overview view. `sameNodeThroughout: true` in all three regimes, so no
measurement is a remount artifact, and `consoleErrors: 0` (the storm above is gone). Positive control
throughout: the tile rendered its real name, "Weekly Sales", and its body painted.

| regime | `data-liquid-shape` | idle breathe (x0 range) | after Refresh | distinct silhouettes |
|---|---|---|---|---|
| full motion, expressiveness 0.8 | `morph` | 0.24 | **1.26** | 264 |
| `prefers-reduced-motion` | `instant` | **0** | 1.63 (one jump) | **2** |
| `expressiveness = 0` | `morph` | **0** | **0.04** | 42 |

Reduced motion is **instant, not fast**: exactly two silhouettes, no perpetual driver, computed
transform `none` throughout. Expressiveness 0 is the **floor, not an off switch** — it still morphs,
at ~3% of the default amplitude, with the idle breathe dropped entirely (the refined tier drops a
heavy effect rather than shrinking it). The two switches behave differently, confirming the previous
entry's point that they are not the same switch.

**A source rail keeps the adoption from silently vanishing** (`dashboard/liquidAdoptionRail.test.ts`,
following `FM-6`'s `surfaceEntranceAdoption.test.ts` precedent): barrel import, renders, no
hand-rolled family timing, the morph precedes the body-switching conditional, plain-number
`intensity`, no hex tint, the text carriers intact, and a whole-`src` census asserting LiquidShape
has at least one non-test call site outside `ui/motion/`. It carries a vacuity floor — pointed at a
nonexistent path it goes red rather than passing on an empty scan. `motion.md` gains the adoption,
the shape semantics and the **mounted-host rule** (a state morph needs a host that survives the state
change, or it silently never animates) — that rule is the reusable lesson, so it belongs in the doc.

**Also corrected:** the plan's example at line 51 is wrong twice — `intensity={expr(1)}`
double-applies the knob (an extra factor of `0.35 + 0.65·e`: 13% low at the default, **65% low at
0**), and `from="circle"` contradicts the primitive's docstring, which names `blob`→`squircle` as the
load pairing. `data-liquid-shape` carries the BRANCH (`morph`/`instant`), not the silhouette name, so
the state→shape mapping is pinned by exact path geometry instead.

**Gate:** `make lint` clean (mypy 939 files); web **439 files / 4,530 tests**; `npm run typecheck`
and `npm run build` clean; Python **23,122 passed, 30 skipped, 12 xfailed**; roadmap sync rails green.
Falsifications, each mutating the live line, confirming it applied, observing the named red and
restoring from a file copy: `active` inverted (the loading tile drew the settled shape), the morph
moved inside the body conditional (rail red on both the position and its own floor), `revalidating`
made conditional like `loading` (the in-flight test red), and `refresh` reverted to an unstable
closure (worker killed). `docs/design/consistency-audit.json` drift was left alone deliberately —
**clean `origin/main` reproduces it byte for byte** (`filesScanned` 527→529, `raw-input` 5→6,
`outlineNoneCount` 141→142 in a settings panel this atom never touched), so it is inherited, and
folding it in here would mask a real drift later. Someone's next design-system change should regen it.

**`FM-7` is now unblocked** (its deps were `FM-4`, `FM-5`, `FM-6`) and enters the ready frontier.
