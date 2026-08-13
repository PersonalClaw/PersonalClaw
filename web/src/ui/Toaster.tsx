import { useEffect, useState } from 'react'
import { AnimatePresence, motion, type Transition } from 'framer-motion'
import { Info, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { dragElastic, spring, swipeDismiss } from '../design/motion'

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

  // A toast the user just FLICKED away, with the transition `swipeDismiss()` resolved
  // for that gesture. Set on drag end and consumed one render later by the effect
  // below, because AnimatePresence animates an exiting element with the props from its
  // last render: removing the toast in the same tick as the verdict would throw the
  // gesture's own curve away and fall back to the timer-expiry spring.
  const [flicked, setFlicked] = useState<{ id: number; transition: Transition } | null>(null)
  useEffect(() => {
    if (!flicked) return
    dismiss(flicked.id)
    setFlicked(null)
  }, [flicked])

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
    // A LIVE REGION, or a toast is never announced. The host had no role and no aria-live,
    // so `notify()` — the app's one channel for "that worked" / "that failed" — reached
    // screen-reader users not at all: the text sat in the a11y tree as ordinary content
    // that nothing prompted anyone to read, then vanished after 5s.
    //
    // `role="status"` (implicitly aria-live=polite) for info/success, so a confirmation
    // waits for a pause instead of interrupting. Errors need the opposite and get their own
    // assertive region below — one region cannot carry both urgencies, and an error that
    // waits its turn behind a queue of confirmations is the wrong tradeoff.
    //
    // aria-relevant="additions" so only NEW toasts are announced; without it the
    // auto-dismiss removal re-announces the region as it empties. The region element itself
    // is always mounted (never conditional) — a live region created at the same moment its
    // content appears is not reliably observed.
    <div className="pointer-events-none fixed bottom-4 right-4 z-[200] flex max-w-sm flex-col items-stretch gap-2">
      <div role="status" aria-live="polite" aria-relevant="additions" aria-atomic="false" className="sr-only">
        {visible.filter((t) => t.level !== 'error').map((t) => <div key={t.id}>{t.message}</div>)}
      </div>
      <div role="alert" aria-live="assertive" aria-relevant="additions" aria-atomic="false" className="sr-only">
        {visible.filter((t) => t.level === 'error').map((t) => <div key={t.id}>{t.message}</div>)}
      </div>
      <AnimatePresence initial={false} mode="popLayout">
        {visible.map((t) => {
          const Icon = ICONS[t.level]
          return (
            <motion.div
              key={t.id}
              layout
              drag="x"
              dragConstraints={{ left: 0, right: 0 }}
              dragElastic={{ left: 0, right: dragElastic() }}
              // RIGHTWARD only — the card is pinned at its left constraint and its exit
              // flies right, so a leftward flick (which Framer still reports a velocity
              // for, since velocity comes from the pointer and not the element) must not
              // count. `swipeDismiss` owns the thresholds: they are user-tunable tokens,
              // not the `> 80 || > 500` literals that used to live here.
              onDragEnd={(_, info) => {
                const swipe = swipeDismiss(Math.max(0, info.velocity.x), Math.max(0, info.offset.x))
                if (swipe.dismiss) setFlicked({ id: t.id, transition: swipe.transition })
              }}
              initial={{ opacity: 0, y: 16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              // A flicked toast leaves on the gesture's own accelerating curve; one that
              // simply timed out keeps the calmer spring.
              exit={{ opacity: 0, x: 80, scale: 0.9, transition: flicked?.id === t.id ? flicked.transition : spring.spatialFast }}
              transition={spring.spatialDefault}
              className="glass pointer-events-auto flex cursor-grab items-start gap-s rounded-lg px-m py-s active:cursor-grabbing"
            >
              <Icon size={16} className={`mt-0.5 shrink-0 ${TONES[t.level]}`} />
              {/* aria-hidden on the TEXT only, never on the card: the live regions above own
                  the announcement, so leaving this readable puts the message in the tree
                  twice. The card itself must stay exposed — it contains the focusable Dismiss
                  button, and hiding an ancestor of a focusable control is `aria-hidden-focus`
                  (serious). Measured: doing that produced exactly that violation. */}
              <span aria-hidden data-type="body-m" className="min-w-0 flex-1 text-on-surface">{t.message}</span>
              {/* Named by its MESSAGE. Toasts stack up to 4, and a bare "Dismiss" gave every
                  one the same name — measured 3 identical buttons with 3 toasts up, so a
                  screen-reader user choosing between them had nothing to go on. The card's
                  text is aria-hidden (the live regions own the announcement), so this label
                  is the only place the message reaches the a11y tree per-toast. */}
              <button
                className="ml-1 shrink-0 text-on-surface-low transition-colors hover:text-on-surface"
                onClick={() => dismiss(t.id)}
                aria-label={`Dismiss: ${t.message}`}
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
