/** Chat-history content-search deep-linking (SM-2).
 *
 *  Two tiny pure helpers, kept out of ChatPage's shell so the path/label logic is
 *  unit-tested without mounting the page. Both are string-in/string-out and hold no
 *  React or DOM.
 */

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
 *  answer; `'scan'` = it fell back to the bounded transcript scan (SESSION-MANAGEMENT
 *  §C1). Anything else (including undefined, i.e. no search ran) returns `''`, and the
 *  caller renders nothing — the indicator only appears once a search has actually
 *  resolved with a known path. */
export function searchSourceLabel(source: string | null | undefined): string {
  if (source === 'index') return 'matched via index'
  if (source === 'scan') return 'scanned transcripts'
  return ''
}
