import { FieldError } from '../../ui/forms'
import type { InboxDrafting } from '../../lib/api'

/** Words as an editor counts them, and as the server counts a draft against her limit
 *  (`reply_grounding.word_count`): runs of text between whitespace. */
export function wordCount(text: string): number {
  return text.split(/\s+/).filter(Boolean).length
}

/** What each place a reply leaves for her answer says is needed, in order, read as the server
 *  reads it (`reply_answers.open_answers`): `[your answer: …]`, an unclosed one running to the end
 *  of its line. A reply that still holds one is not sent. */
export function openAnswers(text: string): string[] {
  return [...text.matchAll(/\[\s*your\s+answer\s*:\s*([^\]\n]*)\]?/gi)]
    .map((m) => m[1].split(/\s+/).filter(Boolean).join(' ') || 'an answer')
}

/** Why Send waits while the reply still leaves *count* places for her answer. */
export function openAnswersReason(count: number): string {
  return count === 1
    ? 'Write your answer where it says [your answer: …], or take that out, first'
    : `Write your answers in the ${count} places that say [your answer: …], or take them out, first`
}

/** What the last Generate draft said, under the button that ran it, in the order she needs it:
 *  why nothing was written (a file she named could not be read), what it needs from her before
 *  it drafts, that it judged no reply is needed, what the draft stood on, and what its check did:
 *  the answers it had given for her that nothing of hers gave, now left to her, or that it could
 *  not be checked. */
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
  const taken = drafting?.answered_for_you ?? 0
  return (
    <>
      {summary && <p data-type="caption" className="text-on-surface-low" role="status">{summary}</p>}
      {cut.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          Only the start of {cut.map((n) => n.found).join(', ')} fit, so the draft saw that much of it.
        </p>
      )}
      {taken > 0 && (
        <p data-type="caption" className="text-on-surface-low" role="status">
          {taken === 1
            ? 'It had answered one of the message’s questions for you with something you didn’t say and your notes don’t say. That is left for you to answer instead.'
            : `It had answered ${taken} of the message’s questions for you with things you didn’t say and your notes don’t say. Those are left for you to answer instead.`}
        </p>
      )}
      {drafting?.unchecked && (
        <p data-type="caption" className="text-warn" role="status">
          It couldn&rsquo;t check this draft against what you said and your notes, so read each answer in it before you send.
        </p>
      )}
    </>
  )
}

/** What the reply in the box still leaves for her to answer, read from it as she edits. */
export function OpenAnswers({ text }: { text: string }) {
  const open = openAnswers(text)
  if (!open.length) return null
  return (
    <p data-type="caption" className="text-on-surface-low">
      Left for you to answer, where it says [your answer: …]: {open.join('; ')}.
    </p>
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
