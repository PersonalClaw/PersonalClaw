import { useState, useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { RectangleVertical, RectangleHorizontal, StretchHorizontal, MoveHorizontal } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAppearance, type WidthPreset } from '../app/appearance'
import { overlayEnter, spring } from '../design/motion'

const OPTS: { key: WidthPreset; icon: LucideIcon; label: string }[] = [
  { key: 'narrow', icon: RectangleVertical, label: 'Narrow' },
  { key: 'default', icon: RectangleHorizontal, label: 'Default' },
  { key: 'wide', icon: StretchHorizontal, label: 'Wide' },
  { key: 'full', icon: MoveHorizontal, label: 'Full' },
]

/** Content-width preset as a single-icon shell control. Collapsed it shows just
 *  the active preset's icon; on hover it expands vertically into all 4 options.
 *  Picking one re-flows the active page's content column and collapses back to
 *  that one icon. Lives in the top-right shell corner. */
export function WidthPill() {
  const { widthPreset, setWidthPreset } = useAppearance()
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const active = OPTS.find((o) => o.key === widthPreset) ?? OPTS[1]
  const ActiveIcon = active.icon

  // 🔴 THIS MENU WAS HOVER-ONLY, so it could not be opened at all without a pointer. Driven on
  // `#/dashboard`: focus the trigger, press Enter → `aria-expanded` stayed **false** and nothing
  // opened; the popover appeared only on `onMouseEnter` of the wrapper. Its four options are real
  // named buttons, so once open they are fine — they were simply unreachable. WCAG 2.1.1.
  //
  // 🪤 AND THE TRIGGER PROMISED A MENU IT NEVER DELIVERED: `aria-haspopup="menu"` with **0**
  // `role="menu"` and **0** menuitems, measured hover-open. The options carry `aria-pressed`, which is
  // this app's recorded idiom for "one of N is chosen" (the Mode row, bento's SegToggle), so the fix
  // is to correct the DECLARATION rather than to convert working buttons into menuitems and fight
  // that convention. `aria-expanded` alone is the honest description of a disclosure.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus() } }
    const onDown = (e: MouseEvent) => { if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown) }
  }, [open])

  return (
    <div ref={wrapRef} className="relative" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      {/* Collapsed trigger — the current preset's icon. Click/Enter/Space toggle it; hover stays as a
          pointer-only convenience on top of that, not as the only way in. */}
      <button ref={btnRef} type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        title={`Content width: ${active.label}`} aria-label={`Content width: ${active.label}`}
        className="grid size-7 place-items-center rounded-pill transition-colors text-on-surface-low hover:text-on-surface">
        {/* the collapsed icon morphs (quick scale+fade key-swap) when the active
            preset changes, so picking a width visibly updates the corner glyph */}
        <AnimatePresence mode="wait" initial={false}>
          <motion.span key={active.key}
            initial={{ opacity: 0, scale: 0.6 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.6 }}
            transition={spring.spatialFast} className="grid place-items-center">
            <ActiveIcon size={14} />
          </motion.span>
        </AnimatePresence>
      </button>
      <AnimatePresence>
        {open && (
          // Anchored directly below the trigger (no gap → the pointer stays
          // inside the hover region while crossing into the menu). Absolute so
          // the corner cluster never reflows as it expands.
          <motion.div
            variants={overlayEnter} initial="initial" animate="animate" exit="exit"
            style={{ transformOrigin: 'top center' }}
            className="absolute left-1/2 top-full z-40 -translate-x-1/2 pt-1">
            <div className="flex flex-col gap-0.5 rounded-pill bg-surface-container p-0.5" style={{ boxShadow: 'var(--shadow-menu)' }}>
              {OPTS.map((o) => {
                const on = o.key === widthPreset
                const Icon = o.icon
                return (
                  <button key={o.key} type="button"
                    onClick={() => { setWidthPreset(o.key); setOpen(false) }}
                    title={`Content width: ${o.label}`} aria-label={`Content width: ${o.label}`} aria-pressed={on}
                    className="relative grid size-7 place-items-center rounded-pill transition-colors"
                    style={{ color: on ? 'var(--color-on-surface)' : 'var(--color-on-surface-low)' }}>
                    {/* The active option's fill. It moves to the picked option at once, and the four
                        options arrive together: a menu opened this often should not cascade or slide. */}
                    {on && (
                      <span className="absolute inset-0 rounded-pill" style={{ background: 'var(--color-surface-highest)' }} />
                    )}
                    <Icon size={14} className="relative" />
                  </button>
                )
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
