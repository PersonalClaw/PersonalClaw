/** The buddy's expanded card (#3893, `docs/design/buddy/surfaces.md#the-expanded-card-top-to-bottom`).
 *
 *  Top to bottom: the focused work (its orb at 48, its name, its state in the machine's words and
 *  its exact phase), its run as a three-line step window (`lib/buddy/steps.ts`), the alert when one
 *  shows, every other piece of live work in sight, and Open in PersonalClaw. Nothing in it sends,
 *  and it adds no answer of its own. It has no drop target and no ask field in v0.3.0.
 *
 *  - **An approval** is the app's approval prompt, unchanged: the waiting-list row's own fields
 *    (`approvalRowFields`, shared with the phone companion) and the choices the host passes, built
 *    on the app's existing answer path (#3832). The card wires no Allow or Deny itself. A question
 *    is the chat's question card, and an error has Open and Dismiss; those two raise `onAnswer`.
 *  - **In sight** is the running list's rows (`GET /api/running`, read the way Mission Control reads
 *    it): each row's agent orb, its kind's glyph, its one line, its exact phase, its last update and
 *    the route that opens it. The list's own states show as such: reconnecting, could not be read,
 *    interrupted by a restart, just finished, and many (the first `IN_SIGHT_SHOWN`, then all).
 *  - **When the focused work finishes,** its step window goes and the work in sight takes its room.
 *  - **No timed loss of content:** `onHold` says while the pointer or the keyboard focus is on the
 *    card, so the presence pauses its fold, and the countdown line says how to keep it.
 *
 *  At 320, 390 and 412 px every section stacks with nothing clipped, and every control is a 44 px
 *  target there. The focused orb is a shared element (`Morph`) with the island's compact orb, so the
 *  room change moves it, and the sections enter on the app's region stagger; both are instant under
 *  reduced motion. */

import { useId, useState, type ReactNode } from 'react'
import { Check, CircleDot, ListChecks, MessageSquare, Repeat, SquareCheckBig, Users, Webhook, Workflow, Wrench, Zap, type LucideIcon } from 'lucide-react'
import type { PendingApproval, WorkKind } from '../lib/api'
import type { Work } from '../lib/buddy/inputs'
import { lineOf } from '../lib/buddy/lines'
import { linesOf, trailOf, type StepKind, type StepWindow } from '../lib/buddy/steps'
import { clipWords } from '../lib/clipWords'
import { clockTime } from '../lib/epoch'
import { rowSubject } from '../lib/rowSubject'
import { INTERRUPTED_LINE, KIND_WORD } from '../lib/runningRows'
import { phaseLabel } from '../lib/workPhase'
import { approvalRowFields } from '../pages/chat/approvalRow'
import type { QuestionSegment } from '../pages/chat/chatTypes'
import { QuestionCard, type QuestionReply } from '../pages/chat/QuestionCard'
import { AgentOrb } from './AgentOrb'
import { ApprovalPrompt, type ApprovalChoice } from './ApprovalPrompt'
import { BuddyOrb } from './BuddyOrb'
import { Button } from './Button'
import { Meter } from './Meter'
import { EntranceGroup, EntranceRegion } from './motion'
import { Morph } from './motion/Morph'
import { TextLink } from './TextLink'
import type { BuddyState as OrbState } from './buddy/orbPoses'

/** One piece of live work as the card shows it: the machine's work, with the running list row's
 *  kind, last update and route beside it. */
export interface CardWork extends Work {
  kind: WorkKind
  /** When its phase or its line last changed (the row's `since`). */
  since: string | null
  /** The in-app route that opens it, or '' when nothing does. */
  route: string
}

/** What waits on the owner, as the card shows it. An approval's choices are the host's, on the
 *  app's existing answer path: the card renders them as they are. */
export type CardAlert =
  | { kind: 'approval'; row: PendingApproval; choices: readonly ApprovalChoice[] }
  | { kind: 'input'; question: QuestionSegment }
  | { kind: 'error'; work: string; reason: string; route: string }

/** What a question's or an error's control raises: the presence turns it into the machine's input.
 *  An approval's choices answer through their own `onClick`. */
export type CardAnswer = { kind: 'reply'; reply: QuestionReply } | { kind: 'dismiss' }

/** The running list's own state, beside its rows. */
export type CardList = 'live' | 'reconnecting' | 'failed'

