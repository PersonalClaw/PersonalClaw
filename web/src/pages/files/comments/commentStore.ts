import { useSyncExternalStore } from 'react'

/** A single comment anchored to a passage of a file/artifact preview. Comments
 *  collect across ALL documents (files + artifacts) into one cross-document deck
 *  that surfaces at the bottom of whichever preview is open. */
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
  ts: number
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

const KEY = 'doc-comments-v1'

function load(): DocComment[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) { const v = JSON.parse(raw); if (Array.isArray(v)) return v }
  } catch { /* ignore */ }
  return []
}

let comments: DocComment[] = load()
const listeners = new Set<() => void>()

function emit() {
  try { localStorage.setItem(KEY, JSON.stringify(comments)) } catch { /* ignore */ }
  listeners.forEach((l) => l())
}

let _seq = 0
function newId(): string {
  // Date/random are fine in the browser (this isn't a workflow script).
  _seq += 1
  return `c-${Date.now().toString(36)}-${_seq}`
}

export const commentStore = {
  all(): DocComment[] { return comments },
  add(c: Omit<DocComment, 'id' | 'ts'>): DocComment {
    const full: DocComment = { ...c, id: newId(), ts: Date.now() }
    comments = [...comments, full]
    emit()
    return full
  },
  update(id: string, patch: Partial<Pick<DocComment, 'comment'>>) {
    comments = comments.map((c) => (c.id === id ? { ...c, ...patch } : c))
    emit()
  },
  remove(id: string) {
    comments = comments.filter((c) => c.id !== id)
    emit()
  },
  removeMany(ids: string[]) {
    const set = new Set(ids)
    comments = comments.filter((c) => !set.has(c.id))
    emit()
  },
  clear() { comments = []; emit() },
  subscribe(fn: () => void): () => void {
    listeners.add(fn)
    return () => { listeners.delete(fn) }
  },
}

/** React hook — re-renders on any change to the cross-document comment list. */
export function useComments(): DocComment[] {
  return useSyncExternalStore(commentStore.subscribe, commentStore.all, commentStore.all)
}
