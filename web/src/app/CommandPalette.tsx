import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Search, CornerDownLeft, ArrowRight } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { spring } from '../design/motion'

export interface Command {
  id: string
  label: string
  hint?: string         // section / context (e.g. "Go to", "Action")
  icon: LucideIcon
  keywords?: string     // extra search terms
  run: () => void
}

/** ⌘K / Ctrl+K command palette — search + run navigation and actions. The single
 *  keyboard entry point (designed for THIS featureset: 16 destinations + global
 *  actions, no per-page chord soup). Opens on ⌘K, closes on Esc, arrows to move,
 *  Enter to run. Mounted once at the app shell. */
export function CommandPalette({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // global ⌘K / Ctrl+K toggle
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen((v) => !v); setQ(''); setActive(0) }
      else if (e.key === 'Escape' && open) setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 0) }, [open])

  const results = useMemo(() => {
    const n = q.trim().toLowerCase()
    if (!n) return commands
    // simple subsequence/substring score: label match > keyword match
    return commands
      .map((c) => {
        const hay = `${c.label} ${c.hint ?? ''} ${c.keywords ?? ''}`.toLowerCase()
        const li = c.label.toLowerCase().indexOf(n)
        const score = li === 0 ? 3 : li > 0 ? 2 : hay.includes(n) ? 1 : 0
        return { c, score }
      })
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.c)
  }, [q, commands])

  useEffect(() => { setActive(0) }, [q])

  // keep the highlighted row visible as arrows move the selection
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-idx="${active}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const run = (c?: Command) => { if (!c) return; setOpen(false); c.run() }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, results.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); run(results[active]) }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          className="fixed inset-0 z-[200] flex items-start justify-center px-l pt-[14vh]"
          onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" />
          <motion.div
            initial={{ opacity: 0, y: -10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -10, scale: 0.98 }}
            transition={spring.spatialFast}
            className="relative w-full overflow-hidden rounded-2xl border border-outline/40 bg-surface-container shadow-2xl"
            style={{ maxWidth: 560, borderRadius: 'var(--radius-xl)' }}
            onClick={(e) => e.stopPropagation()}>
            {/* search row */}
            <div className="flex items-center gap-s border-b border-outline-variant/40 px-l h-14">
              <Search size={17} className="shrink-0 text-on-surface-low" />
              <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKeyDown}
                placeholder="Search pages and actions…"
                className="min-w-0 flex-1 bg-transparent text-on-surface text-[0.9375rem] placeholder:text-on-surface-low outline-none" />
              <kbd className="rounded-md bg-surface-high px-1.5 py-0.5 font-mono text-on-surface-low text-[0.7rem]">esc</kbd>
            </div>
            {/* results */}
            <div ref={listRef} className="max-h-[50vh] overflow-y-auto py-1.5">
              {results.length === 0 ? (
                <div className="px-l py-6 text-center text-on-surface-low text-[0.875rem]">No matches for “{q}”.</div>
              ) : results.map((c, i) => {
                const on = i === active
                const Icon = c.icon
                return (
                  <button key={c.id} type="button" data-cmd-idx={i} onMouseEnter={() => setActive(i)} onClick={() => run(c)}
                    className="flex w-full items-center gap-3 px-l py-2.5 text-left"
                    style={{ background: on ? 'var(--color-surface-high)' : undefined }}>
                    <Icon size={16} className="shrink-0" style={{ color: on ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }} />
                    <span className="flex-1 truncate text-on-surface text-[0.875rem]">{c.label}</span>
                    {c.hint && <span className="shrink-0 text-on-surface-low text-[0.7rem]">{c.hint}</span>}
                    {on && <CornerDownLeft size={13} className="shrink-0 text-on-surface-low" />}
                  </button>
                )
              })}
            </div>
            {/* footer hint */}
            <div className="flex items-center gap-3 border-t border-outline-variant/40 px-l py-2 text-on-surface-low text-[0.7rem]">
              <span className="inline-flex items-center gap-1"><ArrowRight size={11} className="rotate-90" /> navigate</span>
              <span className="inline-flex items-center gap-1"><CornerDownLeft size={11} /> select</span>
              <span className="ml-auto inline-flex items-center gap-1"><kbd className="rounded bg-surface-high px-1 font-mono">⌘K</kbd> toggle</span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
