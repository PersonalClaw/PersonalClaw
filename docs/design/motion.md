# Motion — the author's guide

Everything animated in the dashboard comes from one module,
[`web/src/design/motion.ts`](../../web/src/design/motion.ts). This page is what you need to
pick a transition and know what it is allowed to do. If you finish it and still don't know
which preset to use, that is a bug in this page — fix the page.

The soul of it, from `PRODUCT.md`: **playful within discipline.** Motion is real, but it is
budgeted, and the task always wins. Nothing here may delay a user action.

---

## 1. Pick a preset

Four named presets. They are named by **feel**, not by component, so the question you answer
is "how should this move?" and not "what am I in?".

| Preset | Reach for it when | Feel | Constants (stiffness / damping at bounciness 1 → 0) |
|---|---|---|---|
| `physics.snappy` | controls, chevrons, press + hover feedback, small state flips | quick, barely overshoots | 520 / 30 → 40 |
| `physics.smooth` | **the default.** Anything where nothing argues for another tier | critically damped, no wobble | 320 / 34 → 38 |
| `physics.fluid` | large surfaces, layout shifts, cards promoting, morphs | liquid, generous settle | 180 / 26 → 34 |
| `physics.playful` | the ~3–4 *earned* personality moments — a menu opening, a success bloom | the most overshoot | 420 / 14 → 34 |

```tsx
import { physics } from '../design/motion'

<motion.div animate={{ scale: 1 }} transition={physics.smooth} />
```

**When in doubt: `smooth`.** `playful` is a budget, not a default — if a surface already has a
playful moment, the next one on that surface is `snappy` or `smooth`.

### The other family, and there are only two

`spring` holds the raw tiers: `spatialDefault` / `spatialFast` / `spatialSlow`, and
`spring.effects` — the critically damped transition for **opacity, colour, and content**. A
fade never springs, so a fade never takes a `physics` preset.

```
spatial / character  →  physics.*
opacity, colour      →  spring.effects
```

That is the whole vocabulary. There is deliberately no third set of names: a second way to
say "playful" means two authors make two different choices for one decision, and the app
stops feeling like one app. If a preset is wrong for your surface, **retune the four
constants in `motion.ts`** — that is what they are for — rather than adding a fifth name or a
one-off literal at the call site.

---

## 2. The budget — three dials, and what each one actually does

