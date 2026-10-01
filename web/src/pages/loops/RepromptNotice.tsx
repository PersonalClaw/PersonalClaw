import { RotateCcw } from 'lucide-react'
import { cx } from '../../ui/cx'
import type { RepromptInfo } from './runFold'

/** What a re-prompt says on the loop's page: which worker, why, and how many asks are left. */
export function repromptSentence(r: RepromptInfo): string {
  const who = r.title.trim() ? `The worker on “${r.title.trim()}”` : 'The loop’s worker'
  const owed = r.file ? ` (${r.file})` : ''
  const left = r.left > 0 ? `${r.left} left` : 'the last one'
  return `${who} ended its turn without writing its finding${owed}, so it was asked again: re-prompt ${r.attempt} of ${r.of}, ${left}.`
}

/** A worker that ended its turn without its finding is asked again, a few times at most, and each
 *  ask is a model turn its owner pays for and, on an Attended loop, an approval to answer. So the
 *  loop's page says so while it happens (`loop/manager.announce_reprompt`); the cycle moving on
 *  clears it (`runFold.foldReducer`). */
export function RepromptNotice({ reprompt, className }: { reprompt: RepromptInfo | null; className?: string }) {
  if (!reprompt) return null
  return (
    <div role="status" data-type="body-s" className={cx('flex items-start gap-s bg-info/10 text-on-surface', className)}>
      <RotateCcw size={14} className="mt-xs shrink-0 text-info" aria-hidden />
      <span>{repromptSentence(reprompt)}</span>
    </div>
  )
}
