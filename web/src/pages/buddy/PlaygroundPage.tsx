/** `#/buddy/playground`: the buddy playground (#3892), where the owner plays the buddy before any
 *  desktop surface ships (the buddy epic's gate two).
 *
 *  It drives the real state machine and the real orb, and mocks only their inputs (`playground.ts`):
 *  every control is one input, the clock fires the machine's timers at their lengths, and the log
 *  shows every input with what the machine asked for. Nothing reaches the gateway: an answer the
 *  machine asks to send is shown in the log and never sent. Three tabs, each an address of its own
 *  (`?tab=`): Play, the scripted day (`scriptedDay.ts`) and the pose sheet the renderer's snapshots
 *  are taken from.
 *
 *  A full-screen route outside the navigation, as `#/companion` is, and in release builds: the
 *  owner can play it on an installed build, and nothing links to it but its address. */

import { useEffect, useRef, useState } from 'react'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { PageTitle } from '../../ui/PageTitle'
import { Segmented } from '../../ui/Segmented'
import { useQueryParam, type RouteProps } from '../../app/useQueryState'
import { situationOf } from '../../lib/buddy/machine'
import type { Effect } from '../../lib/buddy/inputs'
import { BuddyStage, STAGE_WIDTHS, type StageWidth } from './BuddyStage'
import { LookControls, PlaygroundControls, clockOf } from './PlaygroundControls'
import { PoseSheet } from './PoseSheet'
import { advance, fire, startPlayground, use, type LogEntry, type Playground } from './playground'
import { DAY_LENGTH, SCRIPTED_DAY, dayAt } from './scriptedDay'

const TABS = [{ key: 'play', label: 'Play' }, { key: 'day', label: 'A day' }, { key: 'poses', label: 'Poses' }]
const SPEEDS = [{ key: '1', label: '1×' }, { key: '4', label: '4×' }, { key: '16', label: '16×' }]
/** How often a playing day moves its clock, in real ms. */
const TICK = 100

/** An effect in the log's words. An answer is never sent from here. */
function effectWords(e: Effect): string {
  switch (e.kind) {
    case 'timer': return `${e.start ? 'start' : 'cancel'} ${e.name}`
    case 'answer': return `answer, not sent: ${e.choice.kind} on ${e.route}`
    case 'reread': return 're-read the lists'
    case 'beat': return `${e.beat} beat`
    case 'ignored': return `ignored: ${e.event} while ${e.situation}`
  }
}

function Log({ log }: { log: readonly LogEntry[] }) {
  const recent = log.slice(-60).reverse()
  return (
    <section role="region" aria-label="Event log" className="flex min-w-0 flex-col gap-s">
      <Eyebrow as="h3">Event log · {log.length}</Eyebrow>
      <ol className="flex max-h-96 flex-col gap-xs overflow-y-auto" data-event-log="">
        {recent.map((e) => (
          <li key={e.n} data-type="body-s" data-event={e.event ?? ''} className="min-w-0 text-on-surface">
            <span className="text-on-surface-var">{clockOf(e.at)}</span> {e.label}
            {e.event ? ` (${e.event})` : ''}: {e.from}{e.to !== e.from ? ` → ${e.to}` : ''}
            {e.effects.length ? <span className="text-on-surface-var">. {e.effects.map(effectWords).join(', ')}</span> : null}
          </li>
        ))}
      </ol>
    </section>
  )
}

function StageWidthPicker({ width, onWidth }: { width: StageWidth; onWidth: (w: StageWidth) => void }) {
  return (
    <Segmented
      ariaLabel="Screen width" size="sm" value={width} onChange={(w) => onWidth(w as StageWidth)}
      options={STAGE_WIDTHS.map((w) => ({ key: w, label: w === 'full' ? 'Full' : `${w} wide` }))}
    />
  )
}

/** The Play tab: the stage, the controls and the log. */
function Play() {
  const [pg, setPg] = useState<Playground>(startPlayground)
  const [width, setWidth] = useState<StageWidth>('full')
  return (
    <div className="grid min-w-0 gap-xl lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div className="flex min-w-0 flex-col gap-l">
        <StageWidthPicker width={width} onWidth={setWidth} />
        <BuddyStage machine={pg.machine} width={width} />
        <Log log={pg.log} />
      </div>
      <div className="flex min-w-0 flex-col gap-l">
        <PlaygroundControls
          pg={pg}
          onUse={(id) => setPg((p) => use(p, id))}
          onPhase={(phase) => setPg((p) => ({ ...p, phase }))}
          onFire={() => setPg(fire)}
          onAdvance={(ms) => setPg((p) => advance(p, ms))}
        />
        <Button variant="ghost" size="sm" onClick={() => setPg(startPlayground())}>Start again</Button>
        <LookControls />
      </div>
    </div>
  )
}

