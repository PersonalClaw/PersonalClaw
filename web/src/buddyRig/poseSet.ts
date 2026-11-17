/** The buddy's pose set (#3830), as one table: every pose `docs/design/buddy/character.md` names
 *  under "The poses", and the tray's and the dock's stills, each the agent orb's halftone drawing
 *  for its state (`ui/buddy/buddyHalftone.ts`), moved the way its pose moves the whole orb.
 *
 *  A pose has a still, what it shows under reduced motion, and, where it moves, a motion frame: the
 *  instant its motion is drawn at. `poseSources.ts` draws each frame as an SVG source in
 *  `docs/design/buddy/poses/`, and the contact sheets beside them. The amplitudes are the default
 *  dials', so a source never depends on one owner's settings. */

import { BUDDY_LINES, type LineParts } from '../lib/buddy/copy'
import { SYSTEM_STATE_FAMILIES, WORK_PHASE_LOOKS, phaseLabel, type PhaseFamily } from '../lib/workPhase'
import { expr } from '../design/motion'
import type { OrbScene } from '../lib/orbState'
import type { Weights } from '../ui/orb/halftone'
import { buddyMoves, buddySceneOf, type BuddyState } from '../ui/buddy/buddyHalftone'

export type { BuddyState }

/** One frame of a pose: the halftone state the orb draws, and what the whole orb does. */
export interface Frame {
  /** The agent orb's state for what the buddy shows (`buddySceneOf`), drawn as the agent orb draws it. */
  scene: OrbScene
  /** A frame caught mid-change: the weights of the change, in place of `scene` alone. */
  weights?: Weights
  /** Whether the dots are drawn at an instant of their motion rather than the still one. */
  moving: boolean
  /** The whole orb, about its centre: its rise (negative) or fall and its move to one side, as
   *  shares of its size, its scale, one factor for both axes, so it never squashes, and its
   *  brightness, 1 as drawn: a dozing orb is dimmed. */
  lift: number
  sway: number
  swell: number
  dim: number
}

export interface PoseSpec {
  /** The source's name: `<family>-<phase>`, `card-<family>`, `presence-<name>`, `answer-<name>`. */
  id: string
  /** The words beside the orb on the contact sheet: the state's own line. */
  words: string
  /** The size the source is drawn at: the room its surface gives the orb. */
  room: number
  /** What moves, in the motion guide's words, or what holds still. */
  motion: string
  still: Frame
  /** The instant its motion is drawn at, for a pose that moves. */
  key?: Frame
}

const text = (parts: LineParts): string => parts.map((p) => p.text).join('')

/** The state's own words: the phase's label, or the system state's line. */
export function wordsOf(state: BuddyState): string {
  if (state === 'restarting') return text(BUDDY_LINES.restarting())
  if (state === 'offline') return text(BUDDY_LINES.offline(null))
  return phaseLabel(state)
}

/** Whether a state's dots move: as the agent orb's state for it does. */
export const movesOf = buddyMoves

/** The family a state reads in, from the phase contract. */
export function familyOf(state: BuddyState): PhaseFamily {
  return state === 'restarting' || state === 'offline' ? SYSTEM_STATE_FAMILIES[state] : WORK_PHASE_LOOKS[state].family
}

/** How far a dozing orb, a sleeping one and a waking one are dimmed. Offline dims itself: its
 *  halftone state draws every other dot, faint. */
export const DIMS = { dozing: 0.6, sleeping: 0.4, waking: 0.8 } as const

/** A state's still: the agent orb's halftone state for it, the whole orb at rest. */
export function stillOf(state: BuddyState): Frame {
  return { scene: buddySceneOf(state), moving: false, lift: 0, sway: 0, swell: 1, dim: 1 }
}

const moving = (frame: Frame): Frame => ({ ...frame, moving: true })

/** The phases each family's poses are drawn for, in the brief's table's order. */
export const FAMILY_STATES: Record<PhaseFamily, readonly BuddyState[]> = {
  quiet: ['done', 'idle'],
  active: ['queued', 'thinking', 'searching', 'tool', 'planning', 'delegating', 'streaming', 'listening', 'restarting'],
  waiting: ['approval', 'input'],
  blocked: ['error', 'offline'],
}
export const FAMILIES: readonly PhaseFamily[] = ['quiet', 'active', 'waiting', 'blocked']

/** The size the record measures at and the focused card shows the orb at. */
const CARD = 48
/** The room each presence gives the orb (`docs/design/buddy/surfaces.md`, "The room budget"). */
const PEEK = 22
const COMPACT = 22
const FLOATING = 56

