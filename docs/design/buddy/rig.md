# Choosing the rig

How the character is drawn and moved is decided by a measured spike, not by preference ([#3887]).
Each candidate draws the same placeholder character through the same transitions, inside the buddy
window and the web app: the change into each of the four families (quiet, active, waiting and
blocked) and the focused status card opening.

## Candidates

1. **Code-drawn SVG**, moved by the motion presets: each layer is an SVG element whose transforms
   the spring presets drive.
2. **Code-drawn canvas**, moved by the same presets.
3. **A state-machine animation runtime** with an authored file holding the states and inputs.
4. **Pre-rendered frames** (a sprite atlas) played per state.

## What is measured

| Measure | How | Bar |
|---|---|---|
| CPU at rest, visible | `app.getAppMetrics()` over 60 s, nothing moving | under 0.5% of one core |
| CPU while animating | the transitions in a loop, 60 s | under 5% of one core |
| Frames while still | an animation-frame spy | zero |
| Memory | the buddy renderer's working set | under 60 MB |
| Bundle size | the compressed size of the chunks only the buddy route loads, since the buddy window loads the web app's own bundle | under 150 KB |
| Live retint | change the colour scheme while it runs | follows within one frame, with no reload |
| The dials | bounciness 0 and 1, expressiveness 0 and 1 | overshoot and amplitude visibly scale, with no per-pose tuning |
| Reduced motion | the system setting on | a still pose per state, no movement at all |
| Reads at 18 px | the tray and the peek | the state is legible |
| Authoring cost | the hours to add one new pose | recorded |
| Licence | the runtime's and the tooling's | compatible with the project's licence and its asset-licence list |

## Starting position

The character is drawn in code first, so the tokens retint it and the dials scale it, and the motion
presets already used across the app move it. An authored-file runtime is chosen only if it wins on
the numbers and a designer will own the file. Pre-rendered frames are an export of the chosen rig
(the portable sprite sheet), not the rig.

The spike chooses the smallest rig that renders the four families and the focused card within the
at-rest and animating CPU bars, smallest by the bundle-size measure. The decision, its numbers and
the rejected candidates with the measure each lost on are recorded in `decisions/rig.md` ([#3887]).

## Later work

### Measurements that move with later work

Later work: play ([#3894]), drop and quick ask ([#3895]), sound ([#3900]) and the sprite sheet
([#3903]).

Each takes its own measurements with it: play its CPU and timer budget, the dizzy reaction and the
drag dangle among them; the drop its transitions; sound its sound-linked motion; and the sprite
sheet its export.

### The portable sprite sheet

Later work: the sprite sheet ([#3903]).

The buddy is also offered as a portable sprite sheet, in a layout desktop pet apps share: an 8 by 9
sheet of 192 by 208 cells, eight frames per row, 1536 by 1872 px in all, with the rows idle,
running right, running left, waving, jumping, failed, waiting, running and review. It is rendered
from the rig, never drawn by hand, licensed like every asset, served from the gateway's existing
`/sprites` path, and checked in CI against a fresh render. It returns only after the status MVP
ships and something real needs the export. Then the sheet holds all four families, documents which
pose and family fills each row, records the rig, the art and the commit it was rendered from, and
has a measured size budget.

[#3887]: https://github.com/PersonalClaw/PersonalClaw/issues/3887
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3900]: https://github.com/PersonalClaw/PersonalClaw/issues/3900
[#3903]: https://github.com/PersonalClaw/PersonalClaw/issues/3903
