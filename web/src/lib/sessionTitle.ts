import { sessionActivitySeconds } from './epoch'

/** The fields the display-title derivation reads. A structural superset of
 *  `ChatSessionSummary`, `ChatSession`, and the peek/header shapes, so any of
 *  them passes through unchanged. This is the ONE seam every session-title
 *  render site calls instead of hand-rolling `s.title || s.key`. */
export interface TitleableSession {
  key: string
  title?: string
  /** First-user-message / prompt preview, when the summary carries one. */
  prompt_preview?: string
  /** Most-recent conversational line (already redacted, ~80 chars) — the
   *  fallback content snippet when there is no prompt preview. */
  last_message?: string
  created?: string
  last_activity_ts?: string
  last_ts?: string
}

// A machine-generated session key is `chat-<counter>-<epoch>` (dashboard state's
// new-session path). The backend's persistence load falls the title back to this
// raw key (`raw_title = … or session_name`), so a truthy-but-machine title reaches
// the UI and every naive `title || 'Untitled'` fallback sails straight past it —
// which is exactly why the raw id showed as a chat title / resume label (WT-11).
const RAW_SESSION_KEY = /^chat-\d+-\d+$/

/** `true` when `title` is not a human title: absent/blank, equal to the session
 *  key, or the raw `chat-N-epoch` machine slug. */
export function isRawSessionId(title: string | undefined | null, key?: string): boolean {
  const t = (title ?? '').trim()
  if (!t) return true
  if (key && t === key.trim()) return true
  return RAW_SESSION_KEY.test(t)
}

const MAX_SNIPPET = 60

/** Collapse whitespace and clip to `MAX_SNIPPET`, breaking on a nearby word
 *  boundary so a long first message truncates cleanly rather than mid-word. */
function toSnippet(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (clean.length <= MAX_SNIPPET) return clean
  const cut = clean.slice(0, MAX_SNIPPET)
  const space = cut.lastIndexOf(' ')
  return `${(space >= MAX_SNIPPET - 15 ? cut.slice(0, space) : cut).trimEnd()}…`
}

/** Short relative stamp ("now" / "3m" / "5h" / "2d" / "1w"), mirroring the chat
 *  page's `relTimeShort` and built on the canonical `epochSeconds` parser (via
 *  `sessionActivitySeconds`, no local `Date.parse`). Empty when no stamp reads. */
function relStamp(secs?: number): string {
  if (secs == null) return ''
  const s = Math.max(0, Date.now() / 1000 - secs)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  if (s < 604800) return `${Math.floor(s / 86400)}d`
  return `${Math.floor(s / 604800)}w`
}

/** The one place a chat session's human-readable display title is derived.
 *
 *  Order: a real user/auto title as-is → else the first-user-message / content
 *  snippet the summary carries → else `Untitled chat · <relative time>`. It
 *  NEVER returns a raw `chat-N-epoch` id or the bare session key, so no render
 *  site has to guard against the machine slug itself. */
export function sessionTitle(s: TitleableSession): string {
  const human = (s.title ?? '').trim()
  if (!isRawSessionId(human, s.key)) return human
  const snippet = (s.prompt_preview ?? '').trim() || (s.last_message ?? '').trim()
  if (snippet) return toSnippet(snippet)
  const when = relStamp(sessionActivitySeconds(s))
  return when ? `Untitled chat · ${when}` : 'Untitled chat'
}
