import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { FileText, BookText, ScrollText, Loader2 } from 'lucide-react'
import { api } from '../../lib/api'

interface FileResult { path: string; name: string; size: number; mtime: number }

/** A menu row is a workspace file, a knowledge-library item, or a user prompt.
 *  Files thread a path into meta.files (the agent reads them); knowledge threads
 *  an id into meta.knowledge (the backend inlines the item's content); a prompt
 *  is a plain `@name` reference the backend expands at send (chat_runner's
 *  _expand_prompt_mention) — leading-only, so prompts surface only when the `@`
 *  is at the message start. */
export type MentionPick =
  | { kind: 'file'; path: string; name: string }
  | { kind: 'knowledge'; id: string; name: string }
  | { kind: 'prompt'; name: string }
interface Row { kind: 'file' | 'knowledge' | 'prompt'; id: string; name: string; sub: string; size?: number }

function fmtSize(b: number): string {
  if (b < 1024) return b + 'B'
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + 'KB'
  return (b / (1024 * 1024)).toFixed(1) + 'MB'
}

// Module-level query→results cache with a short TTL, so repeated/▸retyped queries
// (`@flo`→`@floo`→back to `@flo`) don't re-hit the backend each keystroke. Entries
// expire after CACHE_TTL so newly-created files still surface within a cadence.
const CACHE_TTL = 30_000
const searchCache = new Map<string, { ts: number; rows: Row[] }>()
async function cachedSearch(query: string, project?: string, leading?: boolean): Promise<Row[]> {
  // `leading` is part of the key: prompts only join the set when the `@` is at the
  // message start (where chat_runner expands `@name`), so leading vs mid-text must
  // not share a cache entry.
  const key = `${leading ? 'L' : ''} ${project || ''} ${query.toLowerCase()}`
  const hit = searchCache.get(key)
  const now = performance.now()
  if (hit && now - hit.ts < CACHE_TTL) return hit.rows
  const ql = query.toLowerCase()
  // Files + knowledge in parallel; either failing degrades to the other. Prompts are
  // added ONLY for a leading `@` (backend expands leading-only) — matched client-side
  // over the user-prompt list (same source PromptPalette uses).
  const [files, knowledge, prompts] = await Promise.all([
    api.fileSearch(query, project).then((d) => d.results || []).catch(() => [] as FileResult[]),
    api.knowledgeItems({ q: query, limit: 6 }).then((d) => d.items || []).catch(() => []),
    leading
      ? api.prompts('user')
          .then((items) => items.filter((p) => `${p.name} ${p.title ?? ''}`.toLowerCase().includes(ql)).slice(0, 6))
          .catch(() => [])
      : Promise.resolve([]),
  ])
  const rows: Row[] = [
    ...prompts.map((p): Row => ({ kind: 'prompt', id: p.name, name: p.name, sub: p.title || p.description || 'prompt' })),
    ...files.map((f): Row => ({ kind: 'file', id: f.path, name: f.name, sub: f.path, size: f.size })),
    ...knowledge.map((k): Row => ({ kind: 'knowledge', id: k.id, name: k.title || 'Untitled', sub: k.item_type || 'knowledge' })),
  ]
  searchCache.set(key, { ts: now, rows })
  // bound the cache so it can't grow unbounded across a long session.
  if (searchCache.size > 80) searchCache.delete(searchCache.keys().next().value as string)
  return rows
}

/** `@`-mention file picker — anchored over the composer. Typing `@query` opens
 *  it; debounced fuzzy filename search (api.fileSearch). ↑/↓ navigate, Enter/Tab
 *  select, Esc closes. Calls onSelect with the chosen file's path + display name.
 *  NE-styled; portaled to body so it floats above everything. */
