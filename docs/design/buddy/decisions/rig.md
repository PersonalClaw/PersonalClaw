# The rig, measured

The record of the rig spike ([#3887]): both ways of extending the agent orb's drawing that
[`../rig.md`](../rig.md#candidates) names, measured in a real desktop renderer against the bars it
sets, with the choice its rule gives. The script that took the numbers and every run's raw numbers
are beside this file ([the method](#the-method)).

**Status: no rig chosen.** With the buddy's dots capped at 30 frames a second, the orb at 48 px,
neither candidate passes the animating CPU bar on both of its runs on this Mac, so the rule names
none ([the choice](#the-choice)). In place passed it once and is ahead of the layered candidate on
every run; which way to go from here is the owner's. The memory bar is pending until the buddy
window has its own bundle, and the Linux column waits for a Linux host or the container runner.

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
   with the agent orb's own component, which paints every frame the display draws and takes no
   rate; to take the cap, the candidate now paints the same drawing itself.

## The buddy's frame rate

The buddy paints its dots at most 30 frames a second (`BUDDY_FPS` in `poses.ts`). The agent orb in
chat is unchanged and draws every frame the display does. A capped loop waits out the gap on a
timer and only then asks for one frame, so the page draws nothing between two paints: the working
hold measured 28.5 to 28.6 frames a second. A scheme change repaints the dots at once, moving or
still, rather than at the next capped paint. The motion library still moves the pose's values, and
the layered candidate's SVG layer, at the display's rate while a change is moving.

## The environment

| | macOS | Linux |
|---|---|---|
| Machine | Mac17,8: Apple M5 Pro, 18 cores, 48 GB | pending |
| OS | macOS 27.0.1 (26A434) | pending |
| Display | 120 Hz, scale 2 | pending |
| Electron | 44.7.2, Chromium 152.0.7977.149 | pending |
| Build | this change over `692a48e6`, built with `VITE_BUDDY_RIG=1` | pending |
| Window | 420 by 600 px in the screen's lower right corner, shown without focus and kept above other windows on every space; the orb at 48 px, its dots at 30 frames a second | pending |

The machine is shared. A run starts only while the 5-minute load average is under 30, re-checking
each minute for up to an hour, and every sample carries the load it was taken under. The load did
not fall under 30 in the hour (it ran 34 to 66), so the runs below went at 35 to 46, and each run's
load is beside its numbers. Three windows stopped before their run was done, on a hidden window or a
page that did not load within 30 s; each was run again, and none of their numbers is in the record.

## The numbers

Two runs of each candidate on macOS, each number against its bar. CPU is the renderer's, in percent
of one core, from its CPU time over each second; the at-rest window is 60 s in the quiet family
with nothing moving, and the animating window is 60 s of the loop: into each of the four families
and back, then the card open and closed, each step held 800 ms.

| Measure | Bar | In place, run 1 | In place, run 2 | Layered, run 1 | Layered, run 2 | Linux |
|---|---|---|---|---|---|---|
| CPU at rest, visible | under 0.5% | 0.20%, passes | 0.22%, passes | 0.26%, passes | 0.21%, passes | pending |
| CPU while animating | under 5% | 4.83%, passes | 5.43%, fails | 6.07%, fails | 6.54%, fails | pending |
| Frames while still | zero | 0, passes | 0, passes | 0, passes | 0, passes | pending |
| Memory | under 60 MB, on the buddy window's own bundle | 307 MB, pending | 280 MB, pending | 196 MB, pending | 208 MB, pending | pending |
| Bundle size | under 150 KB | 2.72 KB, passes | 2.72 KB, passes | 2.66 KB, passes | 2.66 KB, passes | pending |
| Live retint | within one frame | 1 frame moving, 1 still: passes | 1 and 1: passes | 1 and 1: passes | 1 and 1: passes | pending |
| The dials | overshoot and travel scale | passes | passes | passes | passes | pending |
| Reduced motion | a still per state | passes | passes | passes | passes | pending |
| The working hold, alone | none of its own | 5.23% | 5.61% | 5.92% | 6.30% | pending |
| 5-minute load (start, mean, highest) | under 30 | 35, 35, 40 | 44, 43, 45 | 44, 46, 46 | 40, 39, 41 | pending |

What each row holds:

- **CPU** is the renderer's alone. The GPU process, which composites what the renderer draws, adds
  4.0 to 4.5% of a core while animating in place and 5.6 to 6.0% layered, whose SVG layer the
  motion library moves at the display's rate.
- **Memory** is the renderer's working set at its highest sample: 148 to 220 MB with the harness
  alone and no candidate, so it is the renderer loading the web app's own bundle, not either rig.
  The bar stays and is the buddy window's to meet: that window gets a small bundle of its own
  ([#3831], [#3891]), and the bar is measured on that bundle once it exists. Until then this row is
  pending, not failed.
- **Bundle size** is the gzip of the chunks only the candidate loads: its own chunk (1.56 KB in
  place, 1.49 KB layered) and the pose and frame-loop code both share (1.17 KB). The thinking-orbs
  drawing and framer-motion are shared app code already in the entry. Beyond `index.html` the route
  also fetched the harness's own chunk and two more it pulls in, 87 KB in all with the candidate.
- **Live retint** counts the display's frames from the scheme change to the painted dots taking the
  new hue: once while the dots move, and once as a still under reduced motion. Both repaint as the
  change lands, since both painters are their own.
- **The dials**, the same in every run of both, since both read one pose: bounciness moves each
  spring's overshoot from about nothing at 0 to its preset's at 1, and expressiveness moves each
  pose's travel from 35% of it at 0 to all of it at 1, with no pose tuned on its own:

  | Move | Preset | Overshoot at bounciness 0, 1 | Travel at expressiveness 0, 1 |
  |---|---|---|---|
  | an approval draws the dots in | `snappy` | 0.3%, 6.4% | 9%, 26% of the radius |
  | a question lifts the orb | `snappy` | 0.2%, 6.4% | 2.0 px, 5.8 px at 48 px |
  | an error sinks the dots | `smooth` | 0%, 0% | 25%, 70% of a full droop |
  | done swirls the dots | `playful` | 0.9%, 31.9% | 0.35, 1 turn |

- **Reduced motion**: with the system setting on, every change in the 20 s loop was a jump: no pose
  value took a value between two poses (each changed at most 6 times in 25 steps). The page ran 30
  to 39 frame callbacks in those 20 s, one or two a step, which is the motion library applying each
  instant change; no frame loop ran.
- **The working hold** is 20 s in the working pose alone: the pose an orb holds for as long as an
  agent works. It has no bar of its own; it is here because it is what the animating number is made
  of, and at 30 frames a second it is about the bar on its own.

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

**It names none.** Both pass at rest, 0.20 to 0.26% of one core, and both retint within a frame on
every run, moving and still. Neither passes the animating bar on both runs. Were both to pass, the
rule would name the layered candidate: its own chunks are 2.66 KB against 2.72 KB in place.

Each candidate, with the measure it lost on:

- **In place** lost on CPU while animating in its second run: 5.43% at a load of 43, after 4.83%
  at 35 in its first. It passes every other bar a rig can meet, it is the only candidate to pass
  the animating bar in any run, and it costs less than the layered candidate on every run, by 0.6
  to 1.7 points.
- **Layered** lost on CPU while animating in both runs: 6.07% at a load of 46 and 6.54% at 39.
  Its dots cost what in place's do; what it adds is the SVG layer, which the motion library moves
  at the display's rate while a change is moving, where in place paints the same moves in its
  capped loop.

Every run went at a load above the gate's 30, which costs CPU time of its own: in place's two runs
differ by 0.6 points between a load of 35 and one of 43. Until the owner decides, the record names
no rig.

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

The record's setting is the orb at 48 px and its dots at the buddy's 30 frames a second, and its
files carry no setting in their names. Another setting names itself in its files and is never mixed
with the record's: `--size 24` adds `24px-`, `--fps 60` adds `60fps-`, and `--fps 0`, every frame
the display draws, adds `uncapped-`. `--summary` reads each setting apart.

The Linux column takes the same commands on a Linux host with a display, or on the container runner
with a virtual one; until then it is pending.

## Where the spike's code lives

Open question 1 of [#3887] proposes keeping the measurement script and this record, and dropping
the losing candidate's code before the renderer ([#3891]) lands. That stands: the harness and both
candidates stay in `web/src/buddyRig/`, built only with the flag, and the losing candidate goes when
the renderer lands, starting from the winner.

[#3831]: https://github.com/PersonalClaw/PersonalClaw/issues/3831
[#3887]: https://github.com/PersonalClaw/PersonalClaw/issues/3887
[#3891]: https://github.com/PersonalClaw/PersonalClaw/issues/3891
