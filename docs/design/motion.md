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
4. **Two helpers answer with an absence rather than a value**, because for them "less motion"
   has no smaller version: `viewTransition()` runs its update with no transition at all, and
   `regionStagger()` returns `null` so a surface renders its regions plain (§5a). A shorter
   route crossfade is still a crossfade; a quicker cascade is still a cascade.

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
it — §5a is how.

---

## 5a. Orchestrating a surface's entrance

A page's top-level bands cascade in on arrival instead of all landing in one frame. You do not
write this per page: wrap the column you already have in `EntranceGroup` and mark each band
`EntranceRegion`.

```tsx
import { EntranceGroup, EntranceRegion } from '../ui/motion'

<EntranceGroup className="mx-auto flex flex-col gap-2xl">
  <EntranceRegion><Launcher /></EntranceRegion>
  <EntranceRegion className="min-w-0"><Section label="Tasks">…</Section></EntranceRegion>
</EntranceGroup>
```

The choreography itself is `regionStagger()` in `motion.ts`, and it takes **no arguments** on
purpose — one step for the whole app, so retuning the cascade is one number in one file and no
surface can pick its own feel. The step is `expr()`-scaled: ~44ms per region at the default
expressiveness, 20ms when refined. Under `prefers-reduced-motion` it returns `null`, and both
components then render plain `<div>`s — no variants, no hidden initial state, no transition.
There is no "fast cascade" tier, because the setting asks for *less* motion, not quicker.

Three things to know before you adopt it on a surface:

- **The entrance plays on MOUNT, and a re-render never replays it.** For a route surface that
  means once per navigation (`App.tsx` keys the route wrapper on the route). A WebSocket push,
  a `refresh()`, a filter change or an opening panel are re-renders and are free.
