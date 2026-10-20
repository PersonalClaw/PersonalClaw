import { useEffect, useState } from 'react'
import { Hourglass } from 'lucide-react'
import { modelIdOf } from '../lib/modelRef'
import { useModelWaits, type ShownWait } from '../lib/useModelWaits'
import { Button } from './Button'

/** The waits of one place, kept current: the chat named `session`, or (`session=""`) the pages,
 *  whose requests wait in no chat. */
export function ModelWaits({ session, floating = false, className }: {
  session: string
  floating?: boolean
  className?: string
}) {
  const { waits, moveOn } = useModelWaits()
  const mine = waits.filter((w) => w.session === session)
  return (
    <ModelWaitNotice waits={mine} onMoveOn={(id) => { void moveOn(id) }} floating={floating}
      className={className} />
  )
}

/** Why a request you are waiting for has not started: the local model it needs is busy, with
 *  what, and what happens next — its next model is asked when the countdown ends, or now if you
 *  choose; with no other model it runs once the model is free.
 *
 *  Renders nothing for no waits. `role="status"`, so the reason is announced once, politely. */
export function ModelWaitNotice({ waits, onMoveOn, floating = false, className }: {
  waits: ShownWait[]
  onMoveOn: (id: string) => void
  /** Over the page (the shell's notice), on glass like a toast; otherwise a row in the flow. */
  floating?: boolean
  className?: string
}) {
  const now = useSecondTicker(waits.some((w) => w.movesOnAt !== null))
  if (waits.length === 0) return null
  return (
    <div role="status" className={['flex flex-col gap-s', className].filter(Boolean).join(' ')}>
      {waits.map((w) => <WaitRow key={w.id} wait={w} now={now} onMoveOn={onMoveOn} floating={floating} />)}
    </div>
  )
}

/** The sentence a wait reads as, and the one after it: what happens next. Exported for the copy
 *  tests, so the words a person reads are pinned where they are made. */
export function waitSentences(w: ShownWait, now: number): { why: string; next: string } {
  const model = modelIdOf(w.model)
  const why = `${w.step} is waiting for ${model}, the local model: it is busy with ${w.busy_with}.`
  if (!w.next) {
    return {
      why,
      next: 'It starts as soon as that is done. A second model for this use in Settings → Models '
        + 'would take over instead of waiting.',
    }
  }
  const nextModel = modelIdOf(w.next)
  const left = w.movesOnAt === null ? null : Math.ceil((w.movesOnAt - now) / 1000)
  if (left === null) return { why, next: `It moves on to ${nextModel} if this takes too long.` }
  if (left <= 0) return { why, next: `Asking ${nextModel} instead…` }
  return { why, next: `It asks ${nextModel} instead in ${left} s.` }
}

function WaitRow({ wait, now, onMoveOn, floating }: {
  wait: ShownWait
  now: number
  onMoveOn: (id: string) => void
  floating: boolean
}) {
  const [asked, setAsked] = useState(false)
  const { why, next } = waitSentences(wait, now)
  return (
    <div className={`flex items-start gap-s rounded-lg px-m py-s ${floating ? 'glass pointer-events-auto' : 'bg-surface-low'}`}>
      <Hourglass size={16} aria-hidden className="shrink-0 text-on-surface-low" />
      <div className="flex min-w-0 flex-1 flex-col gap-xs">
        <p data-type="body-s" className="text-on-surface">{why}</p>
        <p data-type="body-s" className="text-on-surface-low">{next}</p>
      </div>
      {wait.next && (
        <Button variant="secondary" size="xs" className="shrink-0" loading={asked}
          onClick={() => { setAsked(true); onMoveOn(wait.id) }}>
          {`Ask ${modelIdOf(wait.next)} now`}
        </Button>
      )}
    </div>
  )
}

/** `Date.now()`, again every second while `on`: what a countdown is read against. */
function useSecondTicker(on: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!on) return
    setNow(Date.now())
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [on])
  return now
}
