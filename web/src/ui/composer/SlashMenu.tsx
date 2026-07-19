import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Slash } from 'lucide-react'
import { api } from '../../lib/api'

interface Cmd { name: string; description: string }

// Slash commands are a fixed backend set — fetch once and reuse for the session.
let _cache: Cmd[] | null = null
let _inflight: Promise<Cmd[]> | null = null
function loadCommands(): Promise<Cmd[]> {
  if (_cache) return Promise.resolve(_cache)
  if (!_inflight) _inflight = api.slashCommands().then((c) => { _cache = c; return c }).catch(() => { _inflight = null; return [] })
  return _inflight
}

/** `/`-command menu — anchored over the composer, mirrors MentionMenu. Opens when
 *  the input starts with `/word` (commands only run as the FIRST token, matching
 *  the backend's is_slash check). ↑/↓ navigate, Enter/Tab select, Esc closes.
 *  onSelect replaces the input with the chosen command (+ trailing space).
 *  Portaled to body so it floats above everything. */
export function SlashMenu({ query, anchorRef, open, onSelect, onClose }: {
  query: string
  anchorRef: React.RefObject<HTMLElement | null>
  open: boolean
  onSelect: (command: string) => void
  onClose: () => void
}) {
  const [all, setAll] = useState<Cmd[]>(_cache ?? [])
  const [sel, setSel] = useState(0)
  const menuRef = useRef<HTMLDivElement>(null)
  const selItemRef = useRef<HTMLButtonElement>(null)

  useEffect(() => { if (open) loadCommands().then(setAll) }, [open])

  const q = query.toLowerCase()
  const results = q ? all.filter((c) => c.name.slice(1).toLowerCase().startsWith(q) || c.description.toLowerCase().includes(q)) : all
  const resultsRef = useRef<Cmd[]>(results); resultsRef.current = results
  const selRef = useRef(sel); selRef.current = sel
  useEffect(() => { setSel(0) }, [query])

  const choose = useCallback((i: number) => {
    const c = resultsRef.current[i]
    if (c) onSelect(c.name)
  }, [onSelect])

  useEffect(() => { selItemRef.current?.scrollIntoView({ block: 'nearest' }) }, [sel])

  // capture-phase key handling so it wins over the editor while open.
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

  // dismiss on click outside the menu AND outside the composer.
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

  if (!open || !anchorRef.current || results.length === 0) return null
  const rect = anchorRef.current.getBoundingClientRect()
  const w = Math.min(Math.max(rect.width, 280), 460)
  const maxH = 320
  const roomAbove = rect.top - 8
  const dropBelow = roomAbove < 160
  const pos: React.CSSProperties = dropBelow
    ? { top: rect.bottom + 8, maxHeight: Math.min(maxH, window.innerHeight - rect.bottom - 16) }
    : { bottom: window.innerHeight - rect.top + 8, maxHeight: Math.min(maxH, roomAbove) }

  return createPortal(
    <div ref={menuRef} role="listbox" aria-label="Slash commands"
      className="fixed z-[9999] overflow-y-auto rounded-lg border border-outline-variant/50 bg-surface/95 p-1 shadow-xl ring-1 ring-black/5 backdrop-blur-md"
      style={{ left: rect.left, width: w, ...pos }}>
      {results.map((c, i) => (
        <button key={c.name} type="button" role="option" aria-selected={i === sel} title={c.description}
          ref={i === sel ? selItemRef : undefined}
          onMouseEnter={() => setSel(i)}
          onMouseDown={(e) => { e.preventDefault(); choose(i) }}
          className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors"
          style={i === sel ? { background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' } : undefined}>
          <Slash size={13} className={`shrink-0 ${i === sel ? 'text-primary' : 'text-on-surface-low'}`} />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-mono text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 500' }}>{c.name}</span>
            {c.description && <span className="block truncate text-on-surface-low text-[0.7rem]">{c.description}</span>}
          </span>
        </button>
      ))}
    </div>,
    document.body,
  )
}
