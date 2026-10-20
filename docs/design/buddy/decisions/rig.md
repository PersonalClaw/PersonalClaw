# The rig, measured

The record of the rig spike ([#3887]): both ways of extending the agent orb's drawing that
[`../rig.md`](../rig.md#candidates) names, measured in a real desktop renderer against the bars it
sets, with the choice its rule gives. The script that took the numbers and every run's raw numbers
are beside this file ([the method](#the-method)).

**Status: provisional, with no rig chosen.** On this Mac neither candidate keeps the animating CPU
bar with the orb at the buddy's 48 px, so the rule names none ([the choice](#the-choice)), and what
it would take is the owner's to decide. Whatever the rule names stays provisional until the owner
has watched both candidates side by side ([how to watch](#how-to-watch)). The Linux column waits for
a Linux host or the container runner.

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

1. **Extended in place** (`web/src/buddyRig/CandidateInPlace.tsx`). One canvas, painted with the
   agent orb's own engine and painter: the phase's drawing with the pose's density and arrangement,
   the whole orb's rise and fall inside the frame, and the badge and the wash drawn as dots in the
   alert's tone. One frame loop paints all of it while the dots or the pose move, and it pauses
   offscreen. A scheme change repaints it as the change lands.
2. **Inside a code-drawn layer** (`web/src/buddyRig/CandidateLayered.tsx`). The agent orb's own
   component, handed only its dots' density and arrangement through its frame function, inside an
   SVG layer that holds the badge (a ringed glyph) and the wash (a gradient). The layer is painted
   with the tokens as CSS variables and moved, with the whole orb, by the same presets.

## The environment

| | macOS | Linux |
|---|---|---|
| Machine | Mac17,8: Apple M5 Pro, 18 cores, 48 GB | pending |
| OS | macOS 27.0.1 (26A434) | pending |
| Display | 120 Hz, scale 2 | pending |
| Electron | 44.7.2, Chromium 152.0.7977.149 | pending |
| Build | this change over `0bd7796e`, built with `VITE_BUDDY_RIG=1` | pending |
| Window | 420 by 600 px, shown without focus and kept above other windows; the orb at 48 px | pending |

The machine is shared. A run starts only while the 5-minute load average is under 30, re-checking
each minute for up to an hour, and every sample carries the load it was taken under. The load did
not fall under 30 within the hour, so the runs below went at 34 to 48, and each run's load is beside
its numbers.

## The numbers

Two runs of each candidate on macOS, each number against its bar. CPU is the renderer's, in percent
of one core, from its CPU time over each second; the at-rest window is 60 s in the quiet family
with nothing moving, and the animating window is 60 s of the loop: into each of the four families
and back, then the card open and closed, each step held 800 ms.

| Measure | Bar | In place, run 1 | In place, run 2 | Layered, run 1 | Layered, run 2 | Linux |
|---|---|---|---|---|---|---|
| CPU at rest, visible | under 0.5% | 0.26%, passes | 0.20%, passes | 0.22%, passes | 0.22%, passes | pending |
| CPU while animating | under 5% | 10.9%, fails | 10.8%, fails | 12.0%, fails | 11.3%, fails | pending |
| Frames while still | zero | 0, passes | 0, passes | 0, passes | 0, passes | pending |
| Memory | under 60 MB | 281 MB, fails | 277 MB, fails | 240 MB, fails | 262 MB, fails | pending |
| Bundle size | under 150 KB | 2.67 KB, passes | 2.67 KB, passes | 2.08 KB, passes | 2.08 KB, passes | pending |
| Live retint | within one frame | 1 frame moving, 1 still: passes | 1 and 1: passes | 3 and 3: fails | 3 and 3: fails | pending |
| The dials | overshoot and travel scale | passes | passes | passes | passes | pending |
| Reduced motion | a still per state | passes | passes | passes | passes | pending |
| The working hold, alone | none of its own | 19.3% | 20.0% | 19.9% | 18.9% | pending |
| 5-minute load (start, mean, highest) | under 30 | 34, 37, 38 | 48, 46, 47 | 39, 42, 46 | 42, 46, 47 | pending |

What each row holds:

- **CPU** is the renderer's alone. The GPU process, which composites what the renderer draws, adds
  8 to 10% of a core more while animating and 12 to 13% in the working hold, in both candidates.
  The loop ran at the display's 120 frames a second whenever something moved.
- **Memory** is the renderer's working set at its highest sample: 159 to 215 MB with the harness
  alone and no candidate, so it is the renderer loading the web app's own bundle, not either rig.
  No rig can meet the 60 MB bar while the buddy's window loads the whole app.
- **Bundle size** is the gzip of the chunks only the candidate loads: its own chunk (1.85 KB in
  place, 1.27 KB layered) and the pose code both share (0.81 KB). The thinking-orbs drawing and
  framer-motion are shared app code already in the entry. Beyond `index.html` the route also
  fetched the harness's own chunk and two more it pulls in, 84 KB in all, with no candidate too.
- **Live retint** counts the display's frames from the scheme change to the painted dots taking the
  new hue: once while the dots move, and once as a still under reduced motion. In place repaints as
  the change lands, since its painter is its own; the layered candidate's dots are the agent orb's
  component, which takes the new ink only through a render, three frames later.
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
  value took a value between two poses (each changed at most 6 times in 25 steps). The page ran 29
  to 38 frame callbacks in those 20 s, one or two a step, which is the motion library applying each
  instant change; no frame loop ran.
- **The working hold** is 20 s in the working pose alone: the pose an orb holds for as long as an
  agent works, and the loop's most expensive part. It has no bar of its own; it is here because it
  is what the animating number is made of.

The rows the table cannot hold:

- **Reads at 18 px**: the owner's judgement, from the contact sheet ([how to watch](#how-to-watch)).
  Pending.
- **Authoring cost**: not timed as its own task. A new pose is one entry in the shared table
  (`poseOf` and `poseTransition` in `poses.ts`) for both candidates; a new badge mark is a dot
  matrix in place and a text glyph in the layer. An estimate from building the spike: under an
  hour for either, and not a measure that tells them apart.
- **Licence**: both pass. Neither adds a package or an asset: the drawing is `thinking-orbs` (MIT)
  and the motion `framer-motion` (MIT), both already the web app's, with their notices in the
  build's third-party notices.

## The choice

The rule ([#3887], open question 5): the smallest by the chunks only the candidate loads, among the
candidates whose every run passes the at-rest and the animating CPU bars.

**At 48 px it names none.** Both candidates pass at rest, 0.20 to 0.26% of one core, and both fail
while animating, 10.8 to 12.0% against 5%, on every run. If the animating bar were met, the rule
would name the layered candidate: its own chunks are 2.08 KB against 2.67 KB in place.

Each candidate, with the measure it lost on:

- **In place** lost on CPU while animating: 10.9% and 10.8%. It passes every other bar a rig can
  meet, and is the only one that retints within a frame.
- **Layered** lost on CPU while animating, 12.0% and 11.3%, and on the live retint, three frames
  moving and three still.

Both also fail the memory bar, which is the window's to meet rather than the rig's
([the numbers](#the-numbers)).

### What the animating number is made of

The drawing, not the extension. The working hold alone, the agent orb's own working drawing with
nothing the buddy adds moving, costs 18.9 to 20.0% of one core at 120 frames a second in both
candidates, more than the loop's mean, which spends part of each cycle still. What the two
extensions add on top differs by a percent or so, inside the runs' own noise.

Two things bring it down, and neither gets it under 5% alone:

- **A capped frame rate.** In place owns its frame loop, so it can paint at 30 frames a second and
  ask for no frame in between. Measured twice (`macos-in-place-30fps-1.json` and `-2`): 5.55% and
  5.57% while animating, the working hold 5.90% and 5.00%, at rest 0.22% and 0.20%, retint within
  three frames moving and one still, at loads of 33 to 39. The layered candidate's frames are the
  agent orb component's own, which takes no rate.
- **A smaller drawing.** At 24 px, from the agent orb's 20 px drawing, measured once each
  (`macos-in-place-24px-1.json`, `macos-layered-24px-1.json`): 7.69% in place and 6.78% layered
  while animating, the working hold 9.00% and 7.35%, at loads of 43 to 50.

So the owner decides between:

1. **Moving the animating bar** to what the agent orb's own drawing costs while it works, the one
   sanctioned continuous motion. The rule then names the layered candidate, smaller by 0.59 KB,
   though it misses the one-frame retint by two frames.
2. **Capping the dots' frame rate** (a taste call: 30 frames a second is visibly steppier on a
   fast-moving drawing), which only in place can do as it stands, and which still leaves it just
   over the bar.
3. **Both**, or a lighter drawing for the buddy's sizes than the agent orb's 64 px tuning.

Until the owner decides, the record names no rig.

## Sizes

The buddy's 18 and 24 px share the agent orb's 20 px drawing, and its 48 and 96 px the 64 px one
(`tunedDrawingFor` in `poses.ts`). In place draws each at its own size from the shared drawing's
dot counts and radii, so all four are crisp. The layered candidate's drawing is the agent orb's
component, which draws only at its tuned sizes, so the layer scales its canvas: 18 and 24 px are
the 20 px canvas at 0.9 and 1.2 times, 48 px the 64 px canvas at 0.75 times, and 96 px the 64 px
canvas at 1.5 times, which is soft. Whether each reads at 18 px, and whether any size needs a
drawing of its own, is the owner's from the contact sheet.

## How to watch

1. Build the web app with the harness and serve it from a scratch home:

   ```bash
   VITE_BUDDY_RIG=1 npm run build --workspace web
   PERSONALCLAW_HOME=<scratch home> .venv/bin/personalclaw gateway --no-open --no-crons --port <port>
   ```

2. Sign in with the link `PERSONALCLAW_HOME=<scratch home> .venv/bin/personalclaw token` prints,
   then open `#/buddy-rig`. It opens on both candidates side by side at 48 px. **Size** picks 18,
   24, 48 or 96 px, and **Contact sheet** shows every pose of both at every size, still.
3. **State** plays each change: Idle, Thinking, Working, Approval, Question, Error and Done. **Open
   the card** opens the focused status card; **Play the loop** runs the measured loop.
   Adding `&fps=30` to the address caps the in-place candidate at 30 frames a second, as measured.
4. **Dials**: Bounciness and Expressiveness at 0 and at 1. These are the app's own Motion dials, so
   set them back after watching.
5. **Scheme**: pick two (Coral and Jade show it best) and watch both candidates retint while they
   move. **Switch to light** and back for each scheme. These are the app's own settings too.
6. **Reduced motion** is the system's: turn on Reduce motion in the system's accessibility
   settings, and the harness says it is on. Every change is then a still; play each state again.

A release build carries none of this. The harness is imported only behind `VITE_BUDDY_RIG`, so a
build without it emits no part of it: `npm run build --workspace web` with the flag unset wrote 493
assets, none named after the harness or a candidate and none holding its route, against 499 with
it (`web/src/buddyRig/route.test.ts` holds the shape).

## The method

[`rig-measure.mjs`](rig-measure.mjs) runs one candidate at a time in a scratch Electron window,
[`rig-window.cjs`](rig-window.cjs), signed in to the scratch home's gateway the way the desktop
app's windows are (`desktop/localSignIn.js`), and writes each run to `rig-runs/`:

```bash
node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home> --runs 2
node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home> --runs 2 --candidate in-place --fps 30
node docs/design/buddy/decisions/rig-measure.mjs --home <scratch home> --runs 1 --size 24
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

Two other settings run beside the record's runs and are never mixed with them: `--size 24` draws
the orb at 24 px, from the 20 px drawing, and `--fps 30` caps the in-place candidate's frame rate.
Their files say so in their names, and `--summary` reads each setting apart.

The Linux column takes the same commands on a Linux host with a display, or on the container runner
with a virtual one; until then it is pending.

## Where the spike's code lives

Open question 1 of [#3887] proposes keeping the measurement script and this record, and dropping
the losing candidate's code before the renderer ([#3891]) lands. That stands until the owner says
otherwise: the harness and both candidates stay in `web/src/buddyRig/`, built only with the flag,
so the owner can watch them, and the renderer starts from the winner.

[#3887]: https://github.com/PersonalClaw/PersonalClaw/issues/3887
[#3891]: https://github.com/PersonalClaw/PersonalClaw/issues/3891
