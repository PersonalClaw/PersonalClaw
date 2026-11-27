import { useEffect, useRef, useState } from 'react'
import { api, type PreviewItem, type SchedulePreview, type SchedulePreviewBody } from '../../lib/api'
import { AlertTriangle } from 'lucide-react'
import { Button } from '../../ui/Button'
import { cronExprInvalidReason } from './cronExpr'
import { relFuture } from './scheduleMeta'

// ── A schedule's next runs, under its When controls and in its detail ─────────────────────────
//
// A schedule showed one next run, so a cadence that landed on a clock change, a skip date or quiet
// hours surprised its owner only after it fired. This shows the next runs as the clock will make
// them (`POST /api/triggers/preview`, the clock's own steps), with a skipped day struck through
// and each note in words. The form asks 300 ms after the last change; a newer ask aborts the one
// before it, and an older answer is never drawn over a newer one.

/** How long the form waits after the last change before it asks. */
export const PREVIEW_DEBOUNCE_MS = 300

/** The notes drawn in warning ink: the ones that change when, or whether, it runs. */
const WARN: ReadonlySet<PreviewItem['note_kind']> = new Set(['skip_date', 'quiet_hours', 'clock_forward', 'clock_back'])

/** The schedule part of a save body (`draftToPayload`), which the preview reads as the save does. */
export function previewBody(payload: Record<string, unknown>, triggerId = '', count = 5): SchedulePreviewBody {
  const body: SchedulePreviewBody = { count }
  for (const key of ['every', 'cron', 'at', 'timezone', 'skip_dates', 'strict_schedule'] as const) {
    if (payload[key] !== undefined) (body as Record<string, unknown>)[key] = payload[key]
  }
  if (triggerId) body.trigger_id = triggerId
  return body
}

/** The browser's own zone, for "Times are in …" when the automation's differs. */
function browserZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || '' } catch { return '' }
}

type Shown = { preview: SchedulePreview; body: string } | null

/** The next runs of the schedule *body* (`previewBody`). *cron* is the draft's expression when its
 *  kind is cron: the field already says why the server would refuse a malformed one, so a refusal
 *  is shown here only when that check passes. *onCadence* hears the schedule in words. */
export function NextRuns({ body, cron, readOnly = false, onCadence }: {
  body: SchedulePreviewBody
  cron?: string
  readOnly?: boolean
  onCadence?: (cadence: string) => void
}) {
  const key = JSON.stringify(body)
  // A draft with no time yet (an empty picker) has nothing to preview, and the route would answer
  // it 400: nothing is asked and nothing is shown until it has one.
  const hasCadence = body.every !== undefined || Boolean(body.cron) || body.at !== undefined || Boolean(body.trigger_id)
  const [shown, setShown] = useState<Shown>(null)
  const [error, setError] = useState<unknown>(null)
  const [asking, setAsking] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const latest = useRef(0)
  const announced = useRef('')
  const [announce, setAnnounce] = useState('')

  useEffect(() => {
    const ask = ++latest.current
    if (!hasCadence) { setShown(null); setError(null); setAsking(false); return }
    const controller = new AbortController()
    setAsking(true)
    const timer = window.setTimeout(() => {
      // Through a promise, so a request that throws as it starts is a failed ask, said with Retry.
      Promise.resolve()
        .then(() => api.previewSchedule(JSON.parse(key) as SchedulePreviewBody, { signal: controller.signal }))
        .then((preview) => {
          if (ask !== latest.current) return
          setShown({ preview, body: key }); setError(null); setAsking(false)
          onCadence?.(preview.cadence)
          const first = preview.outcome === 'ok' ? preview.items.find((i) => i.runs) : undefined
          const line = first ? `Next run ${first.wall}` : ''
          if (line !== announced.current) { announced.current = line; setAnnounce(line) }
        })
        .catch((e: unknown) => {
          if (ask !== latest.current || controller.signal.aborted) return
          setError(e); setAsking(false)
        })
    }, readOnly ? 0 : PREVIEW_DEBOUNCE_MS)
    return () => { window.clearTimeout(timer); controller.abort() }
    // `onCadence` is the caller's setter; a new function each render must not ask again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt, readOnly, hasCadence])

  if (!hasCadence) return null

  // The announcer sits in a positioned box of its own: `sr-only` is absolute, and with no positioned
  // parent it lands where the form would put it at the page's scale, which at 200% text is past the
  // app's frame, so the frame clipped (the matrix's 200% text-zoom step found it).
  const live = <div className="relative"><div aria-live="polite" className="sr-only">{announce}</div></div>
  if (error && !asking) {
    // `InlineLoadError`'s look, with the sentence that says what failed: the gateway's own message
    // is about a request, and the owner asked when the schedule runs.
    return (
      <div>
        {live}
        <p role="alert" data-type="body-m" className="flex items-center gap-1.5 py-xs text-on-surface-low">
          <AlertTriangle size={14} className="shrink-0 text-danger opacity-70" aria-hidden />
          <span>Couldn’t work out the next runs.</span>
          <Button variant="ghost-accent" size="xs" onClick={() => setAttempt((n) => n + 1)}>Retry</Button>
        </p>
      </div>
    )
  }
  if (!shown) {
    return <div>{live}<p role="status" data-type="caption" className="text-on-surface-low">Working out the next runs…</p></div>
  }
  const { preview } = shown
  if (preview.outcome === 'refused') {
    // The cron field says its own reason for an expression its check refuses; this says the
    // server's only for what that check let through (a skip date, a zone).
    if (cron !== undefined && cronExprInvalidReason(cron)) return live
    return <div>{live}<p role="status" data-type="caption" className="text-warn">{preview.problem}</p></div>
  }
  if (preview.outcome === 'never') {
    return <div>{live}<p role="status" data-type="caption" className="text-on-surface-low">It never runs: {preview.problem}</p></div>
  }
  const zone = preview.zone && preview.zone !== browserZone() ? preview.zone : ''
  return (
    <div className={asking ? 'opacity-60' : undefined}>
      {live}
      <div className="flex items-baseline gap-s">
        <span data-type="label-m" className="text-on-surface-var">Next runs</span>
        {asking && <span data-type="caption" className="text-on-surface-low">Updating…</span>}
      </div>
      {zone && <p data-type="caption" className="text-on-surface-low">Times are in {zone}.</p>}
      <ol aria-label="Next runs" className="mt-xs flex flex-col gap-xs">
        {preview.items.map((item) => (
          <li key={`${item.at}:${item.runs}`} className="flex flex-col">
            <span className="flex flex-wrap items-baseline gap-x-s">
              <span data-type="label-m" className={item.runs ? 'text-on-surface' : 'text-on-surface-low line-through'}>{item.wall}</span>
              <span data-type="caption" className="text-on-surface-low">{item.runs ? relFuture(item.at) : 'skipped'}</span>
            </span>
            {item.note && (
              <span data-type="caption" className={WARN.has(item.note_kind) ? 'text-warn' : 'text-on-surface-low'}>{item.note}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
