import { useId, useState } from 'react'
import { Send, X } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { TextArea } from '../../../ui/forms'
import { useKeptDraft } from '../../../lib/useKeptDraft'
import { reportActionFailure } from '../../../app/reportingWrite'
import type { CommentTarget } from '../../../ui/content/commentTarget'
import type { SelectedLines } from '../DiffView'

// ── A comment on a change under review ────────────────────────────────────────────────────────
//
// Under a change's diff in the review pane: a comment that reaches the agent as guidance, quoting
// the lines selected in the diff. Where it goes is its target (`CommentTarget`): the loop's next
// cycle while it is at work, a new chat once it has ended. It is sent only by its Send button, and an
// unsent one is kept for that change (`useKeptDraft`), so a reload gives it back.

/** A quote longer than this many lines is cut, and says how many it left out. */
const QUOTE_CAP = 40

/** The comment as the agent reads it: the file, the lines it is on, quoted, then what was written. */
export function changeCommentMessage(rel: string, selected: SelectedLines | null, text: string): string {
  if (!selected) return `On \`${rel}\`:\n\n${text}`
  const shown = selected.lines.slice(0, QUOTE_CAP)
  const left = selected.lines.length - shown.length
  const quote = shown.map((l) => `> ${l}`).join('\n') + (left > 0 ? `\n> … ${left} more line${left === 1 ? '' : 's'}` : '')
  const where = selected.from === selected.to ? `line ${selected.from}` : `lines ${selected.from}–${selected.to}`
  return `On \`${rel}\`, ${where}:\n${quote}\n\n${text}`
}

export function ChangeComment({ draftKey, rel, selected, onClearSelection, target }: {
  /** What the unsent comment is kept under: the loop and the change. */
  draftKey: string
  /** The change's path inside the workspace. */
  rel: string
  /** The lines selected in the diff, which the comment quotes. */
  selected: SelectedLines | null
  onClearSelection: () => void
  target: CommentTarget
}) {
  const fieldId = useId()
  const [text, setText, clear] = useKeptDraft(draftKey)
  const [busy, setBusy] = useState(false)
  const send = async () => {
    const body = text.trim()
    if (!body || busy) return
    setBusy(true)
    try {
      await target.submit({ message: changeCommentMessage(rel, selected, body), docPaths: [] })
      clear(); onClearSelection()
    } catch (e) {
      // The comment stays in its field, and kept, to send again.
      reportActionFailure('send that comment')(e)
    } finally { setBusy(false) }
  }
  return (
    <form className="flex shrink-0 flex-col gap-xs border-t border-outline-variant/40 px-3 py-2"
      onSubmit={(e) => { e.preventDefault(); void send() }}>
      <label htmlFor={fieldId} data-type="caption" className="text-on-surface-low">Comment on {rel}</label>
      {selected && (
        <p data-type="caption" className="flex items-center gap-xs text-on-surface-var">
          <span>{selected.from === selected.to ? `On line ${selected.from}` : `On lines ${selected.from}–${selected.to}`}</span>
          <Button variant="ghost" size="xs" onClick={onClearSelection} ariaLabel="Comment on the whole change">
            <X size={12} /> Whole change
          </Button>
        </p>
      )}
      <TextArea id={fieldId} value={text} onChange={setText} rows={2} size="sm"
        placeholder="Guidance for the agent's next step" />
      <div className="flex justify-end">
        <Button type="submit" size="xs" loading={busy} disabled={!text.trim()} disabledReason="Write a comment first">
          <Send size={13} /> {target.label}
        </Button>
      </div>
    </form>
  )
}
