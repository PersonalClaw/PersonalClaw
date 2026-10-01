import { FieldError } from '../../ui/forms'
import type { InboxDrafting } from '../../lib/api'

/** Words as an editor counts them, and as the server counts a draft against her limit
 *  (`reply_grounding.word_count`): runs of text between whitespace. */
export function wordCount(text: string): number {
  return text.split(/\s+/).filter(Boolean).length
}

/** What the last Generate draft said, under the button that ran it, in the order she needs it:
 *  why nothing was written (a file she named could not be read), what it needs from her before
 *  it drafts, that it judged no reply is needed, and what the draft stood on. */
export function DraftNotices({ error, drafting, summary }: {
  error: string; drafting: InboxDrafting | null; summary: string
}) {
  if (error) return <FieldError>{error}</FieldError>
  if (drafting?.question) {
    return (
      <p data-type="body-s" className="text-on-surface" role="status">
        It needs your word before it drafts: {drafting.question} Say it in &ldquo;What should the reply say?&rdquo; and generate again. Nothing was written.
      </p>
    )
  }
  if (drafting?.skipped) {
    return (
      <p data-type="caption" className="text-on-surface-low" role="status">
        It judged this message needs no reply. Say what the reply should say to draft one anyway.
      </p>
    )
  }
  const cut = drafting ? [...drafting.read, ...drafting.related].filter((n) => n.truncated) : []
  return (
    <>
      {summary && <p data-type="caption" className="text-on-surface-low" role="status">{summary}</p>}
      {cut.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          Only the start of {cut.map((n) => n.found).join(', ')} fit, so the draft saw that much of it.
        </p>
      )}
    </>
  )
}

/** The draft's word count, against the limit she gave when she gave one. */
export function WordCount({ text, limit }: { text: string; limit: number | null }) {
  const n = wordCount(text)
  const noun = n === 1 ? 'word' : 'words'
  if (!limit) return <p data-type="caption" className="text-on-surface-low">{n} {noun}</p>
  if (n > limit) {
    return <p data-type="caption" className="text-warn">{n} {noun}, over the {limit} you asked for</p>
  }
  return <p data-type="caption" className="text-on-surface-low">{n} of {limit} words</p>
}
