import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type BrowseStepFrame } from '../../../lib/api'
import { useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { useVisiblePoll } from '../../../lib/useVisiblePoll'
import { StatusPill } from '../../../ui/StatusPill'
import { WidgetRow } from './kit'

/** Live browse mirror, shown on Home's Overview only while an unattended browse runs: the
 *  browser-side sibling of DesktopLiveView — the two share one live-watch idiom so browser and
 *  desktop feel alike.
 *
 *  It draws the run's steps as they happen: each `browse_step` WS frame the loop already emits —
 *  the SCREENED url, the rendered action line (a credential `TYPE` is already `[withheld]`
 *  upstream), the screenshot PATH and any note — one row each, newest first. The panel exposes
 *  nothing the run did not already record: no debug port, no CDP, no new attack surface
 *  (browse/mirror.py's own words). It is OBSERVE-ONLY.
 *
 *  🔑 ONLY WHILE LIVE. A browse is live while it takes steps: the gateway says so on
 *  `GET /api/browse/status` (`live`, a step within the last 30 s, `browse/mirror.is_live`), which
 *  a page opened mid-browse reads, and each step frame says so at once. When no step has come for
 *  30 s the band goes. What used to sit beside the steps has a home that does not hide:
 *    · a per-task grant (a run parked until you allow it to use your browser) is answered in
 *      Attention, beside every other ask (`waiting.py`'s `browse_grant`);
 *    · the kill switch that stops all unattended browsing, and the sites whose saved sign-in
 *      expired, are in Settings › Security › Guardrails, with the explicit re-enable; a browse a
 *      chat started stops with that chat's own Stop.
 *
 *  🔑 THE SOCKET IS AN ARGUED EXCEPTION to DashboardLive's "no widget opens its own socket". That
 *  doctrine also says WS envelopes are SIGNALS, never payloads — true for envelopes like
 *  `sessions`, which say only that something moved. `browse_step` is the opposite: ONE producer
 *  (browse/mirror.py), a fixed named shape, and NO GET slice to read the step stream back from — so
 *  the frame's fields ARE the data and must be read. Precedent: `useAgentActivity` opens its own
 *  socket for the same reason. */

/** How many steps to keep. The feed is about the browse happening NOW, not an audit log — the SEL
 *  is the record. Older steps fall off the bottom. */
const KEPT_STEPS = 12
const DRAWN_STEPS = 8

/** How long after its last step a browse still counts as live: the gateway's own window
 *  (`browse/mirror.LIVE_SECS`). */
const LIVE_MS = 30_000

/** A slow safety-net read of whether a browse is live, for a page that opened mid-browse and
 *  missed its frames; a step frame lands a change at once. */
const STATUS_POLL_MS = 15000

export interface LiveStep extends BrowseStepFrame {
  /** A stable client key — the run + step number can repeat across runs, so a monotonic counter
   *  keeps React keys unique and the list order intent-stable. */
  seq: number
}

/** Whether a browse is live, and the steps it has taken while this page watched. Live while the
 *  gateway says so or a step came within the last 30 s; it ends 30 s after the last step, on a
 *  timer of its own rather than waiting for the next read. */
export function useBrowseLive(): { live: boolean; steps: LiveStep[] } {
  const [steps, setSteps] = useState<LiveStep[]>([])
  const [served, setServed] = useState(false)
  const [lastStepAt, setLastStepAt] = useState(0)
  const [, setTick] = useState(0)
  const alive = useRef(true)
  const seq = useRef(0)
  // Reads are concurrent (the poll, the reopen, a step) — the latest REQUEST wins, not the last
  // response, so an older read landing late cannot bring back a browse that already ended.
  const readSeq = useRef(0)
  useEffect(() => () => { alive.current = false }, [])

  const loadStatus = useCallback(() => {
    const mine = ++readSeq.current
    // Through a promise, so a call that throws as it is made is a failed read like any other.
    Promise.resolve()
      .then(() => api.browseStatus())
      .then((s) => { if (alive.current && mine === readSeq.current) setServed(s?.live === true) })
      // A failed read keeps the last answer: a band that was live stays until a read says otherwise.
      .catch(() => {})
  }, [])

  const onMessage = useCallback((m: WsMessage) => {
    if (m.type !== 'browse_step') return
    // The ONE payload read (see the header): the step stream has no GET to refetch from. Each
    // field is read off the untyped envelope and coerced — a producer/consumer version skew must
    // degrade a row, never crash the dashboard it decorates.
    const d = m.data
    const step: LiveStep = {
      seq: seq.current++,
      run_id: String(d.run_id ?? ''),
      step_n: Number(d.step_n ?? 0),
      url: String(d.url ?? ''),
      action: String(d.action ?? ''),
      screenshot: String(d.screenshot ?? ''),
      note: String(d.note ?? ''),
    }
    setSteps((prev) => [step, ...prev].slice(0, KEPT_STEPS))
    setLastStepAt(Date.now())
  }, [])

  useChatSocket(onMessage, loadStatus)  // reopened after a drop → read whether a browse is live
  useVisiblePoll(loadStatus, STATUS_POLL_MS)

  // The band ends 30 s after the last step, whether or not a read comes then.
  useEffect(() => {
    if (!lastStepAt) return
    const left = lastStepAt + LIVE_MS - Date.now()
    if (left <= 0) return
    const t = window.setTimeout(() => { setServed(false); setTick((n) => n + 1) }, left + 50)
    return () => window.clearTimeout(t)
  }, [lastStepAt])

  const recent = lastStepAt > 0 && Date.now() - lastStepAt <= LIVE_MS
  return { live: served || recent, steps }
}

export function BrowseMirror({ steps }: { steps: LiveStep[] }) {
  const drawn = steps.slice(0, DRAWN_STEPS)
  return (
    <div className="flex min-w-0 flex-col gap-s pt-xs">
      <div className="flex flex-wrap items-center gap-m">
        <StatusPill tone="primary">Browsing</StatusPill>
      </div>
      {drawn.length === 0 && (
        <p data-type="body-s" className="text-on-surface-low">
          A browse is running. Its steps show here as it takes them.
        </p>
      )}
      {drawn.length > 0 && (
        <div className="flex flex-col gap-xs">
          {drawn.map((s) => (
            <WidgetRow key={s.seq}>
              <div className="flex min-w-0 flex-col gap-xs">
                <div className="flex min-w-0 items-center gap-s">
                  <StatusPill tone="neutral">{`#${s.step_n}`}</StatusPill>
                  <span data-type="label-m" className="min-w-0 truncate text-on-surface" title={s.url}>
                    {s.url || '(no url)'}
                  </span>
                </div>
                <span data-type="body-s" className="min-w-0 truncate text-on-surface-var" title={s.action}>
                  {s.action || '(no action)'}
                </span>
                {s.screenshot && (
                  // The screenshot enters as a PATH reference, never fetched as bytes — the
                  // screenshot-as-path discipline (browse/loop.py's `assert_no_base64`), and it
                  // keeps the panel from ever dialing a run-workspace file.
                  <span
                    data-type="caption"
                    className="min-w-0 truncate font-mono text-on-surface-low"
                    title={s.screenshot}
                  >
                    [SCREENSHOT: {s.screenshot}]
                  </span>
                )}
                {s.note && (
                  <span data-type="caption" className="min-w-0 truncate text-on-surface-low" title={s.note}>
                    {s.note}
                  </span>
                )}
              </div>
            </WidgetRow>
          ))}
        </div>
      )}
    </div>
  )
}