/** The A day tab: the scripted day on the playground's clock, played at a speed, paused, stepped. */
function Day() {
  const [day, setDay] = useState(() => dayAt(0))
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState('1')
  const [width, setWidth] = useState<StageWidth>('full')
  const ended = day.pg.now >= DAY_LENGTH
  const latest = useRef(day)
  latest.current = day
  useEffect(() => {
    if (!playing) return
    const id = window.setInterval(() => {
      const d = latest.current
      const next = dayAt(Math.min(DAY_LENGTH, d.pg.now + TICK * Number(speed)), d.pg, d.done)
      setDay(next)
      if (next.pg.now >= DAY_LENGTH) setPlaying(false)
    }, TICK)
    return () => window.clearInterval(id)
  }, [playing, speed])
  const step = () => {
    const nextAt = SCRIPTED_DAY[day.done]?.at ?? DAY_LENGTH
    setDay(dayAt(nextAt, day.pg, day.done))
  }
  return (
    <div className="grid min-w-0 gap-xl lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div className="flex min-w-0 flex-col gap-l">
        <StageWidthPicker width={width} onWidth={setWidth} />
        <BuddyStage machine={day.pg.machine} width={width} />
        <Log log={day.pg.log} />
      </div>
      <div className="flex min-w-0 flex-col gap-l">
        <section role="group" aria-label="Play the day" className="flex flex-col gap-s">
          <Eyebrow as="h3">Clock · {clockOf(day.pg.now)} of {clockOf(DAY_LENGTH)}</Eyebrow>
          <div className="flex flex-wrap items-center gap-s">
            <Button size="sm" onClick={() => setPlaying(true)} disabled={ended || playing} disabledReason={ended ? 'The day has ended.' : 'It is playing.'}>Play</Button>
            <Button variant="secondary" size="sm" onClick={() => setPlaying(false)} disabled={!playing} disabledReason="It is not playing.">Pause</Button>
            <Button variant="secondary" size="sm" onClick={step} disabled={ended || playing} disabledReason={ended ? 'The day has ended.' : 'Pause it to step.'}>Next step</Button>
            <Button variant="ghost" size="sm" onClick={() => { setPlaying(false); setDay(dayAt(0)) }}>Start again</Button>
            <Segmented ariaLabel="Speed" size="sm" value={speed} onChange={setSpeed} options={SPEEDS} />
          </div>
        </section>
        <section role="region" aria-label="The day's steps" className="flex flex-col gap-s">
          <Eyebrow as="h3">The day · {situationOf(day.pg.machine)}</Eyebrow>
          <ol className="flex flex-col gap-xs">
            {SCRIPTED_DAY.map((s, i) => (
              <li key={i} data-type="body-s" aria-current={i === day.done ? 'step' : undefined} className={i < day.done ? 'text-on-surface-var' : 'text-on-surface'}>
                {clockOf(s.at)} {s.note}{i === day.done ? ' (next)' : ''}
              </li>
            ))}
          </ol>
        </section>
        <LookControls />
      </div>
    </div>
  )
}

export function PlaygroundPage({ query, setQuery }: RouteProps) {
  const [tab, setTab] = useQueryParam(query, setQuery, 'tab', 'play', { replace: true })
  return (
    <div data-full-screen="buddy" className="h-full overflow-y-auto" style={{ background: 'var(--color-canvas)' }}>
      <main className="mx-auto flex w-full min-w-0 max-w-7xl flex-col gap-xl p-l">
        <header className="flex min-w-0 flex-col gap-s">
          <PageTitle>Buddy playground</PageTitle>
          <p data-type="body-m" className="text-on-surface-var">
            The buddy's real state machine and orb, played with made-up work. Nothing here reaches the gateway: an answer is only written in the log.
          </p>
          <Segmented ariaLabel="Playground view" value={TABS.some((t) => t.key === tab) ? tab : 'play'} onChange={setTab} options={TABS} />
        </header>
        {tab === 'day' ? <Day /> : tab === 'poses' ? (
          <div className="flex min-w-0 flex-col gap-xl">
            <LookControls />
            <PoseSheet />
          </div>
        ) : <Play />}
      </main>
    </div>
  )
}