/** The card's room (copy.md): its line, and an in-sight row's. */
export const CARD_LINE = 72
export const IN_SIGHT_LINE = 48
/** In-sight rows shown before "Show all": the card is 360 px tall at most. */
export const IN_SIGHT_SHOWN = 6

const KIND_GLYPH: Record<WorkKind, LucideIcon> = {
  chat: MessageSquare, loop: Repeat, workflow: Workflow, subagent: Users, trigger: Zap, webhook: Webhook,
}
const STEP_GLYPH: Record<StepKind, LucideIcon> = { plan: ListChecks, step: CircleDot, tool: Wrench, result: SquareCheckBig }

/** A 44 px target at the compact widths (320 to 768 px), the kit's own size above them. */
const TARGET = 'max-lg:inline-flex max-lg:min-h-11 max-lg:min-w-11'
/** The same for the controls of the prompts the card holds (the approval prompt's arguments, the
 *  question card's options, field and buttons), which are dense where they first live. */
const TARGETS_WITHIN = 'max-lg:[&_button]:min-h-11 max-lg:[&_input]:min-h-11 max-lg:[&_pre]:min-h-11'

const lineOfWork = (w: CardWork, surface: 'expandedCard' | 'inSightRow') =>
  lineOf({ kind: 'phase', phase: w.phase, work: w }, surface)

function Glyph({ kind }: { kind: WorkKind }) {
  const Icon = KIND_GLYPH[kind] ?? CircleDot
  return <Icon size={14} aria-hidden className="shrink-0 text-on-surface-var" data-kind-glyph={kind} />
}

/** The phase and last update, small: the row's exact phase in the phase table's words, or how it
 *  ended, and what the restart did to work it cut off. */
function Facts({ w, ended, cut }: { w: CardWork; ended?: string; cut?: boolean }) {
  const at = cut ? '' : clockTime(w.since)
  return (
    <p data-type="body-s" className="min-w-0 text-on-surface-low">
      <span data-work-phase="">{ended ?? phaseLabel(w.phase)}</span>
      {cut ? <span>{` · ${INTERRUPTED_LINE}`}</span> : null}
      {at ? <span data-work-updated="">{` · ${ended ? 'ended' : 'updated'} ${at}`}</span> : null}
    </p>
  )
}

/** One row in sight: the orb, the kind's glyph, the one line, its facts and the route that opens
 *  it, as Mission Control's Working lane says a row. An ended row's line is its name: the facts say
 *  how it ended. */
function Row({ w, ended, cut }: { w: CardWork; ended?: string; cut?: boolean }) {
  const said = ended ? clipWords(w.name, IN_SIGHT_LINE) : lineOfWork(w, 'inSightRow')
  const name = `Open ${rowSubject([w.name, KIND_WORD[w.kind] ?? w.kind])}`
  return (
    <li data-in-sight={w.id} data-kind={w.kind} className="flex min-w-0 items-start gap-s">
      <span className="flex shrink-0 items-center gap-xs pt-0.5"><AgentOrb phase={w.phase} size={20} /><Glyph kind={w.kind} /></span>
      <div className="flex min-w-0 flex-1 flex-col">
        <span data-type="body-s" data-work-line="" className="min-w-0 truncate text-on-surface">{said}</span>
        <div className="flex min-w-0 flex-wrap items-center gap-x-s">
          <Facts w={w} ended={ended} cut={cut} />
          {w.route ? <TextLink href={w.route} ink="emphasis" size="sm" aria-label={name} className={`${TARGET} items-center`}>Open</TextLink> : null}
        </div>
      </div>
    </li>
  )
}

