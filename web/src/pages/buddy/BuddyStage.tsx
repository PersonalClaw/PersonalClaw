/** The playground's stage (#3892): the buddy as each surface holds it, drawn from the state
 *  machine's view (`viewOf`), with the lists it reads beside it.
 *
 *  A screen's top edge holds the island: the peek's orb, the compact line with its count of other
 *  work, or the expanded card. Undocked, the floating orb sits on the screen with its bubble or its
 *  card. Beside the screen: Waiting on You in the waiting list's order, Running Now in start order,
 *  and the focused work. The card is the real expanded card (`ui/BuddyCard.tsx`, #3893), drawn from
 *  the machine by `stageCard.ts`, and its controls send the machine its answer, as the presence will.
 *  The screen's width is the compact width being played (320, 390, 412 px) or the page's. */

import type { ReactNode } from 'react'
import { BuddyCard } from '../../ui/BuddyCard'
import { BuddyOrb } from '../../ui/BuddyOrb'
import { Eyebrow } from '../../ui/Eyebrow'
import { focusedWork, viewOf, type BuddyState } from '../../lib/buddy/machine'
import { BuddyPresence, orbOf } from './BuddyPresence'
import type { StepWindows } from '../../lib/buddy/steps'
import { phaseLabel } from '../../lib/workPhase'
import { cardPropsOf, type StageAnswer } from './stageCard'

/** The widths the stage plays: the three compact widths and the page's own. */
export const STAGE_WIDTHS = ['320', '390', '412', 'full'] as const
export type StageWidth = (typeof STAGE_WIDTHS)[number]

const ALERT_WORDS = { approval: 'Approval', input: 'Question', error: 'Error' } as const

function Section({ title, children, count }: { title: string; children: ReactNode; count?: number }) {
  return (
    <section role="region" aria-label={title} className="flex min-w-0 flex-col gap-s">
      <Eyebrow as="h3">{title}{count !== undefined ? ` · ${count}` : ''}</Eyebrow>
      {children}
    </section>
  )
}

interface StageProps {
  machine: BuddyState
  width: StageWidth
  /** Each piece of work's step window, folded from the playground's inputs. */
  steps: StepWindows
  /** An alert's control was used: the playground sends the machine its answer. */
  onAnswer: (answer: StageAnswer) => void
}

/** The focused orb's shared-element id: the compact island's orb and the card's are one object. */
const ORB_ID = 'buddy-playground-orb'

/** The expanded card, drawn from the machine. */
function Card({ machine, steps, onAnswer }: Pick<StageProps, 'machine' | 'steps' | 'onAnswer'>) {
  const truth = viewOf(machine).truth!
  return <BuddyCard {...cardPropsOf(machine, steps, onAnswer)} state={orbOf(truth, machine)} orbId={ORB_ID} />
}

/** The screen: the buddy as it shows, on a frame the width being played. */
function Screen({ machine, width, steps, onAnswer }: StageProps) {
  const view = viewOf(machine)
  return (
    <div
      data-stage-screen="" data-room={view.room ?? 'none'}
      className="relative flex min-h-64 w-full min-w-0 flex-col items-center overflow-hidden rounded-xl border border-outline-variant bg-surface-low p-m"
      style={width === 'full' ? undefined : { maxWidth: `${Number(width) / 16}rem` }}
    >
      {!view.shows || !view.truth ? (
        <p data-type="body-s" className="m-auto text-on-surface-var">
          {machine.enabled ? 'Nothing shows: the buddy is hidden.' : 'Nothing shows: Buddy status is off.'}
        </p>
      ) : (
        <BuddyPresence machine={machine} orbId={ORB_ID} card={<Card machine={machine} steps={steps} onAnswer={onAnswer} />} />
      )}
    </div>
  )
}

/** The stage: the screen, then Waiting on You, Running Now and the focused work. */
export function BuddyStage({ machine, width, steps, onAnswer }: StageProps) {
  const view = viewOf(machine)
  const focus = focusedWork(machine)
  return (
    <div data-stage="" data-situation={view.situation} data-family={view.family ?? 'none'} className="flex min-w-0 flex-col gap-l">
      <Screen machine={machine} width={width} steps={steps} onAnswer={onAnswer} />
      <p data-type="body-s" className="text-on-surface-var">
        {view.situation === 'hidden' ? 'Hidden' : `Showing ${view.situation}`}
        {view.family ? `, in the ${view.family} family` : ''}
        {machine.quietHours ? ', in quiet hours' : ''}
        {view.offlineMark ? ', with the offline mark' : ''}.
      </p>
      <div className="grid min-w-0 gap-l md:grid-cols-3">
        <Section title="Waiting on you" count={machine.queue.length}>
          {machine.queue.length ? (
            <ul className="flex flex-col gap-xs">
              {machine.queue.map((a) => (
                <li key={a.id} data-type="body-s" className="text-on-surface">
                  {ALERT_WORDS[a.kind]} for {a.work}: {a.brief}{a.id === machine.shown ? ' (showing)' : ''}
                </li>
              ))}
            </ul>
          ) : <p data-type="body-s" className="text-on-surface-var">Nothing is waiting on you.</p>}
        </Section>
        <Section title="Running now" count={machine.live.length}>
          {machine.live.length ? (
            <ul className="flex flex-col gap-xs">
              {machine.live.map((w) => (
                <li key={w.id} className="min-w-0">
                  <BuddyOrb state={w.phase} line={`${w.name}: ${phaseLabel(w.phase)}${w.id === focus?.id ? ' (in focus)' : ''}`} size={18} />
                </li>
              ))}
            </ul>
          ) : <p data-type="body-s" className="text-on-surface-var">Nothing is running.</p>}
        </Section>
        <Section title="Focused work">
          {focus ? (
            <p data-type="body-s" className="text-on-surface">{focus.name}: {phaseLabel(focus.phase)}{focus.line ? `, ${focus.line}` : ''}</p>
          ) : <p data-type="body-s" className="text-on-surface-var">No work is in focus.</p>}
        </Section>
      </div>
    </div>
  )
}
