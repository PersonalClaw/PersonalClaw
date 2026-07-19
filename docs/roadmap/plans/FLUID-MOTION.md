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

## Contracts & Interfaces (extends `motion.ts` + `ui/motion/`; conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

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

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

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
