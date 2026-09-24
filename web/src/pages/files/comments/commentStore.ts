import { useSyncExternalStore } from 'react'
import { api, type WireDocComment } from '../../../lib/api'
import { notify } from '../../../app/appSdk'

/** A single comment anchored to a passage of a file/artifact preview. Comments
 *  collect across ALL documents (files + artifacts) into one cross-document deck
 *  that surfaces at the bottom of whichever preview is open.
 *
 *  🔴 THE STORE BEHIND THIS IS THE SERVER (`/api/doc-comments`, #429). It used to be one
 *  `localStorage` key — `doc-comments-v1` — and nothing else, which made annotations the
 *  only thing a user creates in this app that a cleared cache destroys, that
 *  `personalclaw snapshot` cannot carry (the server never saw them, so they were absent
 *  from the durability inventory too), and that are invisible on a second device or in the
 *  desktop app. Task comments next door were a real server-side store the whole time, so
 *  the two comment systems had opposite durability guarantees and nothing said which one
 *  you were using.
 *
 *  The module-level list below is a RENDER CACHE of server state, not a second copy of
 *  record: every verb writes through, and a write that fails resyncs from the server and
 *  says so. That distinction is the whole point — the old `try/catch { ignore }` around
 *  both the read and the write was appropriate for a cache and wrong for the only copy of
 *  user-authored content. */
export interface DocComment {
  id: string
  docId: string        // file path or artifact slug — the anchor document
  docLabel: string     // human label (filename / artifact name) for the card
  docPath?: string      // the path/identifier to attach as chat file context
  quote: string        // the selected passage being commented on
  comment: string      // the user's comment
  // 1-based source location of the quote, when resolvable — lets the AI find the
  // exact occurrence (ported from legacy CommentOverlay.findCoords).
  line?: number
  column?: number
  // a short source-context snippet captured at comment time (~20 chars each side
  // of the quote) so the AI can disambiguate short/repeated anchors.
  context?: string
  ts: number           // epoch MILLISECONDS (the wire carries seconds; see `fromWire`)
}

/** Resolve a selected passage to a 1-based (line, column) in the source content.
 *  Ported from legacy `MarkdownPanel.findCoords`. */
export function findCoords(content: string, selected: string): { line: number; column: number } | undefined {
  if (!selected) return undefined
  const idx = content.indexOf(selected)
  if (idx < 0) return undefined
  const before = content.slice(0, idx)
  const nl = before.lastIndexOf('\n')
  const line = (before.match(/\n/g)?.length ?? 0) + 1
  const column = (nl < 0 ? idx : idx - nl - 1) + 1
  return { line, column }
}

/** Capture a short source-context snippet (~20 chars each side of the quote) on
 *  the quote's line, for disambiguating short/repeated anchors. */
export function captureContext(content: string, quote: string, line?: number, column?: number): string | undefined {
  if (!content || line == null || column == null) return undefined
  const lines = content.split('\n')
  if (line < 1 || line > lines.length) return undefined
  const ln = lines[line - 1]
  const start = Math.max(0, column - 1 - 20)
  const end = Math.min(ln.length, column - 1 + quote.length + 20)
  return `${start > 0 ? '…' : ''}${ln.slice(start, end)}${end < ln.length ? '…' : ''}`
}

/** Build the structured feedback message handed to the AI — one block per
 *  referenced document, each comment with its location + captured context so the
 *  agent can resolve the exact occurrence. Adapted from legacy
 *  `CommentOverlay.formatCommentsMessage`. */
export function formatCommentsMessage(comments: DocComment[], instructions: string): string {
  const esc = (s: string) => s.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
  const byDoc = new Map<string, DocComment[]>()
  for (const c of comments) { const arr = byDoc.get(c.docId) ?? []; arr.push(c); byDoc.set(c.docId, arr) }
  const out: string[] = []
  if (instructions) out.push(instructions, '')
  for (const [, list] of byDoc) {
    const label = list[0].docLabel || list[0].docId
    out.push(`[Document feedback on ${label} — ${list.length} comment${list.length === 1 ? '' : 's'}]`, '')
    list.forEach((c, i) => {
      const anchor = c.quote.length > 80 ? c.quote.slice(0, 80) + '…' : c.quote
      const loc = c.line != null ? (c.column != null ? `line ${c.line}, col ${c.column}, ` : `line ${c.line}, `) : ''
      const ctx = c.context ? ` in "${esc(c.context)}"` : ''
      out.push(`${i + 1}. (${loc}"${esc(anchor)}"${ctx}): "${esc(c.comment)}"`)
    })
    out.push('')
  }
  return out.join('\n').trim()
}

