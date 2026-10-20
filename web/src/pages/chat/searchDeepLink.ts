/** Chat-history content-search deep-linking.
 *
 *  Tiny pure helpers, kept out of ChatPage's shell so the path/label logic is unit-tested
 *  without mounting the page. They hold no React or DOM.
 */
import type { SessionSearchAnswer } from '../../lib/api'

/** The route to open a chat FROM a content-search result, carrying the query term as
 *  `?find=<term>` so the opened session's find bar comes up pre-seeded and scrolls to
 *  the first matching message.
 *
 *  The term is URL-encoded because it is the user's own free-text query threaded into
 *  the URL — encoded, never interpolated into markup (the snippet + highlight stay
 *  parsed React parts; nothing here renders HTML). An empty/blank term yields a plain
 *  `chat/<key>` deep-link, unchanged, so opening a chat without an active search is
 *  exactly as it was. */
export function chatFindPath(key: string, term: string): string {
  const t = term.trim()
  return t ? `chat/${key}?find=${encodeURIComponent(t)}` : `chat/${key}`
}

/** The honest one-line caption for which path answered a content search — the `source`
 *  the backend reports and the client now keeps. `'index'` = the FTS5 index had the
 *  answer; `'scan'` = it fell back to the bounded transcript scan. Anything else
 *  (including undefined, i.e. no search ran) returns `''`, and the
 *  caller renders nothing — the indicator only appears once a search has actually
 *  resolved with a known path. */
export function searchSourceLabel(source: string | null | undefined): string {
  if (source === 'index') return 'matched via index'
  if (source === 'scan') return 'scanned transcripts'
  if (source === 'index+scan') return 'matched via index and scanned transcripts'
  return ''
}

/** How far a content search reached, for a `PartialNotice`: the chats it looked in whole, of how
 *  many, why not the others, and how many a direct read of the rest would cover. `null` when the
 *  answer covered every chat — or said nothing about it (no search ran). */
export function searchCoverage(answer: Pick<SessionSearchAnswer, 'searched' | 'complete' | 'index'>): {
  shown: number; total: number; rest: number; detail: string
} | null {
  if (answer.complete !== false || !answer.searched) return null
  const { chats, of } = answer.searched
  const rest = Math.max(0, of - chats)
  const others = rest === 1 ? 'one' : rest.toLocaleString()
  const detail = !answer.index
    ? 'there is no search index, so only the most recent were read.'
    : answer.index.indexed < answer.index.of
      ? `the search index is still being built, so matches in the other ${others} are not listed yet.`
      : rest === 1
        ? 'the other one is longer than the search index keeps, so only its beginning was searched.'
        : `the other ${others} are longer than the search index keeps, so only their beginnings were searched.`
  return { shown: chats, total: of, rest, detail }
}
