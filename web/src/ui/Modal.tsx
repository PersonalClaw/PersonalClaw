import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { motion, useReducedMotion } from 'framer-motion'
import { X } from 'lucide-react'
import { IconButton } from './IconButton'
import { useFocusTrap } from './useFocusTrap'
import { spring, bounce, expr } from '../design/motion'

/** Reusable centered modal with a scrim. Header carries the title and a single
 *  close (X) button; Escape and a scrim click also dismiss it.
 *  Body scrolls; width tracks the content column (a touch wider for reading).
 *  Portaled to <body> so `position:fixed` centers against the VIEWPORT — an
 *  animated/transformed ancestor (composer, glow) would otherwise become the
 *  containing block and push the modal off-center.
 *
 *  Redesign-v2: the sheet rises with an expressiveness-scaled overshoot (bold →
 *  a springy lift, refined/reduced → a clean settle), and the scrim's blur eases
 *  in with it for depth. Pass `layoutId` to morph the sheet OUT of the element
 *  that opened it (a shared-element "grow from the trigger" transition) — the
 *  trigger must render a `motion.*` with the same `layoutId`. */
export function Modal({ title, icon, onClose, children, layoutId }: {
  title: ReactNode
  icon?: ReactNode
  onClose: () => void
  children: ReactNode
  /** Shared-element id: when the opening trigger carries the same layoutId, the
   *  sheet morphs from that element's box instead of scaling from center. */
  layoutId?: string
}) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const reduce = useReducedMotion()
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h)
  }, [onClose])
  // Entrance overshoot + lift scale with expressiveness; reduced-motion → static fade.
  const enterScale = reduce ? 1 : 1 - expr(0.04, 0.5)
  const enterY = reduce ? 0 : expr(10, 0.4)
  return createPortal(
    <motion.div className="fixed inset-0 z-[60] flex items-center justify-center p-2xl"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <motion.div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" onClick={onClose}
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects} />
      <motion.div ref={trapRef} role="dialog" aria-modal="true"
        {...(layoutId ? { layoutId } : {})}
        aria-label={typeof title === 'string' ? title : undefined}
        className="squircle relative flex max-h-full w-full flex-col overflow-hidden bg-surface shadow-sheet"
        style={{ maxWidth: 'calc(var(--content-width) + 160px)' }}
        initial={{ opacity: 0, scale: enterScale, y: enterY }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98, y: 6 }}
        transition={reduce ? spring.effects : bounce.lift}>
        <div className="sticky top-0 z-10 flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface/95 px-l py-m">
          <div className="flex min-w-0 items-center gap-s">{icon}<span data-type="title-l" className="truncate text-on-surface">{title}</span></div>
          <div className="flex shrink-0 items-center gap-1">
            <IconButton icon={X} label="Close" size={34} onClick={onClose} />
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-l py-l">{children}</div>
      </motion.div>
    </motion.div>,
    document.body,
  )
}
