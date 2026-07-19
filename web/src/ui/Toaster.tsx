import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Info, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { spring } from '../design/motion'

interface Toast { id: number; message: string; level: 'info' | 'success' | 'error' }

const ICONS = { info: Info, success: CheckCircle2, error: AlertCircle }
const TONES = { info: 'text-on-surface-var', success: 'text-ok', error: 'text-danger' }

/** Global toast host. Renders transient messages dispatched via the `ne:toast`
 *  CustomEvent — the surface contributed apps reach through the SDK's useNotify,
 *  and any host code can use too. Auto-dismisses; stacks bottom-right with a
 *  Sonner-style fan-out (newest in front, older ones scaled + tucked behind) and
 *  velocity swipe-to-dismiss (drag right past a threshold or with enough flick).
 *  Reduced-motion is honored via the root MotionConfig. */
export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const dismiss = (id: number) => setToasts((prev) => prev.filter((t) => t.id !== id))

  useEffect(() => {
    let seq = 0
    const onToast = (e: Event) => {
      const d = (e as CustomEvent).detail || {}
      const message = String(d.message ?? '').trim()
      if (!message) return
      const level: Toast['level'] = ['info', 'success', 'error'].includes(d.level) ? d.level : 'info'
      const id = ++seq
      setToasts((prev) => [...prev, { id, message, level }])
      window.setTimeout(() => dismiss(id), 5000)
    }
    window.addEventListener('ne:toast', onToast as EventListener)
    return () => window.removeEventListener('ne:toast', onToast as EventListener)
  }, [])

  // Newest at the BOTTOM (closest to the corner) — the most recent toast is the
  // most prominent. Cap the visible count so a burst doesn't build a tall tower;
  // older overflow just isn't shown (still auto-dismisses on its timer).
  const visible = toasts.slice(-4)

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[200] flex max-w-sm flex-col items-stretch gap-2">
      <AnimatePresence initial={false} mode="popLayout">
        {visible.map((t) => {
          const Icon = ICONS[t.level]
          return (
            <motion.div
              key={t.id}
              layout
              drag="x"
              dragConstraints={{ left: 0, right: 0 }}
              dragElastic={{ left: 0, right: 0.9 }}
              onDragEnd={(_, info) => { if (info.offset.x > 80 || info.velocity.x > 500) dismiss(t.id) }}
              initial={{ opacity: 0, y: 16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, x: 80, scale: 0.9, transition: spring.spatialFast }}
              transition={spring.spatialDefault}
              className="glass pointer-events-auto flex cursor-grab items-start gap-s rounded-lg px-m py-s active:cursor-grabbing"
            >
              <Icon size={16} className={`mt-0.5 shrink-0 ${TONES[t.level]}`} />
              <span data-type="body-m" className="min-w-0 flex-1 text-on-surface">{t.message}</span>
              <button
                className="ml-1 shrink-0 text-on-surface-low transition-colors hover:text-on-surface"
                onClick={() => dismiss(t.id)}
                aria-label="Dismiss"
              >
                <X size={14} />
              </button>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}
