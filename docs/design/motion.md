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

### Adopting a liquid state transition

The pinned artifact tile (`pages/dashboard/PinnedTiles.tsx`) is where the family's in-place member
lives. Its always-mounted header row carries the morph, and the silhouette settles from `blob` to
`squircle` as the tile's body arrives. That pairing is the vocabulary rather than a per-call-site
choice: `blob` is the unsettled, organic form and `squircle` the composed one, so a load reads as
something gathering itself instead of two graphics swapping.

**A state morph needs a host that survives the state change.** This is the one way to adopt
`LiquidShape` that looks right in every screenshot and animates never — so it is worth knowing
before you place one. The primitive holds its progress in a `useMotionValue(active ? 1 : 0)` and
starts the spring from an effect, which means a component that **remounts** on the transition gets
a fresh motion value already sitting at the target: no distance to travel, no frame, no error, no
morph. Put it in a region mounted on both sides of the state it depicts — the tile's header row —
and never inside the branch that switches on it, which for that tile is the ternary swapping the
`WidgetFrame` body in. It is §5a's remount trap one level down, with the failure inverted: there,
forgetting the group
costs the entrance and nothing else; here, hosting it in the wrong place costs the *only* thing the
primitive does.

Three things the call site owes:

- **Text carries the meaning.** The primitive is `aria-hidden` and `pointer-events-none` by
  contract, like `DotGlow` — a screen reader and a keyboard never learn it exists. The tile keeps
  its "Loading tile…" line for exactly that reason: the morph is a second, ambient reading of a
  state the surface already states in words, never the only place a user could learn something.
- **`intensity` is a plain number.** The primitive scales the amplitude through `expr()` itself, so
  pre-scaling at the call site applies the knob twice — an extra factor of `0.35 + 0.65·e`. That is
  13% low at the default expressiveness and 65% low at 0, so it is the shape of bug that ships:
  nearly invisible where it is measured, and worst for the users who dialled the knob *down*.
- **Tint is a theme var.** It defaults to `var(--color-primary)`, and both gradient stops are that
  one var at two opacities. A hex here survives a theme flip.

`pages/dashboard/liquidAdoptionRail.test.ts` names the adopting surface and the candidates
deliberately left out, and fails if the primitive drops back to zero product call sites — which is
the state it shipped in, and the state a refactor returns it to for free.

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

---

## 8. The budget, measured

§2 calls the motion budgeted. This section is what the budget actually costs, measured rather
than asserted, and the numbers are here because the claim they replace ("60fps, no jank") is
the easiest one in this file to fake.

**Why it cannot live in the unit tier.** `requestAnimationFrame` under jsdom is a `setTimeout`
shim with no frame clock — it will report whatever rate you ask it for. So the measurement is a
real browser driven by `scripts/motion_frame_budget.mjs`, next to `render_smoke.mjs` for the
same reason that one is a standalone driver: it loads the built artifact and needs a live
gateway. Deliberately **not** a spec in `web/e2e/`, which is the zero-diff gate tier — a
frame-time distribution has no committed baseline and no machine-independent threshold, so a
red/green there would be a claim about hardware. Nothing in CI runs it (`ci.yml` runs exactly
one file out of `web/e2e/`, `a11y.spec.ts`, by explicit path); it is a driver you point at a
gateway and read.

**Why the unit of the report is a distribution.** A mean hides a stall: 60fps with one 400ms
freeze averages to something respectable, and the freeze is the entire content of "no jank". So
every window reports frames, mean, p50/p95/p99, worst, and the count over one 60Hz frame
(16.7ms) and two (33.3ms). `worstMs` is the jank number; the mean is the throughput number.

**Why an idle number would be worthless.** 60fps while nothing moves proves nothing, so each
surface is measured *during* provoked motion — a route change into it (`viewTransition` plus the
arriving `EntranceGroup` cascade), the command palette opening on `physics.playful`, a
22-row list scrolled by wheel and by arrow key, a surface disclosure, a hover sweep across its
controls — and each run also carries an **idle control window** on the same page in the same
process, so the reader can see what the machine costs at rest. The control is the baseline; it
is never the claim. The runner also samples `document.getAnimations()` inside the window and
refuses to present a window that never observed a running animation as a result.

### Environment (state it or the numbers mean nothing)

`chrome-headless-shell` 151.0.7922.34, headless, 1440×900 at DPR 2, **CPU unthrottled**, Apple
M5 Pro (18 cores), darwin arm64, against a gateway seeded with the `demo-home` fixture. This is
the most generous environment the app will ever run in, and the headline table below is
labelled as such rather than presented as a guarantee — which is why the 4× throttled and
headed-on-real-vsync runs follow it.

One thing that changes how to read every row: **the headless shell does not present at 60Hz.**
Its idle control lands on 8.33ms flat — an uncapped ~120Hz clock. The *same page in a headed
window on this machine's real display* idles at 13.34ms (≈75Hz), so 8.33ms is a property of the
harness's browser, not of the hardware. That makes the headless run the more **sensitive**
instrument — every frame is being asked to fit 8.33ms, twice as demanding as the 60fps floor —
and it means a 16.7ms delta, a full miss at 120Hz, still *meets* the 60fps floor. `over16_7` is
therefore the honest 60fps miss count either way. The two clocks do not rank the same steps the
same, which is the subject of the third table.