| Dial | Where the user finds it | What it scales | What it does NOT touch |
|---|---|---|---|
| `--bounciness` (0…1, default 1) | Settings → Design → Motion | the overshoot of every `physics` preset and every gesture spring, by interpolating damping | stiffness (a preset's identity), durations, `spring.*` |
| `--expressiveness` (0…1, default 0.8) | Settings → Design → Motion | every expressive *magnitude* — hover lift, press depth, morph delta — through `expr()`; heavy effects switch off entirely below `exprHeavy()`'s threshold | which preset you picked |
| `prefers-reduced-motion` | the user's OS | **overrides both.** Every preset collapses to `instant` | the user's ability to *do* anything (see §3) |

`expr(max, floor)` scales a number, not a transition: `expr(3, 0.3)` is "3px of hover lift,
keeping 30 % of it when the user has dialled expressiveness to 0". The floor is why a refined
setting still feels alive rather than dead. Use `exprHeavy()` as a boolean gate for effects
that should *drop out* rather than shrink.

Both sliders are read at animation time. Neither one is an accessibility control — that is
the third row, and it is not negotiable.

---

## 3. The reduced-motion contract

Under `prefers-reduced-motion: reduce`:

1. **Every `physics` preset returns `instant`** (`{ type: 'tween', duration: 0 }`). The gate
   lives in one place — `bouncy()` in `motion.ts` — so a preset cannot be added that escapes
   it. `dragSpring()` and `swipeDismiss()`'s transitions collapse the same way.
2. **The app root already wraps everything** in Framer's `<MotionConfig reducedMotion="user">`,
   and `tokens.css` carries a global `*` rule that flattens CSS animations and transitions.
   You do not need to re-implement either. Do not disable a component's animation ad hoc.
3. `useReducedMotion()` from framer-motion is the React-side read when you need to choose a
   *different shape* (an instant swap instead of a morph), not merely a shorter one.

**What must NOT collapse: direct manipulation.** Reduced motion removes decoration, never
capability. A drag still follows the finger 1:1 — `dragElastic()` is deliberately *not* gated,
because with zero elasticity against a zero-width constraint box a swipe-to-dismiss card
cannot move at all and the gesture stops being performable. Only the transition the gesture
*resolves* with is decoration, and that is what goes instant.

Corollary for spreads: `instant` declares `type: 'tween'` on purpose. Call sites like
`{ ...physics.fluid, stiffness: 240 }` are legal, and without an explicit type the leftover
`stiffness` would let Framer infer a spring again — the collapse would leak back through the
spread. Keep the explicit type if you ever touch it.

---

## 4. Gestures

```tsx
import { dragElastic, swipeDismiss } from '../design/motion'

<motion.div
  drag="x"
  dragConstraints={{ left: 0, right: 0 }}
  dragElastic={{ left: 0, right: dragElastic() }}
  onDragEnd={(_, info) => {
    const swipe = swipeDismiss(Math.max(0, info.velocity.x), Math.max(0, info.offset.x))
    if (swipe.dismiss) remove(id)   // `swipe.transition` is the curve to leave on
  }}
/>
```

- **`dragSpring()`** — the transition a dragged element settles with: snapping back inside its
  constraints, or landing after a reorder. Stiffer than `snappy`, because a return that loses
  the race with the hand that threw it reads as lag.
- **`dragElastic()`** — how far the element may stretch past its constraints
  (`--drag-elastic`, 0 rigid … 1 loose).
- **`swipeDismiss(velocity, offset)`** — the verdict *and* the curve. It dismisses on a fast
  flick (`--swipe-dismiss-velocity`, px/s) **or** a slow deliberate haul past
  `--swipe-dismiss-distance` (px). Velocity alone would make a careful drag all the way across
  the screen do nothing, which reads as broken. It answers for either direction, so a call site
  that only accepts one clamps its inputs (as above). A dismissed element leaves on an
  accelerating curve — it must not overshoot back into the surface it is leaving; a kept one
  springs home on `dragSpring()`.

Never hardcode a gesture threshold at a call site. Both thresholds are user-tunable tokens in
the Motion group; a literal `> 80` in a component is a tuning knob nobody can reach.

---

## 5. Two rules that are easy to get wrong

**A variant that carries a preset must be a FUNCTION.** A module-level object literal reads
the preset getter once, at import, and freezes both the bounciness *and* the reduced-motion
answer for the whole session:

```tsx
// WRONG — every surface using this ignores the slider forever
animate: { opacity: 1, transition: physics.smooth }
// RIGHT — Framer resolves the function per animation
animate: () => ({ opacity: 1, transition: physics.smooth })
```

`overlayEnter` and `listItemEnter` are already written this way; copy them.

**Choreograph, don't scatter.** One considered movement beats ten small ones — scattered
animation is the single loudest "this UI was generated" tell. For a list or a grid, put
`stagger()` on the parent and `listItemEnter` on the children so rows cascade instead of
popping in together. Prefer orchestrating a surface's regions over animating every element in
it.

---

## 6. Adding a Motion token

A motion value the user should be able to tune goes in three places, or it is broken in a way
tests will catch:

1. **`tokenRegistry.ts`** — an `s(...)` entry in the `Motion` group (this is what renders the
   slider; the panel is generated, so there is nothing to add in the settings page).
2. **`tokens.css`** — the declared default, so the token has exactly one default.
3. **`runtime.ts`** — a `runtimeKey`, when JS reads the value per frame or per gesture.
   `motion.ts` reads `runtime`, never `getComputedStyle`.

`web/src/design/motion.test.ts` pins the round trip, both dials per preset, and the gesture
thresholds. It is the rail to extend when you add to the system — and the first place to look
when a slider stops doing anything.
