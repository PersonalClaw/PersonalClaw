import { browserStore } from './browserStore'

// ── What the owner is typing in each chat, kept through a reload ──────────────────────────────
//
// Each chat's draft lives in this browser's `drafts` store (`browserStore.ts`), keyed by the chat:
// its text, the blocks pasted into it, the files attached to it and what it mentions (#4012). The
// composer writes it 500 ms after the last change (its caller debounces), restores it when the
// chat opens, and clears it on its send's receipt or on Discard draft. A draft that holds a
// widget's words is not kept: restored, they would read as the owner's own. A Temporary or Incognito chat keeps none: the composer
// never writes one for it, and switching a chat to one clears what it had. Signing out clears
// every draft, and deleting a chat clears its own. At most `MAX_DRAFTS` are kept (the oldest
// go), of at most `MAX_DRAFT_CHARS` each.

export const MAX_DRAFTS = 200
export const MAX_DRAFT_CHARS = 100_000

/** A file attached to a draft, as the upload route answered it. */
export interface DraftAttachment { path: string; name: string; size: number; mime: string }
/** A file, knowledge item or artifact a draft mentions, as the composer holds it. */
export interface DraftMention { target: string; ref: string; label: string }
/** A block pasted into a draft, which its text names by a `[Paste #n]` marker. */
export interface DraftPaste { seq: number; lines: number; content: string }

export interface Draft {
  text: string
  attachments: DraftAttachment[]
  mentions: DraftMention[]
  /** The blocks its markers name: a draft kept without them would send markers with nothing
   *  behind them. */
  pastes: DraftPaste[]
  /** When it was last written, in ms since the epoch. */
  updated_at: number
}

export type DraftContent = Omit<Draft, 'updated_at'>

/** Whether *d* holds anything worth keeping. */
export function hasContent(d: Partial<DraftContent> | null | undefined): boolean {
  return !!d && (!!d.text?.trim() || !!d.attachments?.length || !!d.mentions?.length)
}

function wellFormed(raw: unknown): Draft | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Partial<Draft>
  if (typeof r.text !== 'string') return null
  return {
    text: r.text.slice(0, MAX_DRAFT_CHARS),
    attachments: Array.isArray(r.attachments) ? r.attachments.filter((a) => a && typeof a.path === 'string') : [],
    mentions: Array.isArray(r.mentions) ? r.mentions.filter((m) => m && typeof m.ref === 'string') : [],
    pastes: Array.isArray(r.pastes) ? r.pastes.filter((b) => b && typeof b.seq === 'number' && typeof b.content === 'string') : [],
    updated_at: typeof r.updated_at === 'number' ? r.updated_at : 0,
  }
}

/** The draft kept for *session*, or `null`. */
export async function loadDraft(session: string): Promise<Draft | null> {
  if (!session) return null
  return wellFormed(await browserStore().get<Draft>('drafts', session))
}

/** Keep *content* as *session*'s draft; one with nothing in it clears the draft. Past
 *  `MAX_DRAFTS`, the drafts last written longest ago go. */
export async function saveDraft(session: string, content: DraftContent, now = Date.now()): Promise<void> {
  if (!session) return
  if (!hasContent(content)) return clearDraft(session)
  const store = browserStore()
  await store.put<Draft>('drafts', session, {
    text: content.text.slice(0, MAX_DRAFT_CHARS),
    attachments: content.attachments,
    mentions: content.mentions,
    pastes: content.pastes.map((b) => ({ ...b, content: b.content.slice(0, MAX_DRAFT_CHARS) })),
    updated_at: now,
  })
  const kept = await store.entries<Draft>('drafts')
  if (kept.length > MAX_DRAFTS) {
    const oldest = kept.sort(([, a], [, b]) => (a?.updated_at ?? 0) - (b?.updated_at ?? 0))
    for (const [key] of oldest.slice(0, kept.length - MAX_DRAFTS)) await store.delete('drafts', key)
  }
}

export async function clearDraft(session: string): Promise<void> {
  if (session) await browserStore().delete('drafts', session)
}

/** Every draft goes: on sign-out. */
export async function clearAllDrafts(): Promise<void> {
  await browserStore().clear('drafts')
}

/** The chats with a draft kept, for the chat list's "Draft". */
export async function draftSessions(): Promise<Set<string>> {
  const kept = await browserStore().entries<Draft>('drafts')
  return new Set(kept.filter(([, d]) => hasContent(d)).map(([key]) => key))
}
