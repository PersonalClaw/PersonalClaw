import { useId } from 'react'
import { api, type LoopGauge, type WaitFigures } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { Surface } from '../../ui/Surface'
import { Section } from './settingsUI'

/** How often the open section reads the gauge again. */
const EVERY_MS = 5000

const ms = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`)

/** How long ago, for a reading the gauge took *s* seconds ago. */
const ago = (s: number) => (s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`)

/** The figures each measure shows: now, its usual (p95), and its worst in the last minute and ten. */
const FIGURES: [string, (f: WaitFigures) => number][] = [
  ['Now', (f) => f.now_ms],
  ['Usually (95%)', (f) => f.p95_ms],
  ['Worst, last minute', (f) => f.max_1m_ms],
  ['Worst, last 10 minutes', (f) => f.max_10m_ms],
]

/** One measure: what it is, then its four figures, two a row on a phone and four on a wider panel. */
function Measure({ label, detail, f }: { label: string; detail: string; f: WaitFigures }) {
  const labelId = useId()
  return (
    <div role="group" aria-labelledby={labelId} className="border-t border-outline-variant/40 py-s first:border-t-0 first:pt-0 last:pb-0">
      <p id={labelId} data-type="body-m" className="text-on-surface">{label}</p>
      <p data-type="body-s" className="text-on-surface-low">{detail}</p>
      <dl className="mt-xs grid grid-cols-2 gap-x-m gap-y-xs sm:grid-cols-4">
        {FIGURES.map(([name, pick]) => (
          <div key={name}>
            <dt data-type="body-s" className="text-on-surface-low">{name}</dt>
            <dd data-type="body-m" className="tabular-nums text-on-surface">{ms(pick(f))}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** Settings › Diagnostics: how long the gateway keeps work waiting. Its event loop's lag (work it
 *  runs on the loop holds every other request for as long), and how long work waits for one of
 *  its worker threads (a full pool holds every read that hands work to one). Read every few
 *  seconds while the panel is open, from the gateway's always-on gauge. */
export function GatewayWaitSection() {
  const { data: raw, error, refresh } = useQuery<LoopGauge>('system:loop', () => api.loopGauge())
  // Read as unknown: an older gateway, or a stand-in, may answer without the figures.
  const data = raw && typeof raw === 'object' && raw.loop_lag && raw.pool_wait && raw.pool
    && typeof raw.sampling === 'boolean' ? raw : null
  // Paused while the tab is hidden, and slower while nobody uses it, as the shell's other polls.
  useVisiblePoll(refresh, EVERY_MS, { immediate: false })
  // A gauge that stopped sampling empties its window, and every figure reads 0 ms: never a calm
  // reading, so it says it is not measuring instead.
  const last = data?.loop_lag.last_sample_s_ago
  return (
    <Section
      title="How long the gateway keeps work waiting"
      hint="When pages are slow to fill, this says why: work holding the gateway's loop, or work waiting for one of its worker threads. The last minute and the last ten."
    >
      <Surface tone="container" radius="lg" className="px-l py-m">
        {!data ? (
          <p data-type="body-m" className="text-on-surface-low">
            {error || raw ? 'The gateway did not say how long it keeps work waiting.' : 'Reading the gateway’s wait…'}
          </p>
        ) : !data.sampling ? (
          <p data-type="body-m" className="text-on-surface-low">
            {'The gateway is not measuring how long it keeps work waiting right now'}
            {typeof last === 'number' ? `: its last reading is ${ago(last)} old.` : '.'}
          </p>
        ) : (
          <div>
            <Measure label="The gateway’s loop" detail="How late it ran what it was asked to." f={data.loop_lag} />
            <Measure
              label="Waiting for a worker"
              detail={`${data.pool.workers} worker threads; ${data.pool.queued_now} waiting now, ${data.pool.queued_max_1m} at most in the last minute.`}
              f={data.pool_wait}
            />
          </div>
        )}
      </Surface>
    </Section>
  )
}
