import { useEffect, useState } from 'react'
import { Pause, Play, Square } from 'lucide-react'
import { Button } from '../../ui/Button'
import { LOOP_ACTION_SOURCE_STATUSES, shownCycle, type LoopAction } from '../../lib/loopStatus'
import type { LoopReport } from '../../lib/api'
import { DEFAULT_REPORT, LoopReportPill } from '../loop/LoopReportPill'

/** The four lifecycle actions the strip can send (`PATCH /api/loops/{id} {action}`). */
export type StripAction = 'start' | 'pause' | 'resume' | 'stop'

/** How many cycles a loop has used: "cycle 3/10", or "cycle 3 · ongoing" for one with no cap. */
export function cycleLabel(status: string, totalCycles: number, maxCycles: number): string {
  const shown = shownCycle(status, totalCycles)
  return maxCycles === 0 ? `cycle ${shown} · ongoing` : `cycle ${shown}/${maxCycles}`
}

/** The thin progress strip above a goal loop's composer (#3824): the cycles it has used and its
 *  controls. Each control shows exactly when the action route accepts it, by asking
 *  LOOP_ACTION_SOURCE_STATUSES, the mirror of the route's guard; a loop cannot be offered a press
 *  that answers 409, nor be refused one the route takes; a surface that may show a run-backed loop
 *  passes `sources={loopActionSources(loop)}`, the run's own map. Stop is terminal (a stopped loop does not
 *  resume), so its first press arms it as "Stop for good?" and only the second sends it.
 *
 *  Given `onReport`, it also carries how the loop reports, the choice its start asked for, to change
 *  while the loop still has cycles ahead of it; a loop no action can move on has nothing left to
 *  report. */
export function LoopProgressStrip({ status, totalCycles, maxCycles, onAction, report, onReport, sources = LOOP_ACTION_SOURCE_STATUSES }: {
  status: string
  totalCycles: number
  maxCycles: number
  onAction: (action: StripAction) => void
  report?: LoopReport
  onReport?: (report: LoopReport) => void
  sources?: Readonly<Record<LoopAction, ReadonlySet<string>>>
}) {
  const [stopArmed, setStopArmed] = useState(false)
  useEffect(() => {
    if (!stopArmed) return
    // The census of armed controls (`armedDeleteDisarms.test.ts`) holds every one to 4000ms.
    const t = window.setTimeout(() => setStopArmed(false), 4000)
    return () => clearTimeout(t)
  }, [stopArmed])
  const can = (a: StripAction) => sources[a].has(status)
  const ahead = (['start', 'pause', 'resume', 'stop'] as const).some(can)
  return (
    <div role="toolbar" aria-label="This loop" className="flex min-h-11 flex-wrap items-center gap-s rounded-lg bg-surface-container/60 px-m py-1.5">
      <span data-type="caption" className="tabular-nums text-on-surface-var">{cycleLabel(status, totalCycles, maxCycles)}</span>
      {onReport && ahead && <LoopReportPill value={report ?? DEFAULT_REPORT} onChange={onReport} />}
      <span className="flex-1" />
      {can('start') && <Button size="sm" onClick={() => onAction('start')}><Play size={14} /> Start</Button>}
      {can('pause') && <Button size="sm" variant="secondary" onClick={() => onAction('pause')}><Pause size={14} /> Pause</Button>}
      {can('resume') && <Button size="sm" onClick={() => onAction('resume')}><Play size={14} /> Resume</Button>}
      {can('stop') && (
        <Button size="sm" variant={stopArmed ? 'danger' : 'secondary'}
          onClick={() => { if (!stopArmed) { setStopArmed(true); return } setStopArmed(false); onAction('stop') }}>
          <Square size={14} /> {stopArmed ? 'Stop for good?' : 'Stop'}
        </Button>
      )}
    </div>
  )
}
