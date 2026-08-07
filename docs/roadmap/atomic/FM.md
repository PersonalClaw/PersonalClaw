# FLUID-MOTION — atomic plans

**Source plan:** [`FLUID-MOTION`](../plans/FLUID-MOTION.md)  
**Code:** `FM`  
**Source status:** proposed

FLUID-MOTION is DESIGNED with no execution log — nothing shipped. It is a self-contained frontend motion plan (motion.ts, ui/motion/, tokens, router) that touches no cross-plan shared seams and rides existing cosmetic gates (bounciness/expressiveness/prefers-reduced-motion), so no lifecycle/migration re-scopes apply. Decomposed into 7 todo atoms across its 3 sessions: the physics foundation (FM-1), two independent morph primitives + their coherence pass (FM-2/3/4), route transitions and orchestrated entrances (FM-5/6), and the capstone budget proof + CI guard (FM-7). One declared cross-plan edge (DESIGN-SYSTEM-CONSISTENCY, already shipped v0.1.2) and one soft coordination edge (KNOWLEDGE-LIBRARY for a demo morph surface, with alternates available).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `FM-1` | ⬜ | Physics preset system: named springs, gesture helpers, Motion tokens + author guide | `EXT:DESIGN-SYSTEM-CONSISTENCY:consistent components+tokens as the motion substrate (shipped v0.1.2)` | physics presets (snappy/smooth/fluid/playful) scale with the bounciness slider and zero out under prefers-reduced-motion; dragSpring/swipeDismiss shipped in motion.ts; new Motion tokens wired in tokenRegistry + tokens.css; docs/design/motion.md guide lets an author pick a preset and know the budget/reduced-motion constraints from the doc alone; 2-3 existing interactions (pressable/list-enter/overlay) adopt the presets with no hardcoded transitions left |
| `FM-2` | ⬜ | Morph.tsx shared-element wrapper + wire one real card→detail morph | `FM-1`, `EXT:KNOWLEDGE-LIBRARY:knowledge card→reading view surface for the demo morph (alternate surfaces — session rows→chat, loop cards→cockpit — usable if unavailable)` | a list card visibly morphs into its detail/expanded view and back via framer layout/layoutId; reduced-motion path yields instant swap; no layout thrash |
| `FM-3` | ⬜ | LiquidShape.tsx fluid-blob shape morph (coral-tinted, expr()-scaled) | `FM-1` | shape morphs smoothly between states (SVG-path vs canvas-metaball decided by measurement in T2.2), integrates visually with DotGlow/WavyProgress without clashing, and honors reduced-motion (instant) and expr() scaling |
| `FM-4` | ⬜ | Coherence pass: unify Morph/LiquidShape/Disintegrate/Bud into one motion vocabulary | `FM-2`, `FM-3` | the morph family shares timing/curves and reads as one system on visual review; documented in motion.md; the card→reading morph + a liquid state transition both stay clean under prefers-reduced-motion and expressiveness=0 |
| `FM-5` | ⬜ | Wire viewTransition() into hash-route changes (cosmetic-only) | `FM-1` | navigation crossfades/morphs; URL/state changes remain ungated on the transition and the frontend URL-state test still passes; reduced-motion → crossfade or none |
| `FM-6` | ⬜ | Orchestrated staggered entrances for 2-3 key surfaces | `FM-1` | 2-3 key surfaces stagger their regions in via stagger() (expr()-scaled); entrances feel composed not busy; collapse cleanly under prefers-reduced-motion |
| `FM-7` | ⬜ | Motion budget proof: 60fps pass + reduced-motion/expressiveness=0 zero-motion CI guard | `FM-4`, `FM-5`, `FM-6` | 60fps verified on ChatPage + a cockpit with no jank; expressiveness=0 and prefers-reduced-motion both proven to yield instant/crossfade with zero springs; a reduced-motion assertion added to web CI where feasible; full-app motion pass holds at bounciness=1 (delightful) and bounciness=0 (calm) |

## Atom scopes

### `FM-1` — Physics preset system: named springs, gesture helpers, Motion tokens + author guide

**Status:** todo

Session 1 — Physics system (T1.1–T1.3, V1); Contracts C1

**Done when:** physics presets (snappy/smooth/fluid/playful) scale with the bounciness slider and zero out under prefers-reduced-motion; dragSpring/swipeDismiss shipped in motion.ts; new Motion tokens wired in tokenRegistry + tokens.css; docs/design/motion.md guide lets an author pick a preset and know the budget/reduced-motion constraints from the doc alone; 2-3 existing interactions (pressable/list-enter/overlay) adopt the presets with no hardcoded transitions left

### `FM-2` — Morph.tsx shared-element wrapper + wire one real card→detail morph

**Status:** todo

Session 2 — Liquid morphing (T2.1); Contracts C2 (Morph)

**Done when:** a list card visibly morphs into its detail/expanded view and back via framer layout/layoutId; reduced-motion path yields instant swap; no layout thrash

### `FM-3` — LiquidShape.tsx fluid-blob shape morph (coral-tinted, expr()-scaled)

**Status:** todo

Session 2 — Liquid morphing (T2.2); Contracts C2 (LiquidShape)

**Done when:** shape morphs smoothly between states (SVG-path vs canvas-metaball decided by measurement in T2.2), integrates visually with DotGlow/WavyProgress without clashing, and honors reduced-motion (instant) and expr() scaling

### `FM-4` — Coherence pass: unify Morph/LiquidShape/Disintegrate/Bud into one motion vocabulary

**Status:** todo

Session 2 — Liquid morphing (T2.3, V2)

**Done when:** the morph family shares timing/curves and reads as one system on visual review; documented in motion.md; the card→reading morph + a liquid state transition both stay clean under prefers-reduced-motion and expressiveness=0

### `FM-5` — Wire viewTransition() into hash-route changes (cosmetic-only)

**Status:** todo

Session 3 — Route transitions (T3.1); Contracts C3

**Done when:** navigation crossfades/morphs; URL/state changes remain ungated on the transition and the frontend URL-state test still passes; reduced-motion → crossfade or none

### `FM-6` — Orchestrated staggered entrances for 2-3 key surfaces

**Status:** todo

Session 3 — orchestration (T3.2)

**Done when:** 2-3 key surfaces stagger their regions in via stagger() (expr()-scaled); entrances feel composed not busy; collapse cleanly under prefers-reduced-motion

### `FM-7` — Motion budget proof: 60fps pass + reduced-motion/expressiveness=0 zero-motion CI guard

**Status:** todo

Session 3 — budget proof (T3.3, V3)

**Done when:** 60fps verified on ChatPage + a cockpit with no jank; expressiveness=0 and prefers-reduced-motion both proven to yield instant/crossfade with zero springs; a reduced-motion assertion added to web CI where feasible; full-app motion pass holds at bounciness=1 (delightful) and bounciness=0 (calm)