function StepLines({ steps }: { steps: StepWindow }) {
  const lines = linesOf(steps)
  const trail = trailOf(steps.done)
  if (!lines.length) return null
  return (
    <div data-step-window="" className="flex min-w-0 flex-col gap-xs">
      {trail.marks ? (
        <p data-step-trail="" data-type="label-s" className="flex items-center gap-0.5 text-on-surface-low">
          <span className="sr-only">{steps.done === 1 ? '1 step done' : `${steps.done} steps done`}</span>
          {Array.from({ length: trail.marks }, (_, i) => <Check key={i} size={10} aria-hidden data-step-mark="" />)}
          {trail.more ? <span aria-hidden className="ml-xs">+{trail.more}</span> : null}
        </p>
      ) : null}
      <ol aria-label="The run" className="flex min-w-0 flex-col gap-xs">
        {lines.map(({ place, step }) => {
          const Icon = place === 'before' ? Check : STEP_GLYPH[step.kind]
          const current = place === 'current'
          const label = clipWords(step.label, CARD_LINE)
          return (
            <li
              key={step.id} data-step-place={place} aria-current={current ? 'step' : undefined}
              data-type="body-s" className={`flex min-w-0 items-center gap-s ${current ? 'text-on-surface' : 'text-on-surface-low'}`}
            >
              <Icon size={14} aria-hidden className="shrink-0" />
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className={`min-w-0 truncate ${current && step.progress === undefined ? 'text-shimmer' : ''}`}>
                  {place === 'before' ? <span className="sr-only">Done: </span> : place === 'next' ? <span className="sr-only">Next: </span> : null}
                  {label}
                  {current && step.progress !== undefined ? <span className="text-on-surface-low">{` · ${Math.round(step.progress * 100)}%`}</span> : null}
                </span>
                {current && step.progress !== undefined ? (
                  <Meter label={`${label}, progress`} pct={step.progress * 100} size="thin" tone="var(--color-orb-alive)" />
                ) : null}
                {step.helpers?.length ? (
                  <span data-helpers="" className="flex flex-wrap gap-xs" aria-label={`${step.helpers.length} helpers`} role="group">
                    {step.helpers.map((phase, i) => <AgentOrb key={i} phase={phase} size={20} />)}
                  </span>
                ) : null}
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function AlertArea({ alert, waiting, onAnswer }: { alert: CardAlert; waiting: number; onAnswer: (a: CardAnswer) => void }) {
  let body: ReactNode
  if (alert.kind === 'approval') {
    const ap = alert.row
    body = (
      <div data-alert-brief="" className={`min-w-0 ${TARGETS_WITHIN}`}>
        <ApprovalPrompt density="roomy" {...approvalRowFields(ap)} choices={[...alert.choices]} />
      </div>
    )
  } else if (alert.kind === 'input') {
    body = (
      <div className={TARGETS_WITHIN}>
        <QuestionCard seg={alert.question} onAnswer={async (_id, reply) => { onAnswer({ kind: 'reply', reply }); return true }} />
      </div>
    )
  } else {
    body = (
      <div role="group" aria-label={`${alert.work} stopped`} className="flex min-w-0 flex-col gap-s">
        <p data-type="body-s" className="min-w-0 text-on-surface">{clipWords(`${alert.work} stopped: ${alert.reason}`, CARD_LINE)}</p>
        <div className="flex flex-wrap items-center gap-s">
          {alert.route ? <TextLink href={alert.route} ink="emphasis" size="sm" className={TARGET} aria-label={`Open ${alert.work}`}>Open</TextLink> : null}
          <Button variant="secondary" size="sm" className={TARGET} onClick={() => onAnswer({ kind: 'dismiss' })}>Dismiss</Button>
        </div>
      </div>
    )
  }
  return (
    <div data-card-alert={alert.kind} className="flex min-w-0 flex-col gap-s">
      {body}
      {waiting > 0 ? <p data-type="label-s" className="text-on-surface-low">+{waiting} waiting</p> : null}
    </div>
  )
}

function InSight({ rows, finished, interrupted, list, roomy }: {
  rows: readonly CardWork[]
  finished: readonly CardWork[]
  interrupted: readonly CardWork[]
  list: CardList
  /** The focused work finished: the rows take its room and show whole. */
  roomy: boolean
}) {
  const headingId = useId()
  const [all, setAll] = useState(false)
  const cap = roomy ? IN_SIGHT_SHOWN * 2 : IN_SIGHT_SHOWN
  const shown = all ? rows : rows.slice(0, cap)
  // An ending the gateway kept wins over the restart's word for the same work.
  const ended = new Set(finished.map((w) => w.id))
  const cut = interrupted.filter((w) => !ended.has(w.id))
  return (
    <section aria-labelledby={headingId} data-in-sight-list={list} className="flex min-w-0 flex-col gap-s">
      <h4 id={headingId} data-type="label-s" className="text-on-surface-var">In sight · {rows.length}</h4>
      {list === 'reconnecting' ? (
        <p role="status" data-type="body-s" className="text-on-surface-low">Reconnecting… what shows here may be out of date.</p>
      ) : null}
      {list === 'failed' && !rows.length ? (
        <p data-type="body-s" className="text-on-surface-low">Could not read what is running.</p>
      ) : !rows.length ? (
        <p data-type="body-s" className="text-on-surface-low">Nothing else is running.</p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-s">{shown.map((w) => <Row key={w.id} w={w} />)}</ul>
      )}
      {rows.length > cap ? (
        <div>
          <Button size="xs" variant="ghost-accent" className={TARGET} ariaExpanded={all} onClick={() => setAll((was) => !was)}>
            {all ? `Show the first ${cap}` : `Show all ${rows.length}`}
          </Button>
        </div>
      ) : null}
      {finished.length + cut.length ? (
        <ul aria-label="Just finished" data-just-finished="" className="flex min-w-0 flex-col gap-s">
          {cut.map((w) => <Row key={`cut:${w.id}`} w={w} ended="Interrupted" cut />)}
          {finished.map((w) => <Row key={w.id} w={w} ended={phaseLabel(w.phase)} />)}
        </ul>
      ) : null}
    </section>
  )
}

export function BuddyCard({
  focus, line, state, steps, alert, waiting = 0, inSight, finished = [], interrupted = [], list = 'live',
  countdown, openHref, orbId, onAnswer, onHold,
}: {
  focus: CardWork | null
  /** The machine's line for what the card is about (`lineOf(truth, 'expandedCard')`). */
  line: string
  /** The focused orb's state; the focused work's phase by default. */
  state?: OrbState
  steps: StepWindow
  alert: CardAlert | null
  /** How many more wait behind the alert that shows. */
  waiting?: number
  inSight: readonly CardWork[]
  /** The running list's last endings: each row's phase is how it ended. */
  finished?: readonly CardWork[]
  /** Work a restart cut off. */
  interrupted?: readonly CardWork[]
  list?: CardList
  /** Seconds before it folds, while the countdown line shows. */
  countdown?: number
  /** Where Open in PersonalClaw goes. */
  openHref: string
  /** The shared element's id, the same on the island's compact orb. */
  orbId?: string
  onAnswer: (answer: CardAnswer) => void
  /** The pointer or the keyboard focus is on the card (true), or has left it (false). */
  onHold?: (held: boolean) => void
}) {
  const headerId = useId()
  const orbState: OrbState = state ?? focus?.phase ?? 'idle'
  const orb = <BuddyOrb state={orbState} line="" size={48} />
  const done = focus?.phase === 'done'
  return (
    <div
      role="group" aria-labelledby={headerId} data-buddy-card="" data-card-done={String(done)}
      className="flex w-full min-w-0 flex-col gap-m rounded-xl border border-outline-variant bg-surface p-l"
      onPointerEnter={() => onHold?.(true)} onPointerLeave={() => onHold?.(false)}
      onFocus={() => onHold?.(true)}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onHold?.(false) }}
    >
      <EntranceGroup className="flex min-w-0 flex-col gap-m">
        <EntranceRegion>
          <header className="flex min-w-0 items-start gap-m">
            {orbId ? <Morph id={orbId} className="shrink-0">{orb}</Morph> : <span className="shrink-0">{orb}</span>}
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <h3 id={headerId} data-type="title-s" className="min-w-0 break-words text-on-surface">
                {focus ? clipWords(focus.name, CARD_LINE) : 'Buddy'}
              </h3>
              <p data-type="body-s" data-card-line="" className="min-w-0 text-on-surface">{line}</p>
              {focus ? <Facts w={focus} /> : null}
            </div>
          </header>
        </EntranceRegion>
        {focus && !done ? <EntranceRegion><StepLines steps={steps} /></EntranceRegion> : null}
        {alert ? <EntranceRegion><AlertArea alert={alert} waiting={waiting} onAnswer={onAnswer} /></EntranceRegion> : null}
        <EntranceRegion>
          <InSight rows={inSight} finished={finished} interrupted={interrupted} list={list} roomy={done} />
        </EntranceRegion>
        <EntranceRegion>
          <footer className="flex min-w-0 flex-col gap-xs">
            <TextLink href={openHref} ink="emphasis" size="sm" className={TARGET}>Open in PersonalClaw</TextLink>
            {countdown ? (
              <p role="status" data-type="body-s" className="text-on-surface-low">
                Folding in {countdown} seconds; pointing at the card or tabbing into it keeps it open.
              </p>
            ) : null}
          </footer>
        </EntranceRegion>
      </EntranceGroup>
    </div>
  )
}
