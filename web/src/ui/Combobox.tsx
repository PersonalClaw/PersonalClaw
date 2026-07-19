import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ChevronDown, Search, Check, X } from 'lucide-react'
import { spring, bounce } from '../design/motion'

export interface ComboOption { value: string; label: string; group?: string; description?: string }

/** Searchable single-select autocomplete. Type to filter; arrow keys + Enter to
 *  pick; options optionally grouped by `group`. A reusable building block for
 *  any "pick one from many" field (agents, models, …).
 *
 *  Redesign-v2 (per user direction, 2026-07-05): the field MORPHS INTO the menu
 *  as ONE continuous surface (Motion `layout` container-transform) rather than a
 *  separate menu opening below a still-distinct trigger. The single surface grows
 *  in place — pushing the content below it down — and its corner radius eases from
 *  the field radius to the menu radius during the morph; the collapsed value and
 *  the open search-header crossfade. Selecting an option or moving focus away
 *  collapses the same surface and the page settles back. §Goal 4 ("morph, don't
 *  mount") + the researched container-transform pattern. */
export function Combobox({ options, value, onChange, placeholder = 'Select…', emptyText = 'No matches' }: {
  options: ComboOption[]
  value: string
  onChange: (v: string) => void
  placeholder?: string
  emptyText?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [active, setActive] = useState(0)
  // Per-instance layoutId so two open Comboboxes don't share (and fling) one
  // active-row indicator between them.
  const activeLayoutId = `combo-active-${useId()}`
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const selected = options.find((o) => o.value === value)

  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase()
    return n ? options.filter((o) => `${o.label} ${o.group ?? ''} ${o.description ?? ''}`.toLowerCase().includes(n)) : options
  }, [options, q])

  // group for display, preserving first-seen group order
  const groups = useMemo(() => {
    const by = new Map<string, ComboOption[]>()
    for (const o of filtered) { const g = o.group ?? ''; const a = by.get(g) ?? []; a.push(o); by.set(g, a) }
    return [...by.entries()]
  }, [filtered])

  // Outside-click still closes (covers clicks on non-focusable page chrome).
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  useEffect(() => {
    if (!open) return
    setQ(''); setActive(0)
    setTimeout(() => inputRef.current?.focus(), 0)
  }, [open])

  // Focus leaving the whole control collapses it (user direction: "when the user
  // selects or looses the focus from the combo box, it should collapse again").
  // relatedTarget staying inside root (input → a row) must NOT close it, so we
  // guard on containment; a null relatedTarget (click to blank) also collapses.
  function onBlurCapture(e: React.FocusEvent) {
    const next = e.relatedTarget as Node | null
    if (next && rootRef.current?.contains(next)) return
    setOpen(false)
  }

  function pick(v: string) { onChange(v); setOpen(false) }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, filtered.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (filtered[active]) pick(filtered[active].value) }
    else if (e.key === 'Escape') { setOpen(false) }
  }

  // flat index for active-row highlighting across groups
  let flatIdx = -1

  return (
    // rootRef spans the whole control so outside-click / focus-leave are measured
    // against the ONE morphing surface below it.
    <div ref={rootRef} onBlurCapture={onBlurCapture}>
      {/* THE surface. A single motion.div with `layout` that IS the field when
          collapsed and the menu when open — it grows in place (pushing siblings
          down) and its corner radius eases field→menu during the size morph.
          borderRadius is set via style so Motion scale-corrects it; the surface
          tint also shifts container→high as it becomes a menu. */}
      <motion.div
        layout
        transition={spring.spatialDefault}
        style={{ borderRadius: open ? 'var(--radius-lg)' : 'var(--radius-md)', overflow: 'hidden' }}
        className={open ? 'bg-surface-high shadow-menu' : 'bg-surface-container'}
      >
        {open ? (
          // ── Expanded: the same surface now hosts a search header + option list.
          //    layout on this inner wrapper keeps it from stretching as the
          //    surface scales; it fades in just after the shape starts changing.
          <motion.div layout="position" initial={{ opacity: 0 }} animate={{ opacity: 1, transition: { delay: 0.04 } }}>
            <div className="relative p-2">
              <Search size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-on-surface-low pointer-events-none" />
              <input ref={inputRef} value={q} onChange={(e) => { setQ(e.target.value); setActive(0) }} onKeyDown={onKey}
                placeholder="Search…" className="w-full h-8 rounded-md bg-surface pl-8 pr-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none" />
            </div>
            <div className="max-h-64 overflow-y-auto pb-1">
              {filtered.length === 0 ? <div className="px-3 py-3 text-on-surface-low text-[0.8125rem]">{emptyText}</div> : groups.map(([group, opts]) => (
                <div key={group}>
                  {group && <div className="px-3 pt-2 pb-1 text-on-surface-low text-[0.65rem] uppercase tracking-wide">{group}</div>}
                  {opts.map((o) => {
                    flatIdx++
                    const idx = flatIdx
                    const sel = o.value === value
                    const isActive = idx === active
                    return (
                      <button key={o.value} type="button" onMouseEnter={() => setActive(idx)} onClick={() => pick(o.value)}
                        className="relative flex w-full items-center gap-s px-3 py-1.5 text-left">
                        {/* liquid active-row highlight — a single shared element that
                            SLIDES between rows via layoutId (the Segmented pattern
                            applied to a list), so keyboard/hover navigation glides
                            instead of blink-swapping the background. */}
                        {isActive && (
                          <motion.span layoutId={activeLayoutId} transition={spring.spatialFast}
                            className="absolute inset-x-1 inset-y-0.5 rounded-md bg-primary/15" />
                        )}
                        <span className="relative flex-1 min-w-0">
                          <span className="block truncate text-on-surface text-[0.875rem]">{o.label}</span>
                          {o.description && <span className="block truncate text-on-surface-low text-[0.7rem]">{o.description}</span>}
                        </span>
                        {sel && <Check size={14} className="relative shrink-0 text-primary" />}
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          </motion.div>
        ) : (
          // ── Collapsed: the surface is the field row. layout="position" so it
          //    doesn't stretch during the morph; it's the container-transform's
          //    "outgoing" content (fades quickly as the shape opens).
          <motion.button layout="position" type="button" onClick={() => setOpen(true)} data-type="title-m"
            className="flex w-full items-center gap-s h-10 px-m text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/50">
            <span className={`flex-1 truncate ${selected ? 'text-on-surface' : 'text-on-surface-low'}`}>{selected ? selected.label : placeholder}</span>
            {selected && <span role="button" tabIndex={-1} aria-label="Clear selection" className="shrink-0 text-on-surface-low hover:text-on-surface" onClick={(e) => { e.stopPropagation(); onChange('') }}><X size={14} /></span>}
            <motion.span className="shrink-0 text-on-surface-low" animate={{ rotate: open ? 180 : 0 }} transition={bounce.subtle}>
              <ChevronDown size={16} />
            </motion.span>
          </motion.button>
        )}
      </motion.div>
    </div>
  )
}
