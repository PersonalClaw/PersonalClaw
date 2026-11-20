import { useEffect, useState } from 'react'
import { TOOL_SCENE_OF_ACT, type OrbScene } from '../../lib/orbState'

/** How long a tool step's state stays drawn at least, from the moment the step began
 *  (`docs/design/agent-orb.md`, "Phase to state"). A read or an edit on PersonalClaw's own runtime is
 *  over in a few milliseconds, well before the morph into its state (about 0.45 s) has arrived, so
 *  without a hold a chat would show thinking, a flicker, then thinking again. */
export const TOOL_STEP_HOLD_MS = 1200

/** The states a tool step is drawn as: reading, editing and running a command. */
const TOOL_STEPS: ReadonlySet<OrbScene> = new Set<OrbScene>(Object.values(TOOL_SCENE_OF_ACT))

/** What a turn does between its steps. Only a return to one of these waits for a held step. */
const BETWEEN_STEPS: ReadonlySet<OrbScene> = new Set<OrbScene>(['thinking', 'planning'])

interface Held {
  /** The state the phase maps to now. */
  asked: OrbScene
  /** The state drawn: `asked`, or a tool step's while it holds. */
  shown: OrbScene
  /** When the last tool step began, by `Date.now()`. */
  began: number
}

/** The state to draw when the phase maps to `asked`. A tool step's state stays drawn until
 *  `TOOL_STEP_HOLD_MS` after the step began while the turn only thinks or plans again; anything
 *  else replaces it at once: the next step, a wait on the owner, the answer, the turn's end, a
 *  stage. A step that ran longer than the hold gives way to thinking at once. The words beside the
 *  orb, and its image's name, say the phase as it is all along. */
export function useHeldToolStep(asked: OrbScene): OrbScene {
  const [held, setHeld] = useState<Held>(() => ({ asked, shown: asked, began: Date.now() }))
  let now = held
  if (held.asked !== asked) {
    // Settled in the render that brings the change, so a state that is not held is drawn in the
    // same frame, never a frame late.
    const at = Date.now()
    const began = TOOL_STEPS.has(asked) ? at : held.began
    const holds = TOOL_STEPS.has(held.shown) && BETWEEN_STEPS.has(asked) && at - began < TOOL_STEP_HOLD_MS
    now = { asked, shown: holds ? held.shown : asked, began }
    setHeld(now)
  }
  useEffect(() => {
    if (now.shown === now.asked) return
    const wait = window.setTimeout(() => setHeld((h) => ({ ...h, shown: h.asked })), TOOL_STEP_HOLD_MS - (Date.now() - now.began))
    return () => window.clearTimeout(wait)
  }, [now])
  return now.shown
}