const familyPose = (state: BuddyState): PoseSpec => {
  const still = stillOf(state)
  const family = familyOf(state)
  return movesOf(state)
    ? { id: `${family}-${state}`, words: wordsOf(state), room: CARD, motion: `the dots move as the agent orb's ${still.scene} state does`, still, key: moving(still) }
    : { id: `${family}-${state}`, words: wordsOf(state), room: CARD, motion: 'holds still', still }
}

/** The focused card's orb for each family: the family's own pose at the card's size, for the
 *  phase the card most often shows it in. */
export const CARD_STATES: Record<PhaseFamily, BuddyState> = { quiet: 'done', active: 'tool', waiting: 'approval', blocked: 'error' }

/** The pose under an alert, which an answer goes back to: the work running. */
const UNDER = 'tool'

function rest(): PoseSpec[] {
  const working = stillOf(UNDER)
  const asleep = stillOf('idle')
  const alert = stillOf('approval')
  // A yes closes the alert with done's check; an answer elsewhere is caught as the orb goes back
  // from the alert to the work under it.
  const yes: Frame = stillOf('done')
  const elsewhere: Frame = { ...alert, weights: { approval: 0.45, [working.scene]: 0.55 } }
  return [
    ...FAMILIES.map((family): PoseSpec => {
      const pose = familyPose(CARD_STATES[family])
      return { ...pose, id: `card-${family}` }
    }),
    { id: 'presence-peek', words: wordsOf(UNDER), room: PEEK, motion: 'slides down from the top edge until it clears it', still: working, key: { ...moving(working), lift: -0.55 } },
    { id: 'presence-compact', words: wordsOf(UNDER), room: COMPACT, motion: "the dots move as the agent orb's running state does", still: working, key: moving(working) },
    { id: 'presence-expanded', words: wordsOf(UNDER), room: CARD, motion: "the dots move as the agent orb's running state does", still: working, key: moving(working) },
    { id: 'presence-floating', words: wordsOf(UNDER), room: FLOATING, motion: "the dots move as the agent orb's running state does", still: working, key: moving(working) },
    { id: 'presence-dozing', words: wordsOf(UNDER), room: FLOATING, motion: 'holds still, dimmed', still: { ...working, dim: DIMS.dozing } },
    { id: 'presence-sleeping', words: wordsOf('idle'), room: FLOATING, motion: 'holds still, dimmed', still: { ...asleep, dim: DIMS.sleeping } },
    { id: 'presence-waking', words: wordsOf(UNDER), room: FLOATING, motion: 'brightens back into its pose as its dots move again', still: working, key: { ...moving(working), dim: DIMS.waking } },
    { id: 'answer-yes-nod', words: `${wordsOf('approval')}: yes`, room: CARD, motion: 'dips once, with done\'s check', still: yes, key: { ...yes, lift: expr(0.08) } },
    { id: 'answer-no-shake', words: `${wordsOf('approval')}: no`, room: CARD, motion: 'shakes once side to side', still: working, key: { ...alert, sway: expr(0.08) } },
    { id: 'answer-answered-elsewhere', words: text(BUDDY_LINES.answeredElsewhere()), room: CARD, motion: 'the alert\'s plate folds away as the orb goes back to the work under it', still: working, key: elsewhere },
  ]
}

/** Every pose of the v0.3.0 set, in the brief's order: the four families, the focused card's orb,
 *  presence and the answers. */
export function poseSet(): PoseSpec[] {
  return [...FAMILIES.flatMap((family) => FAMILY_STATES[family].map(familyPose)), ...rest()]
}

/** The tray's and the dock's stills (`docs/design/agent-orb.md`, "Rows and the tray"): idle is
 *  ready, working is running a command (the orb, a ">" and its output: in a template image, one ink,
 *  the thinking sphere is ready's very shape, and this one is not), needs-you needs your OK, error
 *  the error mark, listening the recording dot and offline the offline mark. Working as running was
 *  decided on the maintainer's delegation on 2026-11-17 ("Decide yourself."), over the earlier mapping's
 *  thinking, which in one ink is ready's sphere. Thinking would be this one entry set to
 *  `'thinking'` and the stills redrawn. */
export const TRAY_STATES = { idle: 'idle', working: 'tool', 'needs-you': 'approval', error: 'error', listening: 'listening', offline: 'offline' } as const satisfies Record<string, BuddyState>
export type TrayStill = keyof typeof TRAY_STATES
export const TRAY_SIZES = [16, 18, 22] as const
