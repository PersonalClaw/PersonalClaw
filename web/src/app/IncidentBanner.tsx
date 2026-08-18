import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { api } from '../lib/api'
import { notify } from './appSdk'
import { useVisiblePoll } from '../lib/useVisiblePoll'
import { Button } from '../ui/Button'
import { treatmentPaint } from '../design/errorTreatments'
import { useErrorTreatment } from './personality'

/** A persistent banner shown on every page while incident mode is active
 *  (AUTONOMY-GUARDRAILS §1.3, §4.4). Incident mode suspends all unattended work;
 *  this makes that state impossible to miss and offers one-click resume.
 *  Polls the incident endpoint on a slow cadence (it changes rarely, and the CLI
 *  can flip it out-of-band). Renders nothing when there is no incident. */
export function IncidentBanner() {
  const [state, setState] = useState<{ active: boolean; reason: string } | null>(null)
  const [busy, setBusy] = useState(false)
  // Personality skin (PERSONALITY-THEMES §S2). Presentation only — the copy, the
  // `role="alert"` and the Resume action below are the same for every identity.
  const treatment = useErrorTreatment()

  useVisiblePoll(() => {
    api.incident().then((s) => setState({ active: s.active, reason: s.reason })).catch(() => {})
  }, 15000)

  if (!state?.active) return null

  // Spread LAST into the style object below so a treatment's colours win. With no
  // treatment this is `null`, the spread is a no-op, and both the class list and
  // the style object are byte-identical to the pre-personality banner.
  const paint = treatmentPaint(treatment)

  const resume = async () => {
    setBusy(true)
    try {
      await api.incidentResume()
      setState({ active: false, reason: '' })
    } catch (e) {
      notify(`Couldn't resume: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(false) }
  }

  return (
    <div role="alert"
      className={['flex items-center gap-3 pl-4 py-2 text-[0.8125rem]', treatment?.surfaceClass]
        .filter(Boolean)
        .join(' ')}
      // 🔴 THE RESUME BUTTON WAS UNCLICKABLE. This banner is the first child of the
      // `relative` main area, and `ShellCornerRight` is pinned `absolute right-0 top-0
      // z-30` in that same box — so the corner cluster (terminal • width • bell • theme •
      // health) landed ON TOP of the banner's right end. Measured at 1440/1280/1024/390:
      // `document.elementFromPoint` over the Resume button's own centre returned the
      // degraded-status pill at EVERY width, so the one-click recovery from incident mode
      // — the whole point of the banner — could not be clicked where anyone would aim.
      //
      // `--shell-corner-r` is the mechanism that already solves this (ShellCorners
      // publishes its measured width; TopBar pads by it, SidePanel starts below it). The
      // banner arrived later and never opted in. Same expression as TopBar's, fallback
      // included, so a corner that has not measured yet still clears.
      style={{
        background: 'var(--color-error-container)',
        color: 'var(--color-on-error-container)',
        paddingRight: 'calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px))',
        ...paint,
      }}>
      <AlertTriangle size={16} className={['shrink-0', treatment?.iconClass].filter(Boolean).join(' ')} />
      <span className="min-w-0 flex-1 truncate">
        <strong>Incident mode is active</strong> — all unattended work (cron, hooks, triggers,
        subagents) is suspended{state.reason ? ` · ${state.reason}` : ''}. Chat still works.
      </span>
      <Button variant="danger" size="xs" onClick={resume} loading={busy} className="shrink-0">
        Resume
      </Button>
    </div>
  )
}