### What it measured

Unthrottled, per interaction (`mean` in ms, `worst` in ms, `>16.7` = frames that missed 60fps):

| Surface / step | b=1 mean | b=1 worst | b=1 >16.7 | b=0 mean | b=0 worst | b=0 >16.7 |
|---|---|---|---|---|---|---|
| **idle control** (both surfaces) | 8.33 | ≤10.4 | 0 / 360 | 8.33 | ≤10.5 | 0 / 360 |
| **ChatPage** — whole window | 10.39 | 41.5 | 60 / 754 | 10.34 | 42.9 | 73 / 753 |
| · route crossfade + entrance | 9.73 | 41.4 | 7 / 143 | 9.81 | 35.2 | 13 / 142 |
| · command palette open | **19.32** | 40.5 | 10 / 28 | **20.15** | 42.9 | 11 / 26 |
| · palette list scroll + close | **18.51** | 41.5 | 42 / 100 | **17.38** | 41.4 | 48 / 107 |
| · composer typing + slash menu | 8.45 | 16.8 | 1 / 223 | 8.44 | 17.1 | 1 / 219 |
| · disclosure open/close | 8.33 | 10.3 | 0 / 133 | 8.35 | 10.3 | 0 / 133 |
| · hover sweep | 8.33 | 10.3 | 0 / 127 | 8.33 | 10.3 | 0 / 126 |
| **Loop cockpit** (`#/loops/a17c3f92`) — whole window | 9.95 | 32.2 | 65 / 583 | 10.07 | 34.1 | 54 / 574 |
| · route crossfade + entrance | 8.38 | 16.8 | 1 / 166 | 8.42 | 25.3 | 1 / 165 |
| · disclosure open/close | 8.34 | 10.4 | 0 / 134 | 8.35 | 10.3 | 0 / 133 |
| · hover sweep | 8.33 | 10.3 | 0 / 128 | 8.33 | 10.3 | 0 / 128 |
| · command palette open | **15.17** | 32.2 | 19 / 34 | **15.64** | 34.1 | 12 / 32 |
| · palette list scroll + close | **14.12** | 25.9 | 45 / 121 | **14.79** | 32.0 | 41 / 116 |

Four things fall out of that, and the last two are the ones worth acting on.

1. **No stall anywhere.** The worst single frame across every unthrottled run is **42.9ms** —
   about 2.5 dropped frames at 60Hz, a hitch rather than a freeze. Nothing crossed 50ms.
2. **Both dials cost the same.** Every b=1 and b=0 pair above is within noise of the other.
   That is not luck, it is §2's contract holding: `bouncy()` interpolates **damping only**, so
   bounciness changes the shape of the settle and touches neither stiffness (the preset's
   identity) nor duration. The calm dial is free, and a future change that makes it cheaper
   *or* dearer than playful is a bug in `bouncy()`, not a tuning result.
3. **The loop cockpit's own motion is the cheapest thing measured** — its route entrance,
   disclosure and hover sweep all sit at the 8.33ms idle floor, i.e. they cost nothing
   detectable on this clock. It arrives, it settles, and no frame it owns misses 60fps.
4. **The command palette is the most expensive motion in the app, and it is shell chrome.**
   Its open sustains a 15–20ms mean — 51–66fps — for ~1.3s, and its 22-row list scroll holds
   14–18ms; it costs the same on both surfaces because it belongs to neither. Every frame that
   misses 60fps in the ChatPage column is inside those two steps: strip them and ChatPage's own
   motion is 8.33–9.81ms. **This is a finding, not a fix**: `app/CommandPalette.tsx` is the
   owner of it, and the honest reading is that the app clears its budget everywhere except one
   overlay that animates 22 staggered rows at once, which is §5's "choreograph, don't scatter"
   pointing at itself.

### The same run at 4× CPU throttle

`--cpu-throttle 4` (CDP `Emulation.setCPUThrottlingRate`) is the closest thing here to user
hardware, and it separates the two surfaces sharply. The **idle control is unchanged at
8.33ms** — the resting page does no main-thread work worth throttling — so everything below is
interaction cost:

| Surface | mean | p95 | worst | >16.7 |
|---|---|---|---|---|
| Loop cockpit, b=1 | 10.24 | 18.3 | 49.7 | 65 / 573 |
| Loop cockpit, b=0 | 10.49 | 24.8 | 58.5 | 57 / 561 |
| ChatPage, b=1 | **20.43** | 50.0 | **117.2** | 249 / 469 |
| ChatPage, b=0 | **19.92** | 49.8 | **108.5** | 233 / 469 |

