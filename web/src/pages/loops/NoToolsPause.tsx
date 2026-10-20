import { ArrowRight, CirclePause, Play } from 'lucide-react'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { cx } from '../../ui/cx'
import { withWeight } from '../../design/fontWeight'

/** A loop whose worker ran a cycle without tools: paused, not failed (`LoopWatchdog.
 *  hold_without_tools`). Its model can't use them, and a loop's worker does all its work with
 *  tools, so a cycle could write no finding. Nothing is asked of its owner but a model that uses
 *  tools: the gateway's sentence names the model and where to choose another, the link opens
 *  Settings → Models, and Resume carries on (refused, in words, while the model still can't). */
export function NoToolsPause({ question, why, onResume, busy = false, className }: {
  /** The gateway's sentence: the model, and what to choose instead. */
  question: string
  /** A line under it: what the pause means for the loop. */
  why?: string
  /** Resume the loop. Omit where another control on the page already does. */
  onResume?: () => void
  /** Resume is under way: its button spins and is disabled. */
  busy?: boolean
  className?: string
}) {
  return (
    <div role="status" data-type="body-s"
      className={cx('flex flex-col items-start gap-xs rounded-lg bg-warn/10 px-m py-s', className)}>
      <p className="inline-flex items-center gap-xs" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
        <CirclePause size={14} /> Paused: its model can’t use tools
      </p>
      <p className="whitespace-pre-wrap text-on-surface">{question}</p>
      {why && <p data-type="caption" className="whitespace-pre-wrap text-on-surface-low">{why}</p>}
      <div className="flex flex-wrap items-center gap-m">
        <TextLink href="#/settings/models" icon={ArrowRight} iconPosition="trailing" size="xs" ink="emphasis">
          Open Settings → Models
        </TextLink>
        {onResume && (
          <Button variant="secondary" size="xs" onClick={onResume} loading={busy}>
            <Play size={13} /> Resume
          </Button>
        )}
      </div>
    </div>
  )
}