- **Where you put the group is the whole game.** Put it *above* every data-dependent branch
  when its regions are static (the dashboard, the inbox), or *on* the loaded column when the
  regions **are** the data (Discover's areas). Never key a group or a region on data.
  `useCachedData` is what makes the first placement safe — a same-key revalidation holds the
  last value instead of dropping to `undefined`, so a refresh cannot flip the surface back
  through its skeleton and remount the group underneath.
- **A surface that re-parents its own blocks is not a candidate.** Settings' masonry re-packs
  on every commit, so a staggered block there would remount and replay its entrance on any
  resize.

A region rendered with no group above it renders plain. That is deliberate: forgetting the
group costs the entrance, never the content — the failure a region cannot be allowed to have
is sitting at `opacity: 0` with no parent to hand it the `animate` label.

Adopt it on surfaces with a real **band stack** (2+ top-level regions). Cascading a single
region is motion for its own sake. `pages/surfaceEntranceAdoption.test.ts` names the surfaces
that have adopted it, and the ones deliberately left out.

---

## 5b. The morph family — one vocabulary

Four primitives in `ui/motion/` are the same gesture at different scales: something the user
already sees **becomes** something else, rather than one thing crossfading out under another.

| Primitive | The gesture | Base |
|---|---|---|
| `Morph` | a library card flies to the page it opens | `MORPH_FAMILY.flight` (190) |
| `LiquidShape` | a silhouette changes composure in place | `MORPH_FAMILY.state` (200) |
| `Bud` | a panel separates from the control that made it | `MORPH_FAMILY.spawn` (240) |
| `Disintegrate` | a row dissolves out of a list | the family tween |

They shipped one atom at a time, and each arrived with its own numbers. `ui/motion/vocabulary.ts`
is now the only place any of them gets a timing value, and the primitives contain **no timing
arithmetic at all**. Three rules make that a system rather than a shared file:

1. **Travel picks the base; the knob picks the bonus.** Bases are ordered by how far the thing
   travels — a cross-page flight is the softest, a bud off a button the tautest, because a
   full-width flight on a taut spring reads as a snap and a bud on a soft one reads as lag on a
   button press. The bonus expressiveness adds on top is **one number for the whole family**
   (`stiffnessBonus`, keeping `floor` of itself at expressiveness 0).
2. **Bold always means tauter** — which, under this app's fixed-damping `bouncy()` springs,
   means both quicker *and* more overshoot. Before FM-4 `Morph` was `190 + expr(70, 0.4)` and
   `Bud` was `260 - expr(70, 0.4)`: identical magnitude, identical floor, opposite sign, so
   dialling the user's knob up made one primitive tauter and the other slacker.
3. **One spring, one tween, one fade.** The spring is `physics.fluid` via `familySpring(base)`,
   so the family inherits `bouncy()`'s bounciness scaling and its reduced-motion collapse for
   free. `Disintegrate` is the only member that must not overshoot at all (a spring settling on
   `filter` undershoots, and `blur()` rejects negatives), so `familyTween()` exists — on
   `ease.emphasized`, the house curve, replacing a hardcoded Material standard curve. Anything
   *fading* uses `familyFade()`, which is just `spring.effects`.

**Adding a fifth member?** Give it a base in `MORPH_FAMILY` ordered by its travel, take its
transition from `familySpring`/`familyTween`, self-gate `useReducedMotion()` in JS, and expose a
`data-*` attribute naming the branch that ran (`data-morph`, `data-bud`, `data-liquid-shape`) so
the off-switch is assertable from the DOM. `ui/motion/vocabulary.test.ts` enforces the first
three from the source; `ui/motion/family.reducedMotion.test.tsx` enforces the last.

**The two off-switches are not the same switch**, and this is the easiest thing in the whole
file to get wrong:

- `prefers-reduced-motion` means **instant**, and it is self-gated in JS by every member. The
  global CSS rule only kills CSS transitions and the root `MotionConfig` only neutralizes framer
  *transforms* — neither one stops a `borderRadius` animation, a projection node, or a
  `duration: 0` transition from still projecting a frame. A member renders a plain element.
- `expressiveness = 0` means **the floor, still moving**. `expr()` keeps `floor` of its input, so
  a refined `Morph` is a calmer spring, not a still one. What *does* switch off at 0 is the heavy
  tier: `exprHeavy()` goes false, and `LiquidShape` drops its idle breathe and `Disintegrate` its
  scatter/blur entirely rather than shrinking them. The whole family splits at `exprHeavy()`'s
  default threshold — passing your own would give the family two definitions of "bold".

---

## 6. Route transitions

Navigating between pages crossfades, and no page opts in. The hash router wraps the one
commit that changes the route in `viewTransition()`, so a nav click and browser
back/forward both animate through the same seam.

There is one rule, and it is the only thing to remember if you ever touch this:

> **A view transition may never gate a state change.** Never `await` one before
> committing, and never put the URL write inside it.

`viewTransition(update)` runs `update` **exactly once on every path** — no API (a third
of browsers, plus jsdom), a `startViewTransition` that throws, and an animation that
never settles (the transition object is dropped, never awaited). Get this wrong and a
cosmetic nicety becomes a lost navigation on the browsers you don't test in. The
function is deliberately not `async` and returns `void` so the bug is hard to write.

```tsx
// WRONG — a hung or unsupported transition now eats the navigation
await document.startViewTransition(() => setRoute(next)).finished
// RIGHT — the URL is already written; the commit cannot be lost
viewTransition(() => { flushSync(() => setRoute(next)) })
```

Two details worth knowing before you reuse it:

- **`flushSync` is required, not defensive.** The browser captures the "after" frame as
  soon as the callback returns, and React 18 commits a plain `setState` later on the
  scheduler — without the flush both snapshots are the *old* frame and the crossfade
  animates nothing.
- **Reduced motion is gated inside `viewTransition`, at call time.** It takes no `reduce`
  argument, so no call site can forget it or overrule the user's OS setting. The reduced
  answer is *no transition at all* — the instant swap.

What deliberately does **not** animate: query-only changes (opening a detail panel,
a tab, a filter, a search keystroke) and `replace` navigations, which are URL
*corrections* rather than something the user navigated to. Fading the whole page under
an opening panel — or once per keystroke — is the "motion that fights the task" the
budget exists to prevent. The curve and duration live in `tokens.css` under
`::view-transition-old/new(root)`; that rule is taste, and the JS is the mechanism.

---

## 7. Adding a Motion token

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
