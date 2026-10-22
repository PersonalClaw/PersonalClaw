# The rig, measured

The record of the rig spike ([#3887]): both ways of extending the agent orb's drawing that
[`../rig.md`](../rig.md#candidates) names, measured in a real desktop renderer against the bars it
sets, with the choice its rule gives. The script that took the numbers and every run's raw numbers
are beside this file ([the method](#the-method)).

**Status: the rig is extended in place.** With the buddy's dots and its pose both on one clock at
30 frames a second, the orb at 48 px on a 120 Hz display, in place passes every bar a rig can meet
on both of its runs, 4.27% and 4.44% of one core while animating, and the layered candidate fails
the animating bar on both, 5.18% and 5.13% ([the numbers](#the-numbers), [the choice](#the-choice)).
The renderer ([#3891]) starts from in place. Two rows are not this spike's to fill: the memory bar
is measured on the buddy window's own bundle once it exists ([#3831], [#3891]), and the Linux
column is taken with the buddy's acceptance in CI ([#3902]).

## What the spike drew

The list in [`../rig.md`](../rig.md#candidates) holds: each candidate is the agent orb's drawing
plus what that list names, and the spike needed nothing the list leaves out. It draws the v0.3.0
set, the change into each of the four families and the focused card:

| Phase | Family | The agent orb's drawing | What the buddy adds |
|---|---|---|---|
| idle | quiet | breathing, still, in the rest ink | sparse dots |
| thinking | active | breathing, moving, in the alive ink | the working mark |
| tool | active | working, moving, in the alive ink | the working mark |
| approval | waiting | still, in the alive ink | the dots drawn in tight; "!" and the wash in the warn tone |
| input | waiting | still, in the alive ink | the whole orb lifted; "?" and the wash in the info tone |
| error | blocked | still, in the rest ink | the dots sunk toward the lower edge; "×" and the wash in the danger tone |
| done | quiet | still, in the rest ink | sparse dots that swirl once; the check in the ok tone |

The focused status card opens on `playful` and closes on the effects curve, with the orb at 48 px
inside it. Both candidates read one table of poses (`web/src/buddyRig/poses.ts`), so they draw the
same poses on the same presets and differ only in how they draw them. Every amplitude goes through
`expr()`, every move is one of the four presets or the effects curve, and no spring is written
outside `web/src/design/motion.ts`: `reducedMotionAppWide.test.ts` passes with the spike's code in
the tree, and `SPRING_PARAM_BASELINE` keeps its three entries. Left to later work, as the approved
update moves them: the microphone and offline marks, the badge unfolding into words, the presence
poses, the answers' nod and shake, and play.

## The candidates

Both paint the agent orb's drawing with its own engine and painter (`thinking-orbs`), in a canvas
of their own, at most at [the buddy's frame rate](#the-buddys-frame-rate):

1. **Extended in place** (`web/src/buddyRig/CandidateInPlace.tsx`). One canvas: the phase's
   drawing with the pose's density and arrangement, the whole orb's rise and fall inside the frame,
   and the badge and the wash drawn as dots in the alert's tone. One frame loop paints all of it
   while the dots or the pose move, and it pauses offscreen.
2. **Inside a code-drawn layer** (`web/src/buddyRig/CandidateLayered.tsx`). The drawing gains only
   its dots' density and arrangement, and an SVG layer around it holds the badge (a ringed glyph)
   and the wash (a gradient). The layer is painted with the tokens as CSS variables and moved, with
   the whole orb, by the same presets through motion values. The spike's first runs drew the dots
   with `thinking-orbs`' own component, which paints every frame the display draws and takes no
   rate; to take the cap, the candidate now paints the same drawing itself.

## The buddy's frame rate

The buddy paints its dots at most 30 frames a second (`BUDDY_FPS` in `poses.ts`). The agent orb in
chat draws through the same clock at the same rate (`web/src/ui/orbDots.ts`). A capped clock waits
out the gap on a timer and only then asks for one frame, so the page draws nothing between two
paints: the working hold measured 28.0 to 28.6 frames a second. A scheme change repaints the dots at
once, moving or still, rather than at the next capped paint.

The pose moves on the same clock. The motion library runs each change of pose on the app's presets,
through a driver of the clock's (`cappedDriver`), so a change steps its values at most 30 times a
second, in the frame the dots are painted in and just before they are, instead of on every frame
the display draws. In place painted the pose only at the capped rate already, so what it shows does
not change; the motion library stopped doing the work between two paints that nobody saw. The
layered candidate's SVG layer, which the motion library moves with those values, now moves at 30
frames a second too.

## The environment

| | macOS | Linux |
|---|---|---|
| Machine | Mac17,8: Apple M5 Pro, 18 cores, 48 GB | pending |
| OS | macOS 27.0.1 (26A434) | pending |
| Display | 120 Hz, scale 2 | pending |
| Electron | 44.7.2, Chromium 152.0.7977.149 | pending |
| Build | this change over `f8aa737c`, built with `VITE_BUDDY_RIG=1` | pending |
| Window | 420 by 600 px in the lower right corner of the 120 Hz display, shown without focus and kept above other windows on every space; the orb at 48 px, its dots and its pose at 30 frames a second | pending |

The machine is shared. A run starts only while the 5-minute load average is under the gate,
re-checking each minute for up to an hour, and every sample carries the load it was taken under.
The record's runs went at 5-minute loads of 12 to 23, each beside its numbers.

## The numbers

Two runs of each candidate on macOS, each number against its bar. CPU is the renderer's, in percent
of one core, from its CPU time over each second; the at-rest window is 60 s in the quiet family
with nothing moving, and the animating window is 60 s of the loop: into each of the four families
and back, then the card open and closed, each step held 800 ms. The runs are in `rig-runs/`.

| Measure | Bar | In place, run 1 | In place, run 2 | Layered, run 1 | Layered, run 2 | Linux |
|---|---|---|---|---|---|---|
| CPU at rest, visible | under 0.5% | 0.29%, passes | 0.32%, passes | 0.27%, passes | 0.31%, passes | [#3902] |
| CPU while animating | under 5% | 4.27%, passes | 4.44%, passes | 5.18%, fails | 5.13%, fails | [#3902] |
| Frames while still | zero | 0, passes | 0, passes | 0, passes | 0, passes | [#3902] |
| Memory | under 60 MB, on the buddy window's own bundle | 178 MB, pending | 196 MB, pending | 164 MB, pending | 191 MB, pending | [#3902] |
| Bundle size | under 150 KB | 2.53 KB, passes | 2.53 KB, passes | 2.38 KB, passes | 2.38 KB, passes | [#3902] |
| Live retint | within one frame | 1 frame moving, 1 still: passes | 1 and 1: passes | 1 and 1: passes | 1 and 1: passes | [#3902] |
| The dials | overshoot and travel scale | passes | passes | passes | passes | [#3902] |
| Reduced motion | a still per state | passes | passes | passes | passes | [#3902] |
| The working hold, alone | none of its own | 5.56% | 6.47% | 5.48% | 5.53% | [#3902] |
| Frames in the 60 s loop | none of its own | 936 | 936 | 1,437 | 1,434 | [#3902] |
| 5-minute load (start, mean, highest) | under 45 | 20, 19, 20 | 17, 20, 23 | 15, 14, 15 | 12, 12, 13 | [#3902] |

What each row holds:

- **CPU** is the renderer's alone. The GPU process, which composites what the renderer draws, adds
  3.6 to 3.8% of a core while animating in place and 4.0 to 4.1% layered, whose SVG layer is
  composited apart from the canvas.
- **Memory** is the renderer's working set at its highest sample: 139 to 164 MB with the harness
  alone and no candidate, so it is the renderer loading the web app's own bundle, not either rig.
  The bar stays and is the buddy window's to meet: that window gets a small bundle of its own
  ([#3831], [#3891]), and the bar is measured on that bundle once it exists. Until then this row is
  pending, not failed.
- **Bundle size** is the gzip of the chunks only the candidate loads: its own chunk (1.64 KB in
  place, 1.49 KB layered) and the pose code both share (0.89 KB). The thinking-orbs drawing,
  framer-motion and the capped clock (`orbDots.ts`, the agent orb's in chat) are shared app code
  the entry already loads.
- **Live retint** counts the display's frames from the scheme change to the painted dots taking the
  new hue: once while the dots move, and once as a still under reduced motion. Both repaint as the
  change lands, since both painters are their own.
- **The dials**, the same in every run of both, since both read one pose: bounciness moves each
  spring's overshoot from about nothing at 0 to its preset's at 1, and expressiveness moves each
  pose's travel from 35% of it at 0 to all of it at 1, with no pose tuned on its own:

  | Move | Preset | Overshoot at bounciness 0, 1 | Travel at expressiveness 0, 1 |
  |---|---|---|---|
  | an approval draws the dots in | `snappy` | 0%, 6.2% | 9%, 26% of the radius |
  | a question lifts the orb | `snappy` | 0%, 6.1% | 2.0 px, 5.8 px at 48 px |
  | an error sinks the dots | `smooth` | 0%, 0% | 25%, 70% of a full droop |
  | done swirls the dots | `playful` | 0.9%, 31.4% | 0.35, 1 turn |

  The values are read where the pose is painted, 30 times a second, so a peak can fall between two
  readings: an approval's overshoot read 6.4% when the values moved on every frame the display drew.

- **Reduced motion**: with the system setting on, every change in the 20 s loop was a jump: no pose
  value took a value between two poses (each changed at most 6 times in 25 steps). The page ran 7
  or 8 frame callbacks in those 20 s in place and 37 or 38 layered, whose SVG layer the motion
  library renders for each instant change; no frame loop ran.
- **The working hold** is 20 s in the working pose alone: the pose an orb holds for as long as an
  agent works, its dots moving at 30 frames a second. It has no bar of its own; the loop holds the
  working pose for one of its steps.
- **Frames in the loop** counts every animation-frame callback the page ran in the 60 s loop. It
  was 3,026 to 3,062 at 120 Hz while the pose moved on every frame the display drew
  ([before the pose was capped](#before-the-pose-was-capped)).

The rows the table cannot hold:

- **Reads at 18 px**: the owner's judgement, from the contact sheet ([how to watch](#how-to-watch)).
- **Authoring cost**: not timed as its own task. A new pose is one entry in the shared table
  (`poseOf` and `poseTransition` in `poses.ts`) for both candidates; a new badge mark is a dot
  matrix in place and a text glyph in the layer. An estimate from building the spike: under an
  hour for either, and not a measure that tells them apart.
- **Licence**: both pass. Neither adds a package or an asset: the drawing is `thinking-orbs` (MIT)
  and the motion `framer-motion` (MIT), both already the web app's, with their notices in the
  build's third-party notices.

## The choice

The rule ([#3887], open question 5): the smallest by the chunks only the candidate loads, among the
candidates whose every run passes the at-rest and the animating CPU bars and retints within a
frame.

**It names in place.** It is the only candidate whose every run passes both CPU bars: 0.29% and
0.32% at rest, 4.27% and 4.44% while animating. Both candidates retint within a frame on every run,
moving and still, and pass every other bar a rig can meet.

Each candidate, with the measure it lost on:

- **Layered** lost on CPU while animating in both runs: 5.18% at a load of 15 and 5.13% at 12. Its
  own chunks are smaller, 2.38 KB against 2.53 KB, which the rule weighs only among candidates that
  pass. Its dots cost what in place's do; what it adds is the SVG layer, which the motion library
  renders on its own frames: its loop ran 1,434 to 1,437 frames to in place's 936.

`node docs/design/buddy/decisions/rig-measure.mjs --summary` reads `rig-runs/` and names the same.

## Before the pose was capped

The runs before the pose moved on the dots' clock, kept for what they show; none of them is the
record's.

**The first record**, the dots capped and the pose not, at loads of 35 to 46 (`rig-runs/loaded/`,
build `692a48e6`): in place 4.83% and 5.43% while animating, layered 6.07% and 6.54%; at rest 0.20
to 0.26%; the GPU process 4.0 to 4.5% in place and 5.6 to 6.0% layered.

**The quiet re-measure**, the same code at loads under 30, builds `53eeaeed` and `1cb803e1`. The
120 Hz runs put the window on the built-in panel with a copy of the window script that picked the
fastest display, which `--hz` now does; the 60 Hz runs went on an external monitor.

| CPU while animating, bar 5 | 120 Hz, the built-in panel | 60 Hz, the external monitor |
|---|---|---|
| In place, run 1 | 5.78%, fails (load 25, highest 26) | 4.24%, passes (load 27, highest 29) |
| In place, run 2 | 5.04%, fails (load 27, highest 29) | 3.95%, passes (load 26, highest 27) |
| Layered, run 1 | 5.58%, fails (load 23, highest 27) | 4.48%, passes (load 32, highest 33) |
| Layered, run 2 | 5.77%, fails (load 18, highest 19) | 4.38%, passes (load 24, highest 25) |

The load was not the cause: at 120 Hz the quiet runs land where the loaded ones did. The cap held,
the working hold painting 572 to 574 frames in its 20 s, but the motion library moved the pose's
values at the display's rate while a change ran, so the 60 s loop drew 3,026 to 3,062 frames at
120 Hz and 1,657 to 1,691 at 60 Hz. That is why 60 Hz passed and 120 Hz did not, and it is what the
pose on the dots' clock removed. The runs are in `rig-runs/quiet/` and `rig-runs/quiet-60hz/`, each
read by `--summary --out docs/design/buddy/decisions/rig-runs/<folder>`; each run file records its
display's rate as `window.refreshHz`. The record's runs were not repeated at 60 Hz: the 60 Hz
monitor was no longer attached, and a pose that steps 30 times a second asks a 60 Hz display for no
more than it asks a 120 Hz one.

## Sizes

The buddy's 18 and 24 px draw from the agent orb's 20 px drawing, and its 48 and 96 px from the
64 px one (`tunedDrawingFor` in `poses.ts`). Both candidates draw each size at its own size, from
the shared drawing's dot counts and radii, so all four are crisp; the spike's layered candidate
scaled a tuned canvas instead, and its 96 px was soft. No size has a tuned drawing of its own.

## The spike's first runs

Before the cap, every candidate drew every frame the display did, 120 a second, and the card's orb
was never capped. Those runs, at loads of 33 to 50, are in `rig-runs/spike/` (`--summary --out
docs/design/buddy/decisions/rig-runs/spike` reads them):

- both at 48 px: 0.20 to 0.26% at rest; 10.9% and 10.8% while animating in place, 12.0% and 11.3%
  layered; the working hold 18.9 to 20.0% at 120 frames a second; retint within a frame in place,
  three frames layered, whose dots were then the agent orb's component and took a new ink only
  through a render;
- in place capped at 30 frames a second, the stage's orb only: 5.55% and 5.57% while animating;
- at 24 px from the 20 px drawing, every frame: 7.69% in place and 6.78% layered.

## How to watch

An optional how-to, not a gate on the choice: how the owner can watch both candidates side by side.

1. Build the web app with the harness and serve it from a scratch home:

   ```bash
   VITE_BUDDY_RIG=1 npm run build --workspace web
   PERSONALCLAW_HOME=<scratch home> .venv/bin/personalclaw gateway --no-open --no-crons --port <port>
   ```

2. Sign in with the link `PERSONALCLAW_HOME=<scratch home> .venv/bin/personalclaw token` prints,
   then open `#/buddy-rig`. It opens on both candidates side by side at 48 px, their dots at the
   buddy's 30 frames a second. **Size** picks 18, 24, 48 or 96 px, and **Contact sheet** shows
   every pose of both at every size, still.
3. **State** plays each change: Idle, Thinking, Working, Approval, Question, Error and Done. **Open
   the card** opens the focused status card; **Play the loop** runs the measured loop. Adding
   `&fps=0` to the address draws every frame the display does, as the spike's first runs did.
4. **Dials**: Bounciness and Expressiveness at 0 and at 1. These are the app's own Motion dials, so
   set them back after watching.
5. **Scheme**: pick two (Coral and Jade show it best) and watch both candidates retint while they
   move. **Switch to light** and back for each scheme. These are the app's own settings too.
6. **Reduced motion** is the system's: turn on Reduce motion in the system's accessibility
   settings, and the harness says it is on. Every change is then a still; play each state again.

A release build carries none of this. The harness is imported only behind `VITE_BUDDY_RIG`, so a
build without it emits no part of it (`web/src/buddyRig/route.test.ts` holds the shape).

## The method

[`rig-measure.mjs`](rig-measure.mjs) runs one candidate at a time in a scratch Electron window,
[`rig-window.cjs`](rig-window.cjs), signed in to the scratch home's gateway the way the desktop
app's windows are (`desktop/localSignIn.js`), and writes each run to `rig-runs/`:

```bash
node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home> --runs 2
node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home> --runs 2 --hz 60
node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home> --runs 1 --size 24
node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home> --runs 1 --fps 0
node docs/design/buddy/decisions/rig-measure.mjs --summary
```

For each candidate the window measures, in this order:

- **The chunks**: it loads the harness with no candidate, then with the candidate. The chunks the
  second load fetched that the first did not are the candidate's own, gzipped at level 9 from the
  build output. The harness's own chunks, and the shared app code `index.html` loads, are not.
- **At rest and animating**: `app.getAppMetrics()` each second for 60 s each, for the renderer
  (`webContents.getOSProcessId()`), its CPU time and its working set. A script the window adds
  before any of the app's own counts every animation-frame callback the page runs, which is the
  zero-frames check, and stops the run if the page is ever hidden.
- **The working hold**: 20 s in the working pose alone, beside the loop, since the orb holds it for
  as long as an agent works.
- **The retint**: a scheme change while the dots move, and again as a still under reduced motion,
  counting the frames until the painted dots' mean hue is within 15 degrees of the new alive ink.
- **The dials**: bounciness and expressiveness each at 0 and 1, and four moves at each (an
  approval's draw-in, a question's lift, an error's droop, done's swirl), read from the pose's own
  values on every frame: how far each travelled and how far past its target it went.
- **Reduced motion**: the system setting, emulated through the DevTools protocol, then the loop
  again for 20 s: the frames it ran and how many times each pose value changed (a jump is once).

The record's setting is the orb at 48 px and its dots at the buddy's 30 frames a second, on a 120 Hz
display, and its files carry no setting in their names. Another setting names itself in its files
and is never mixed with the record's: `--size 24` adds `24px-`, `--fps 60` adds `60fps-`, `--fps 0`,
every frame the display draws, adds `uncapped-`, and `--hz 60` adds `60hz-`. The window opens on
the display that refreshes `--hz` times a second, whichever display is the main one, and a machine
with none is refused; a run whose window drew at another rate is refused too. `--summary` reads each
setting apart. The 5-minute load gate is `--max-load` (30); the record's runs set 45.

The Linux column takes the same commands on a Linux host with a display, or on the container runner
with a virtual one, and goes with the buddy's acceptance in CI ([#3902]).

## Where the spike's code lives

Open question 1 of [#3887] proposes keeping the measurement script and this record, and dropping
the losing candidate's code before the renderer ([#3891]) lands. That stands: the harness and both
candidates stay in `web/src/buddyRig/`, built only with the flag, and the layered candidate goes
when the renderer lands, starting from in place.

[#3831]: https://github.com/PersonalClaw/PersonalClaw/issues/3831
[#3887]: https://github.com/PersonalClaw/PersonalClaw/issues/3887
[#3891]: https://github.com/PersonalClaw/PersonalClaw/issues/3891
[#3902]: https://github.com/PersonalClaw/PersonalClaw/issues/3902
