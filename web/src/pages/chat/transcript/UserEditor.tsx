import { useId, useState } from 'react'
import { Rewind } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { InlineError } from '../../../ui/InlineError'
import { replacedTurnsAreKept } from '../editReplaces'

/** Inline editor for a user turn (Edit & resend). Replaces the bubble with a
 *  right-aligned textarea + Cancel/Resend; ⌘↵ submits, Esc cancels.
 *
 *  `replacesLater`: this is an EARLIER turn, so resending replaces every exchange below it.
 *  That is said while the editor is open — beside the button that does it, and on the button
 *  itself — together with where the replaced turns go, because the old editor resent a middle
 *  turn with no warning and the later turns were simply gone. */
export function UserEditor({ initial, onSubmit, onCancel, replacesLater = false, canFork = false, failure = null }: {
  initial: string; onSubmit: (v: string) => void; onCancel: () => void
  replacesLater?: boolean; canFork?: boolean
  /** Why the last Resend did not go through. The editor stays open with the text, and the
   *  transcript below it is untouched, so the user can try again or cancel. */
  failure?: string | null
}) {
  const [v, setV] = useState(initial)
  const noticeId = useId()
  return (
    <div className="flex flex-col items-end gap-2">
      {/* Raw: the in-place editor keeps the sent message bubble's radius, padding and type size, which TextArea's field chrome does not draw. */}
      <textarea autoFocus value={v} onChange={(e) => setV(e.target.value)} rows={Math.min(10, v.split('\n').length + 1)}
        aria-label="Edit your message"
        aria-describedby={replacesLater ? noticeId : undefined}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); onCancel() }
          else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSubmit(v) }
        }}
        className="w-full resize-none rounded-2xl bg-surface-container px-5 py-4 text-on-surface text-[1.0625rem] leading-relaxed outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
        style={{ maxWidth: '80%' }} />
      {replacesLater && (
        <p id={noticeId} data-type="caption" className="flex w-full items-start gap-1.5 text-on-surface-var" style={{ maxWidth: '80%' }}>
          <Rewind size={12} className="mt-0.5 shrink-0" />
          <span>Resending replaces everything below this message. {replacedTurnsAreKept(canFork)}</span>
        </p>
      )}
      {failure && (
        <div className="w-full" style={{ maxWidth: '80%' }}>
          <InlineError icon multiline>{failure}</InlineError>
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} className="px-3 text-on-surface-low">Cancel</Button>
        <Button size="sm" onClick={() => onSubmit(v)} disabled={!v.trim()} className="px-4"
          disabledReason={!v.trim() ? 'The message cannot be empty' : undefined}>{replacesLater ? 'Resend & replace' : 'Resend'}</Button>
      </div>
    </div>
  )
}
