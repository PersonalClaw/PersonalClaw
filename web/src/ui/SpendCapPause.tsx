import { ArrowRight, CirclePause, Play } from 'lucide-react'
import { Button } from './Button'
import { TextLink } from './TextLink'
import { cx } from './cx'
import { withWeight } from '../design/fontWeight'

/** The Settings pages a spend ceiling's refusal is lifted on, by the route id the gateway names
 *  them with (`BudgetExceededError.settings_page`): the ceilings, and a model's price. */
const SETTINGS_PAGES: Record<string, string> = { guardrails: 'Guardrails', usage: 'Usage' }

/** A run a spend ceiling stopped: paused, not failed. The refusal's own sentence (which ceiling,
 *  what was spent against it, what the call needed, where it is lifted), a link to the Settings
 *  page it names, and Resume. A loop's cockpit and a planning walkthrough show it in place of
 *  "Drafting…" or a question to answer: nothing is asked of the user but to lift the cap or wait for
 *  it to reset, and nothing runs again until they resume. */
export function SpendCapPause({ reason, detail, settings, onResume, busy = false, resumeLabel = 'Resume', className }: {
  /** The refusal as the gateway says it (`BudgetExceededError.sentence`). */
  reason: string
  /** A line under it: what the pause means for this run. */
  detail?: string
  /** The Settings page the cap is changed on, as its route id (`guardrails`, `usage`). */
  settings?: string
  /** Resume the run. Omit where another control on the page already does. */
  onResume?: () => void
  /** Resume is under way: its button spins and is disabled. */
  busy?: boolean
  resumeLabel?: string
  className?: string
}) {
  const page = settings ? SETTINGS_PAGES[settings] : undefined
  return (
    <div role="status" data-type="body-s"
      className={cx('flex flex-col items-start gap-xs rounded-lg px-m py-s', className)}
      style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
      <p className="inline-flex items-center gap-xs" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
        <CirclePause size={14} /> Paused by a spend cap
      </p>
      <p className="whitespace-pre-wrap text-on-surface">{reason}</p>
      {detail && <p data-type="caption" className="whitespace-pre-wrap text-on-surface-low">{detail}</p>}
      {(page || onResume) && (
        <div className="flex flex-wrap items-center gap-m">
          {page && <SpendCapSettingsLink settings={settings ?? ''} />}
          {onResume && (
            <Button variant="secondary" size="xs" onClick={onResume} loading={busy}>
              <Play size={13} /> {resumeLabel}
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

/** The link to the Settings page a spend cap's refusal is lifted on (`settings`, its route id), in
 *  the words every cap surface uses for it; nothing for a page it does not know. */
export function SpendCapSettingsLink({ settings, size = 'xs' }: { settings: string; size?: 'xs' | 'sm' }) {
  const page = SETTINGS_PAGES[settings]
  if (!page) return null
  return (
    <TextLink href={`#/settings/${settings}`} icon={ArrowRight} iconPosition="trailing" size={size} ink="emphasis">
      Open Settings → {page}
    </TextLink>
  )
}