The cockpit degrades gracefully: its own steps stay at 8.4–8.75ms and only the palette gets
dear (16.4ms mean, against 41.7ms for the same overlay on ChatPage). ChatPage does not — its route entrance goes to 26ms mean / 117ms worst, and
**composer typing** goes to 21.5ms mean with a 106.7ms worst, which unthrottled was the
cheapest step on the page (8.45ms). A per-keystroke cost that is invisible at 1× and becomes a
100ms frame at 4× is the shape of bug that ships, and it is the one to look at first if this
page ever feels heavy on a real laptop.

### The same run headed, on the real display — and why it disagrees

`--headed` swaps the headless shell for the full browser in a visible window, which puts the
page on the machine's actual vsync. ChatPage at b=1:

| Window | frames | mean | p50 | p95 | p99 | worst | >16.7 | >33.3 |
|---|---|---|---|---|---|---|---|---|
| idle control | 224 | 13.34 | 13.3 | 15.2 | 15.3 | 15.4 | 0 | 0 |
| interaction | 530 | 14.64 | 13.3 | 15.1 | **81.9** | **162** | 8 | 8 |

Read the two instruments together, because **they rank the steps differently and each hides
what the other shows**:

- Vsync **absorbs** small overruns. `palette list scroll` — the *worst* step headless (18.51ms
  mean, 41.5ms worst) — is spotless here (13.33ms mean, 15.6ms worst): its per-frame work fits
  inside one 13.3ms interval, so the presented cadence never wavers. Every headless p95 above
  is measuring work that a real display would not have shown a user.
- Vsync **exposes** long tasks, and the headless clock spreads them. Every step that was fine
  headless has a single multi-frame stall here: **162ms in composer typing**, 133.4ms in the
  disclosure, 93.3ms in the palette open, 81.9ms in the route entrance — 6–12 dropped frames
  each, i.e. exactly the "60fps with one 400ms stall" that §8's whole shape exists to catch.
  `p99` is 81.9ms against a `p95` of 15.1ms: five nines of clean cadence with a visible hitch
  buried in the tail, which no mean and no p95 would have reported.

**Caveat this one harder than the rest.** A headed Chromium window that loses focus or gets
occluded has its `requestAnimationFrame` throttled by the compositor, and that produces
sporadic long frames indistinguishable from an application stall. This run was not screened for
occlusion, so treat the four stalls as *reproducible-looking candidates* — the throttled column
independently found a 106.7ms frame in the same composer-typing step, which is the corroboration
that makes them worth chasing — and confirm on a focused window before attributing one to a
component. The steady-state columns (mean/p50/p95) are unaffected by that risk.

### The harness, and what it does not see

```bash
# 1. build the SPA and point the gateway's static/dist SYMLINK at it (a `cp -R` here leaves
#    a frozen directory that shadows the link and serves a STALE bundle — `make web-build`
#    is the one that gets the symlink right)
make web-build
# 2. an ISOLATED home, seeded — `demo-home` is what gives the cockpit a launched loop
PERSONALCLAW_HOME="$PWD/.dev-home" PERSONALCLAW_AUTH_MODE=none \
  .venv/bin/personalclaw gateway --seed demo-home --seed-replace --no-open --port 10473
# 3. confirm it is serving YOUR bundle, not a stale one — these two must match
curl -s http://127.0.0.1:10473/ | grep -o 'assets/index-[^"]*\.js'; ls web/dist/assets/index-*.js
# 4. measure
node scripts/motion_frame_budget.mjs --url http://127.0.0.1:10473
node scripts/motion_frame_budget.mjs --url http://127.0.0.1:10473 --inject-stall 200
node scripts/motion_frame_budget.mjs --url http://127.0.0.1:10473 --cpu-throttle 4
node scripts/motion_frame_budget.mjs --url http://127.0.0.1:10473 --headed
```

**`--inject-stall` is the reason to believe any of the above.** It blocks the page's main
thread synchronously from inside the measured window, in its own labelled slice. A 200ms stall
reports `worst 206.4ms` on ChatPage and `206.7ms` on the cockpit, attributed to that slice and
to no other, on runs whose next-worst frame was 42.4ms and 33.4ms. A harness that reported
60fps whether or not the page froze would be measuring nothing, and this is what rules that
out. Re-run it whenever you change the collector.

Four gaps the run does not cover, stated because a measurement's blind spots are part of it:

- **Neither surface's own column scrolls** with the `demo-home` fixture at 1440×900 (`docOver`
  is 0 and every `overflow-y:auto` container fits its content), so the scroll path is measured
  on the palette's 22-row list instead and the per-surface scroll step records an explicit
  skip rather than a zero. A taller fixture — or `--viewport 1280x720`, where the nav rail
  overflows by 149px — is what would close it.
- **The cockpit is not live.** The seeded loop is `status=complete`, so the run stream and its
  live region never animate; what was measured is the cockpit's arrival, its disclosure and its
  hover states. A running loop is the missing case.
- **The headed window was not screened for occlusion**, so its four long frames are candidates
  rather than attributed defects (above).
- **One machine, single runs.** These are one-shot numbers from one Apple-silicon dev box, not a
  distribution over hardware or over repeats. `--cpu-throttle` simulates a slow CPU only — not a
  slow GPU, a cold cache, a 60Hz panel, or a machine with something else running on it.
