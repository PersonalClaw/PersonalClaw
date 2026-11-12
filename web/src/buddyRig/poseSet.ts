/** The buddy's v0.3.0 pose set (#3830), as one table: every pose `docs/design/buddy/character.md`
 *  names under "The poses", and the tray's and the dock's stills, each the agent orb's claw ring for
 *  its state, moved the way its pose moves it (`poses.ts`), with the badge and the wash in strokes
 *  (`ui/buddy/buddyRing.ts`).
 *
 *  A pose has a still, what it shows under reduced motion, and, where it moves, a motion frame: the
 *  instant its motion is drawn at. `poseSources.ts` draws each frame as a layered SVG source in
 *  `docs/design/buddy/poses/`, and the contact sheets beside them. The amplitudes are the default
 *  dials', so a source never depends on one owner's settings. */

import { BUDDY_LINES, type LineParts } from '../lib/buddy/copy'
import { SYSTEM_STATE_FAMILIES, WORK_PHASE_LOOKS, phaseLabel, type PhaseFamily, type PhaseTone, type WorkPhase } from '../lib/workPhase'
import { expr } from '../design/motion'
import { MOVES } from '../ui/orb/clawRing'
import { ORB_STATE_OF, type OrbState, type OrbStateName } from '../lib/orbState'
import type { Mark } from '../ui/buddy/buddyRing'
import { poseOf, type RigPhase } from './poses'

/** What the orb shows: a phase of the contract, or one of the system's own states. */
export type BuddyState = WorkPhase | 'restarting' | 'offline'