export function MentionMenu({ query, anchorRef, open, project, leading, onSelect, onClose }: {
  query: string
  anchorRef: React.RefObject<HTMLElement | null>
  open: boolean
  project?: string
  /** the `@` sits at the message start → also suggest prompts (chat_runner expands
   *  `@name` leading-only). */
  leading?: boolean
  onSelect: (pick: MentionPick) => void
  onClose: () => void
}) {
  const [results, setResults] = useState<Row[]>([])
  const [sel, setSel] = useState(0)
  const [loading, setLoading] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const selItemRef = useRef<HTMLButtonElement>(null)
  const resultsRef = useRef<Row[]>([])
  const selRef = useRef(0)
  resultsRef.current = results
  selRef.current = sel

  useEffect(() => {
    if (!open || query.length < 2) { setResults([]); setLoading(false); return }
    // cache hit → render instantly (no spinner, no debounce); miss → debounce.
    const key = `${leading ? 'L' : ''} ${project || ''} ${query.toLowerCase()}`
    const hit = searchCache.get(key)
    if (hit && performance.now() - hit.ts < CACHE_TTL) { setResults(hit.rows); setSel(0); setLoading(false); return }
    setLoading(true)
    const t = setTimeout(() => {
      cachedSearch(query, project, leading)
        .then((rows) => { setResults(rows); setSel(0) })
        .catch(() => setResults([]))
        .finally(() => setLoading(false))
    }, 180)
    return () => clearTimeout(t)
  }, [query, open, project, leading])

  const choose = useCallback((i: number) => {
    const r = resultsRef.current[i]
    if (!r) return
    onSelect(
      r.kind === 'file' ? { kind: 'file', path: r.id, name: r.name }
      : r.kind === 'prompt' ? { kind: 'prompt', name: r.name }
      : { kind: 'knowledge', id: r.id, name: r.name },
    )
  }, [onSelect])

  // keep the selected item in view as ↑/↓ move past the visible window.
  useEffect(() => {
    selItemRef.current?.scrollIntoView({ block: 'nearest' })
  }, [sel])

  // capture-phase key handling so it wins over the textarea while open.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const r = resultsRef.current
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); return }
      if (r.length === 0) return
      if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); setSel((i) => (i + 1) % r.length) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); setSel((i) => (i - 1 + r.length) % r.length) }
      else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); choose(selRef.current) }
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [open, onClose, choose])

  // dismiss on click/focus outside the menu AND outside the composer textarea.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (menuRef.current?.contains(t) || anchorRef.current?.contains(t)) return
      onClose()
    }
    document.addEventListener('mousedown', onDown, true)
    return () => document.removeEventListener('mousedown', onDown, true)
  }, [open, onClose, anchorRef])

  if (!open || !anchorRef.current) return null
  const rect = anchorRef.current.getBoundingClientRect()
  const w = Math.min(Math.max(rect.width, 280), 460)
  const maxH = 300
  // Anchor the menu's BOTTOM just above the composer so it HUGS it and grows
  // upward — whether it's a one-line hint or a full result list. (Pinning `top`
  // from a fixed maxH left a gap below short content.) If there isn't room above,
  // drop below the composer instead (top-anchored).
  const roomAbove = rect.top - 8
  const dropBelow = roomAbove < 140
  const pos: React.CSSProperties = dropBelow
    ? { top: rect.bottom + 8, maxHeight: Math.min(maxH, window.innerHeight - rect.bottom - 16) }
    : { bottom: window.innerHeight - rect.top + 8, maxHeight: Math.min(maxH, roomAbove) }

  return createPortal(
    <div ref={menuRef} role="listbox"
      className="fixed z-[9999] overflow-y-auto rounded-lg border border-outline-variant/50 bg-surface/95 p-1 shadow-xl ring-1 ring-black/5 backdrop-blur-md"
      style={{ left: rect.left, width: w, ...pos }}>
      {query.length < 2 ? (
        <Hint>Type 2+ characters to search {leading ? 'prompts, files & knowledge' : 'files & knowledge'}…</Hint>
      ) : loading && results.length === 0 ? (
        <Hint><Loader2 size={12} className="mr-1.5 inline animate-spin" />Searching…</Hint>
      ) : results.length === 0 ? (
        <Hint>No matching {leading ? 'prompts, files or knowledge' : 'files or knowledge'}</Hint>
      ) : results.map((r, i) => {
        const Icon = r.kind === 'knowledge' ? BookText : r.kind === 'prompt' ? ScrollText : FileText
        return (
          <button key={`${r.kind}:${r.id}`} type="button" role="option" aria-selected={i === sel} title={r.sub}
            ref={i === sel ? selItemRef : undefined}
            onMouseEnter={() => setSel(i)}
            onMouseDown={(e) => { e.preventDefault(); choose(i) }}
            className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors"
            style={i === sel ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined}>
            <Icon size={13} className={`shrink-0 ${i === sel ? 'text-primary' : 'text-on-surface-low'}`} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }}>{r.name}</span>
              <span className="block truncate text-on-surface-low text-[0.7rem]">{r.kind === 'knowledge' ? `knowledge · ${r.sub}` : r.kind === 'prompt' ? `prompt · ${r.sub}` : r.sub}</span>
            </span>
            {r.size !== undefined && <span className="shrink-0 font-mono text-on-surface-low text-[0.65rem] tabular-nums">{fmtSize(r.size)}</span>}
          </button>
        )
      })}
    </div>,
    document.body,
  )
}

function Hint({ children }: { children: React.ReactNode }) {
  return <div className="px-3 py-3 text-center text-on-surface-low text-[0.75rem]">{children}</div>
}