/** The server's row → the deck's record. `ts` crosses a UNIT boundary here: the wire
 *  carries `time.time()` seconds, this interface has always been milliseconds, and the
 *  field feeds ordering, so multiplying at the one seam beats auditing every reader. */
function fromWire(w: WireDocComment): DocComment {
  return {
    id: w.id,
    docId: w.doc_id,
    docLabel: w.doc_label || '',
    docPath: w.doc_path || undefined,
    quote: w.quote || '',
    comment: w.comment || '',
    line: w.line ?? undefined,
    column: w.column ?? undefined,
    context: w.context || undefined,
    ts: (w.ts || 0) * 1000,
  }
}

function toWire(c: Omit<DocComment, 'id' | 'ts'>): Omit<WireDocComment, 'id' | 'ts'> {
  return {
    doc_id: c.docId,
    doc_label: c.docLabel ?? '',
    doc_path: c.docPath ?? '',
    quote: c.quote ?? '',
    comment: c.comment ?? '',
    line: c.line ?? null,
    column: c.column ?? null,
    context: c.context ?? '',
  }
}

let comments: DocComment[] = []
const listeners = new Set<() => void>()
let hydrated = false

function emit() { listeners.forEach((l) => l()) }

/** Re-read the server's list. The recovery path for a failed write: the deck must not keep
 *  showing an optimistic row the server rejected, which is the shape that made the old
 *  swallowed write invisible. */
async function resync(): Promise<void> {
  const { comments: rows } = await api.docCommentsList()
  comments = rows.map(fromWire)
  emit()
}

/** Report a failed write AND put the deck back on the server's truth. Both halves matter:
 *  the toast is what tells the user their note did not land, and the resync is what stops
 *  the UI from implying it did. */
async function failed(what: string, e: unknown): Promise<void> {
  notify(`Couldn't ${what}: ${e instanceof Error ? e.message : String(e)}`, 'error')
  try { await resync() } catch { /* the notify above already reported the write */ }
}

/** First read populates the deck. Kicked from `subscribe`, so a surface that never mounts
 *  the comment layer never fetches. */
function hydrate(): void {
  if (hydrated) return
  hydrated = true
  void resync().catch((e) => {
    notify(`Couldn't load document comments: ${e instanceof Error ? e.message : String(e)}`, 'error')
  })
}

export const commentStore = {
  all(): DocComment[] { return comments },
  /** Optimistic: the card appears immediately, then takes the server's id. A rejected write
   *  removes it again and says why — it does not linger looking saved. */
  async add(c: Omit<DocComment, 'id' | 'ts'>): Promise<DocComment | null> {
    const optimistic: DocComment = { ...c, id: `pending-${Date.now()}`, ts: Date.now() }
    comments = [...comments, optimistic]
    emit()
    try {
      const { comment } = await api.docCommentCreate(toWire(c))
      const saved = fromWire(comment)
      comments = comments.map((row) => (row.id === optimistic.id ? saved : row))
      emit()
      return saved
    } catch (e) {
      comments = comments.filter((row) => row.id !== optimistic.id)
      emit()
      await failed('save that comment', e)
      return null
    }
  },
  async update(id: string, patch: Partial<Pick<DocComment, 'comment'>>): Promise<void> {
    const before = comments
    comments = comments.map((c) => (c.id === id ? { ...c, ...patch } : c))
    emit()
    try {
      await api.docCommentUpdate(id, patch.comment ?? '')
    } catch (e) {
      comments = before
      emit()
      await failed('save that edit', e)
    }
  },
  async remove(id: string): Promise<void> {
    const before = comments
    comments = comments.filter((c) => c.id !== id)
    emit()
    try {
      await api.docCommentDelete(id)
    } catch (e) {
      comments = before
      emit()
      await failed('remove that comment', e)
    }
  },
  async removeMany(ids: string[]): Promise<void> {
    if (!ids.length) return
    const before = comments
    const set = new Set(ids)
    comments = comments.filter((c) => !set.has(c.id))
    emit()
    try {
      await api.docCommentsDeleteMany(ids)
    } catch (e) {
      comments = before
      emit()
      await failed('remove those comments', e)
    }
  },
  async clear(): Promise<void> {
    const before = comments
    comments = []
    emit()
    try {
      await api.docCommentsClear()
    } catch (e) {
      comments = before
      emit()
      await failed('clear the comments', e)
    }
  },
  subscribe(fn: () => void): () => void {
    listeners.add(fn)
    hydrate()
    return () => { listeners.delete(fn) }
  },
}

/** React hook — re-renders on any change to the cross-document comment list. */
export function useComments(): DocComment[] {
  return useSyncExternalStore(commentStore.subscribe, commentStore.all, commentStore.all)
}
