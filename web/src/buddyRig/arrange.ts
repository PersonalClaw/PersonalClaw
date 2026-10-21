/** The parameters both rig candidates give the agent orb's drawing (#3887): its dots' density and
 *  arrangement, applied to the frame the agent orb's own engine draws for the phase.
 *
 *  The orb never squashes, stretches or turns into another shape: its dots gather and spread,
 *  crowd toward the lower edge and swirl about the centre, and the whole orb lifts or sinks
 *  (`docs/design/buddy/character.md`). Density goes through the engine's own count scaler, the one
 *  `thinking-orbs`' `dots` prop uses, so a sparse orb is the same drawing with fewer dots rather
 *  than the full one with dots hidden. */

import { MODE_FRAMES, STATE_TO_MODE, scaleCounts, type ModeFrame, type ModeKey, type OrbFrame, type OrbState } from 'thinking-orbs'
import { STILL_AT } from '../ui/orbDots'
import type { Pose } from './poses'

/** What the frame needs of a pose: everything but the wash, which is not the dots'. */
export type Arrangement = Omit<Pose, 'wash'>

/** The agent orb's drawing as its engine draws it. */
export const IDENTITY: Arrangement = { density: 1, spread: 1, sink: 0, lift: 0, swirl: 0 }

/** Where an error's dots settle: just above the orb's lower edge, as a share of its size. */
const SINK_TO = 0.9
/** How much of the way there they go at a full droop. */
const SINK_SHARE = 0.45

/** `frame` with its dots moved by `a` (all but density, which is the opts'). Returns `frame` itself
 *  when nothing moves, so the identity arrangement is the agent orb's drawing exactly. */
export function arrangeFrame(frame: OrbFrame, size: number, a: Arrangement): OrbFrame {
  const turn = a.swirl % 1
  if (a.spread === 1 && a.sink === 0 && a.lift === 0 && turn === 0) return frame
  const c = size / 2
  const cos = Math.cos(turn * 2 * Math.PI)
  const sin = Math.sin(turn * 2 * Math.PI)
  const floor = size * SINK_TO
  const dy = a.lift * size
  const dots = frame.dots.map((d) => {
    const ox = (d.x - c) * a.spread
    const oy = (d.y - c) * a.spread
    const x = c + ox * cos - oy * sin
    let y = c + ox * sin + oy * cos
    y += Math.max(0, floor - y) * SINK_SHARE * a.sink
    return { ...d, x, y: y + dy }
  })
  return { dots, lines: frame.lines }
}

/** The agent orb's drawing for `drawing`, its density and arrangement read from `read` at every
 *  frame. `still` holds it at the instant the agent orb paints its still frames. `lift` says whether
 *  the whole orb's rise and fall are drawn in the frame (the candidate that draws everything in
 *  place) or left to a layer around it (the one that moves the whole orb in its own layer). */
export function poseFrame(mode: ModeKey, read: () => Arrangement, opts: { still: boolean; lift: boolean }): ModeFrame {
  const draw = MODE_FRAMES[mode]
  return (size, t, modeOpts) => {
    const a = read()
    const scaled = a.density === 1 ? modeOpts : scaleCounts(modeOpts, Math.max(0.1, a.density))
    return arrangeFrame(draw(size, opts.still ? STILL_AT : t, scaled), size, opts.lift ? a : { ...a, lift: 0 })
  }
}

/** The engine's mode for a drawing. */
export const modeOf = (drawing: OrbState): ModeKey => STATE_TO_MODE[drawing]
