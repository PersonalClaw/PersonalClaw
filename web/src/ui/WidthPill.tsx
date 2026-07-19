import { useId, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { RectangleVertical, RectangleHorizontal, StretchHorizontal, MoveHorizontal } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAppearance, type WidthPreset } from '../app/appearance'
import { spring, stagger, listItemEnter } from '../design/motion'

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
  const active = OPTS.find((o) => o.key === widthPreset) ?? OPTS[1]
  const ActiveIcon = active.icon
  const indicatorId = `widthpill-active-${useId()}`

  return (
    <div className="relative" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      {/* Collapsed trigger — the current preset's icon. */}
      <button type="button" aria-haspopup="menu" aria-expanded={open}
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
            initial={{ opacity: 0, scale: 0.96, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0, transition: spring.spatialFast }}
            exit={{ opacity: 0, scale: 0.98, transition: spring.effects }}
            style={{ transformOrigin: 'top center' }}
            className="absolute left-1/2 top-full z-40 -translate-x-1/2 pt-1">
            <motion.div variants={{ animate: { transition: stagger(0.035) } }} initial="initial" animate="animate"
              className="flex flex-col gap-0.5 rounded-pill bg-surface-container p-0.5" style={{ boxShadow: 'var(--shadow-menu)' }}>
              {OPTS.map((o) => {
                const on = o.key === widthPreset
                const Icon = o.icon
                return (
                  <motion.button key={o.key} type="button" variants={listItemEnter} whileTap={{ scale: 0.9 }}
                    onClick={() => { setWidthPreset(o.key); setOpen(false) }}
                    title={`Content width: ${o.label}`} aria-label={`Content width: ${o.label}`} aria-pressed={on}
                    className="relative grid size-7 place-items-center rounded-pill transition-colors"
                    style={{ color: on ? 'var(--color-on-surface)' : 'var(--color-on-surface-low)' }}>
                    {/* liquid active indicator — one shared pill that SLIDES between
                        the 4 width options via layoutId, instead of the highlight
                        blink-jumping from icon to icon. */}
                    {on && (
                      <motion.span layoutId={indicatorId} transition={spring.spatialFast}
                        className="absolute inset-0 rounded-pill" style={{ background: 'var(--color-surface-highest)' }} />
                    )}
                    <Icon size={14} className="relative" />
                  </motion.button>
                )
              })}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
