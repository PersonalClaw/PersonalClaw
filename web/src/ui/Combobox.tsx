import { useEffect, useId, useMemo, useRef, useState, useCallback } from 'react'
import { motion } from 'framer-motion'
import { ChevronDown, Search, Check, X } from 'lucide-react'
import { spring, physics } from '../design/motion'

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

  // 🪤 `groups` re-orders: it buckets `filtered` by group, so an options list whose groups interleave
  // (A/g1, B/g2, C/g1) RENDERS as A, C, B while `filtered` still reads A, B, C. The highlight indexes
  // the rendered order and Enter used to index `filtered`, so the two could point at different rows —
  // latent today because every caller happens to ship contiguous groups, and untrue the moment one
  // does not. `flat` is the rendered order, and everything below indexes it: the highlight, the
  // `aria-activedescendant` this control now publishes, and the Enter that commits.
  const flat = useMemo(() => groups.flatMap(([, opts]) => opts), [groups])

  // Ids for the listbox relationship. `aria-activedescendant` needs a stable id per rendered row.
  const listId = `combo-list-${useId()}`
  const optId = useCallback((i: number) => `${listId}-opt-${i}`, [listId])

  // 🔴 The arrow cursor moved without the list following it. Measured on `#/triggers/new` with 19
  // options in a `max-h-64` scroller: the active row was OUT OF VIEW from index 12 on and `scrollTop`
  // stayed 0, so arrowing past the first screenful moved an invisible highlight — for sighted keyboard
  // users as much as for the `aria-activedescendant` this control now publishes, which must point at
  // something on screen. `block: 'nearest'` is a no-op while the row is already visible, so it does not
  // fight the surface's layout spring.
  useEffect(() => {
    if (!open) return
    // Optional call: jsdom does not implement scrollIntoView, and every render test in this repo
    // runs there — an unguarded call throws inside the effect and takes the whole mount down.
    document.getElementById(optId(active))?.scrollIntoView?.({ block: 'nearest' })
  }, [active, open, optId])

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
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, flat.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)) }
    else if (e.key === 'Enter') { e.preventDefault(); if (flat[active]) pick(flat[active].value) }
    else if (e.key === 'Escape') { setOpen(false) }
  }

  // flat index for active-row highlighting across groups
  let flatIdx = -1

  return (
    // rootRef spans the whole control so outside-click / focus-leave are measured
    // against the ONE morphing surface below it. `relative` anchors the Clear button,
    // which overlays the collapsed field from OUTSIDE the surface's overflow clip.
    <div ref={rootRef} onBlurCapture={onBlurCapture} className="relative">
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
              {/* 🔴 Measured on `#/triggers/new` before this: the input had no role, no aria-expanded,
                  no aria-controls and no aria-activedescendant, and the popup contained **0 listboxes
                  and 0 options** — pressing ArrowDown moved the visual highlight and changed NOTHING
                  in the accessibility tree. The keyboard model was already right (arrows + Enter, and
                  the doc says so); what was missing was saying so. */}
              <input ref={inputRef} value={q} onChange={(e) => { setQ(e.target.value); setActive(0) }} onKeyDown={onKey}
                role="combobox" aria-expanded aria-controls={listId} aria-autocomplete="list"
                aria-activedescendant={flat[active] ? optId(active) : undefined}
                placeholder="Search…" className="w-full h-8 rounded-md bg-surface pl-8 pr-2 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none" />
            </div>
            {/* `role="listbox"` only while it HOLDS options: a container that claims the role and
                contains one line of prose is the exact lie `popupItemRoles` was written about. When the
                filter matches nothing the message takes `role="status"`, so it is announced rather than
                sitting silently inside an empty listbox. */}
            <div id={listId} role={flat.length ? 'listbox' : undefined} tabIndex={-1}
              className="max-h-64 overflow-y-auto pb-1">
              {filtered.length === 0 ? <div role="status" className="px-3 py-3 text-on-surface-low text-[0.8125rem]">{emptyText}</div> : groups.map(([group, opts]) => (
                <div key={group} role={group ? 'group' : undefined} aria-label={group || undefined}>
                  {group && <div className="px-3 pt-2 pb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">{group}</div>}
                  {opts.map((o) => {
                    flatIdx++
                    const idx = flatIdx
                    const sel = o.value === value
                    const isActive = idx === active
                    // 🪤 `onMouseMove`, NOT `onMouseEnter`. Once the list scrolls to follow the arrow
                    // cursor, a stationary pointer resting over it has new rows move underneath — and
                    // the browser fires mouseenter for each one, so hover kept yanking the keyboard
                    // cursor back to wherever the pointer happened to sit. Measured: 12 ArrowDowns
                    // advanced the cursor to index 3 with the pointer over the list and to 12 with it
                    // parked off it. `mousemove` needs real pointer movement, so hover still highlights
                    // and scrolling no longer counts as hovering.
                    // `tabIndex={-1}` because the input owns focus and publishes the cursor via
                    // aria-activedescendant. Measured before: Tab from the search field moved focus onto
                    // a row INSIDE the open popup, so two navigation models — a visual arrow cursor and
                    // real Tab focus — ran independently and disagreed. Mouse behaviour is untouched.
                    return (
                      <button key={o.value} id={optId(idx)} role="option" aria-selected={sel} tabIndex={-1}
                        type="button" onMouseMove={() => setActive(idx)} onClick={() => pick(o.value)}
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
                          <span className="block truncate text-on-surface text-[0.8125rem]">{o.label}</span>
                          {o.description && <span className="block truncate text-on-surface-low text-[0.75rem]">{o.description}</span>}
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
            aria-haspopup="listbox" aria-expanded={open}
            className="flex w-full items-center gap-s h-10 px-m text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary/50">
            <span className={`flex-1 truncate ${selected ? 'text-on-surface' : 'text-on-surface-low'}`}>{selected ? selected.label : placeholder}</span>
            <motion.span className="shrink-0 text-on-surface-low" animate={{ rotate: open ? 180 : 0 }} transition={physics.snappy}>
              <ChevronDown size={16} />
            </motion.span>
          </motion.button>
        )}
      </motion.div>
      {/* Clear is a SIBLING of the morphing surface, not a child of the field button.
          It used to be a `role="button" tabIndex={-1}` span nested inside that button,
          which promised assistive tech a control no keyboard could reach: the negative
          tabindex hid it from Tab and it carried no key handler, so Enter/Space did
          nothing. A real <button> is operable by every input, and the field stops
          being an interactive-in-interactive.
          It overlays rather than sitting in the flex row because the surface owns a
          container-transform (`layout` + `overflow: hidden`): a new flex child would
          be measured by the morph and clipped during it. As a sibling of that surface
          it is outside the clip, and being outside the field button also means its
          click cannot bubble into "open the menu" — no stopPropagation needed. */}
      {/* `value` — not `selected`. Several callers ship an explicit empty-valued
          option ("Auto — provider default"), so `options.find(o => o.value === value)`
          MATCHES on an empty value and the old code offered Clear on a field with
          nothing to clear. Harmless while the control was unreachable; a dead button
          once it became operable. Gating on the raw value ties the affordance to
          "something is set", which is what Clear actually means. */}
      {!open && value !== '' && (
        <button type="button" aria-label="Clear selection" title="Clear selection"
          onClick={() => onChange('')}
          className="absolute right-8 top-5 grid size-6 -translate-y-1/2 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50">
          <X size={14} />
        </button>
      )}
    </div>
  )
}