/** One frame of a pose: the orb's look, what its strokes do, and what the whole orb does. */
export interface Frame {
  /** The agent orb's state the buddy draws (`lib/orbState.ts`): its claw ring's form, in its ink. */
  form: OrbStateName
  ink: 'alive' | 'rest'
  /** The badge's mark, in the tone; the wash rises in the same tone. */
  mark: Mark | null
  tone: PhaseTone
  /** Whether the strokes are drawn at an instant of their motion rather than the still one. */
  moving: boolean
  /** The strokes: how thin quiet draws them, how far from the centre, how far an error sags them,
   *  their turn (`ui/buddy/buddyRing.ts`, `posedForm`). */
  density: number
  spread: number
  sink: number
  swirl: number
  /** 0 to 1: the alert's wash. */
  wash: number
  /** The whole orb, about its centre: its rise (negative) or fall and its move to one side, as
   *  shares of its size, and its scale, one factor for both axes, so it never squashes. */
  lift: number
  sway: number
  swell: number
  /** The strokes' brightness, 1 as drawn: a dozing or offline orb is dimmed. */
  dim: number
  /** How far the badge is shown, 1 in full: it folds toward 0 as an answer elsewhere closes it. */
  badge: number
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

/** The agent orb's state the buddy draws for each phase. It is the agent orb's own
 *  (`lib/orbState.ts`), but for one thing: the buddy never draws the claw mark, which the owner's
 *  choice of 2026-10-18 keeps out of it (`docs/design/buddy/character.md`, "The choice"). Where the
 *  chat's orb closes its strokes into the mark (done, and a failure), the buddy holds ready's open
 *  ring, its badge saying which, with no bloom: its own flourish is done's turn. */
const BUDDY_LOOKS: Readonly<Record<WorkPhase, OrbState>> = Object.freeze(Object.fromEntries(
  (Object.keys(ORB_STATE_OF) as WorkPhase[]).map((phase) => {
    const look = ORB_STATE_OF[phase]
    return [phase, look.state === 'done' ? Object.freeze({ ...look, state: 'ready', bloom: false }) : look]
  }),
) as Record<WorkPhase, OrbState>)

/** The agent orb's state the buddy draws for `state`, one object a state so a change is seen by
 *  identity: a phase's own, with done's and a failure's form held open (`BUDDY_LOOKS`); restarting
 *  is working's; offline is ready's, in the rest ink. */
const OFFLINE: OrbState = Object.freeze({ state: 'ready', ink: 'rest', bloom: false })
export function orbLookOf(state: BuddyState): OrbState {
  if (state === 'restarting') return BUDDY_LOOKS.tool
  if (state === 'offline') return OFFLINE
  return BUDDY_LOOKS[state]
}

/** Whether a state's strokes move: as the agent orb's state for it does (`lib/orbState.ts`), while it
 *  thinks or works. Restarting moves as working does, and offline holds still. */
export function movesOf(state: BuddyState): boolean {
  return MOVES[orbLookOf(state).state]
}

/** The family a state reads in, from the phase contract. */
export function familyOf(state: BuddyState): PhaseFamily {
  return state === 'restarting' || state === 'offline' ? SYSTEM_STATE_FAMILIES[state] : WORK_PHASE_LOOKS[state].family
}

const MARKS: Partial<Record<WorkPhase, Mark>> = {
  listening: 'listening', approval: 'approval', input: 'input', error: 'error', done: 'done',
}
const RIG_PHASES: ReadonlySet<string> = new Set<RigPhase>(['idle', 'thinking', 'tool', 'approval', 'input', 'error', 'done'])

/** How far an offline orb, a dozing one and a sleeping one are dimmed. */
export const DIMS = { offline: 0.55, dozing: 0.6, sleeping: 0.4, waking: 0.8 } as const

/** A state's still: the agent orb's look for it, with the rig's pose for its family, and the badge
 *  the buddy adds. The active family takes the working mark (the microphone while listening);
 *  restarting draws working's orb, and offline is ready's ring, dimmed, with no droop or wash. */
export function stillOf(state: BuddyState): Frame {
  const base = (phase: WorkPhase): Frame => {
    const orb = orbLookOf(phase)
    const look = WORK_PHASE_LOOKS[phase]
    const pose = RIG_PHASES.has(phase) ? poseOf(phase as RigPhase) : poseOf('thinking')
    return {
      form: orb.state, ink: orb.ink, tone: look.tone, moving: false,
      mark: MARKS[phase] ?? (look.family === 'active' ? 'working' : null),
      density: pose.density, spread: pose.spread, sink: pose.sink, swirl: pose.swirl, wash: pose.wash,
      lift: pose.lift, sway: 0, swell: 1, dim: 1, badge: 1,
    }
  }
  if (state === 'restarting') return base('tool')
  // Offline is not the agent failing: ready's ring, dimmed, with the slash, and none of an error's
  // droop or wash.
  if (state === 'offline') return { ...base('error'), form: 'ready', sink: 0, mark: 'offline', tone: 'neutral', wash: 0, dim: DIMS.offline }
  return base(state)
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
    ? { id: `${family}-${state}`, words: wordsOf(state), room: CARD, motion: `the strokes move as the agent orb's ${still.form} state does`, still, key: moving(still) }
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
  const yes: Frame = { ...alert, mark: 'done', tone: 'ok', spread: 1, wash: 0 }
  return [
    ...FAMILIES.map((family): PoseSpec => {
      const pose = familyPose(CARD_STATES[family])
      return { ...pose, id: `card-${family}` }
    }),
    { id: 'presence-peek', words: wordsOf(UNDER), room: PEEK, motion: 'slides down from the top edge until its badge clears it', still: working, key: { ...moving(working), lift: -0.55 } },
    { id: 'presence-compact', words: wordsOf(UNDER), room: COMPACT, motion: "the strokes move as the agent orb's working state does", still: working, key: moving(working) },
    { id: 'presence-expanded', words: wordsOf(UNDER), room: CARD, motion: "the strokes move as the agent orb's working state does", still: working, key: moving(working) },
    { id: 'presence-floating', words: wordsOf(UNDER), room: FLOATING, motion: "the strokes move as the agent orb's working state does", still: working, key: moving(working) },
    { id: 'presence-dozing', words: wordsOf(UNDER), room: FLOATING, motion: 'holds still, dimmed', still: { ...working, dim: DIMS.dozing } },
    { id: 'presence-sleeping', words: wordsOf('idle'), room: FLOATING, motion: 'holds still, dimmed', still: { ...asleep, dim: DIMS.sleeping } },
    { id: 'presence-waking', words: wordsOf(UNDER), room: FLOATING, motion: 'brightens back into its pose as its strokes move again', still: working, key: { ...moving(working), dim: DIMS.waking } },
    { id: 'answer-yes-nod', words: `${wordsOf('approval')}: yes`, room: CARD, motion: 'dips once, with the check badge', still: yes, key: { ...yes, lift: expr(0.08) } },
    { id: 'answer-no-shake', words: `${wordsOf('approval')}: no`, room: CARD, motion: 'shakes once side to side', still: working, key: { ...alert, sway: expr(0.08) } },
    { id: 'answer-answered-elsewhere', words: text(BUDDY_LINES.answeredElsewhere()), room: CARD, motion: 'the badge folds away and the orb goes back to the pose under the alert', still: working, key: { ...alert, badge: 0.45, wash: 0.45 } },
  ]
}

/** Every pose of the v0.3.0 set, in the brief's order: the four families, the focused card's orb,
 *  presence and the answers. */
export function poseSet(): PoseSpec[] {
  return [...FAMILIES.flatMap((family) => FAMILY_STATES[family].map(familyPose)), ...rest()]
}

/** The tray's and the dock's stills, each its family's still (`docs/design/buddy/surfaces.md`). */
export const TRAY_STATES = { idle: 'idle', working: 'tool', 'needs-you': 'approval', error: 'error', listening: 'listening', offline: 'offline' } as const satisfies Record<string, BuddyState>
export type TrayStill = keyof typeof TRAY_STATES
export const TRAY_SIZES = [16, 18, 22] as const
