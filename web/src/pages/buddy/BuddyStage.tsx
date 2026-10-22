/** The playground's stage (#3892): the buddy as each surface holds it, drawn from the state
 *  machine's view (`viewOf`), with the lists it reads beside it.
 *
 *  A screen holds the buddy where it stays (#5216): one orb of one size, and beside it a card on
 *  the side with room, the line while work runs or the full card when it opens. Docked or
 *  floating, nothing the machine says moves the orb. Beside the screen: Waiting on You in the waiting list's order, Running Now in start order,
 *  and the focused work. The card is the real card (`ui/BuddyCard.tsx`, #3893), drawn from
 *  the machine by `stageCard.ts`, and its controls send the machine its answer, as the presence will.
 *  The screen's width is the compact width being played (320, 390, 412 px) or the page's. */

import { useEffect, useState, type ReactNode } from 'react'
import { BuddyCard } from '../../ui/BuddyCard'
import { BuddyOrb } from '../../ui/BuddyOrb'
import { Eyebrow } from '../../ui/Eyebrow'
import { focusedWork, viewOf, type BuddyState } from '../../lib/buddy/machine'
import { BuddyPresence, CARD_HEIGHT, CARD_WIDTH, ORB_SLOT_WIDTH, orbOf } from './BuddyPresence'
import { CARD_GAP, cardPlace, DISPLAY_EDGE } from '../../lib/buddy/cardPlacement'
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

/** The full card, drawn from the machine. */
function Card({ machine, steps, onAnswer }: Pick<StageProps, 'machine' | 'steps' | 'onAnswer'>) {
  const truth = viewOf(machine).truth!
  return <BuddyCard {...cardPropsOf(machine, steps, onAnswer)} state={orbOf(truth, machine)} />
}

/** The screen's height: the orb at the top, and the card's room below it. */
const SCREEN_HEIGHT = 320

/** The screen as laid out: the width its content has, which the card is placed in, and the height
 *  its open card takes, which the screen grows to hold, so the card is never cut off and the orb
 *  never moves. */
function useScreenRoom(): [(el: HTMLDivElement | null) => void, number | null, number] {
  const [width, setWidth] = useState<number | null>(null)
  const [card, setCard] = useState(0)
  const [el, setEl] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!el || typeof ResizeObserver === 'undefined') return
    const read = () => {
      setWidth(el.clientWidth - 2 * SCREEN_PAD)
      const surface = el.querySelector<HTMLElement>('[data-buddy-card-surface]')
      setCard(surface ? surface.offsetHeight : 0)
    }
    read()
    const watch = new ResizeObserver(read)
    watch.observe(el)
    const surfaces = new MutationObserver(() => {
      watch.disconnect()
      watch.observe(el)
      const surface = el.querySelector('[data-buddy-card-surface]')
      if (surface) watch.observe(surface)
      read()
    })
    surfaces.observe(el, { childList: true, subtree: true })
    return () => { watch.disconnect(); surfaces.disconnect() }
  }, [el])
  return [setEl, width, card]
}

/** The screen's padding (`p-m`). */
const SCREEN_PAD = 16

/** The screen: the buddy where it stays, at the top centre (its place until a drag moves it), on a
 *  frame the width being played, with its card on the side that has room in the frame. Nothing the
 *  machine says moves the orb, docked or floating. */
function Screen({ machine, width, steps, onAnswer }: StageProps) {
  const view = viewOf(machine)
  const [frameRef, inner, cardHeight] = useScreenRoom()
  const room = inner ?? (width === 'full' ? CARD_WIDTH + 2 * DISPLAY_EDGE : Number(width) - 2 * SCREEN_PAD)
  const orb = { x: room / 2 - ORB_SLOT_WIDTH / 2, y: 0, width: ORB_SLOT_WIDTH, height: 48 }
  // The side and the width from the card's budget, on a screen that grows below the orb to the
  // card's own height.
  const { side, align, width: cardWidth } = cardPlace(orb, { width: room, height: Infinity }, { width: CARD_WIDTH, height: CARD_HEIGHT })
  const place = { side, align, width: cardWidth }
  const tall = Math.max(SCREEN_HEIGHT, 2 * SCREEN_PAD + 48 + CARD_GAP + cardHeight + SCREEN_PAD)
  return (
    <div
      ref={frameRef}
      data-stage-screen="" data-room={view.room ?? 'none'}
      className="relative flex w-full min-w-0 flex-col items-center overflow-auto rounded-xl border border-outline-variant bg-surface-low p-m"
      style={{ minHeight: tall, ...(width === 'full' ? {} : { maxWidth: `${Number(width) / 16}rem` }) }}
    >
      {!view.shows || !view.truth ? (
        <p data-type="body-s" className="m-auto text-on-surface-var">
          {machine.enabled ? 'Nothing shows: the buddy is hidden.' : 'Nothing shows: Buddy status is off.'}
        </p>
      ) : (
        <BuddyPresence machine={machine} place={place} card={<Card machine={machine} steps={steps} onAnswer={onAnswer} />} />
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
