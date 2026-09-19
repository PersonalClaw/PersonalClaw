import { useEffect, useState } from 'react'
import { AnimatePresence, motion, type Transition } from 'framer-motion'
import { Info, CheckCircle2, AlertCircle, X } from 'lucide-react'
import { dragElastic, spring, swipeDismiss } from '../design/motion'
import { playCue } from '../design/soundCues'
import { TextLink } from './TextLink'

interface Toast {
  id: number
  message: string
  level: 'info' | 'success' | 'error'
  /** An in-app hash route this toast is ABOUT, rendered as a link (#258).
   *
   *  A toast whose sentence tells the reader to go somewhere used to leave them to find it: the
   *  approval nudge said *"open workflow:…:… to respond"* — a session key with no route behind
   *  it. Naming an outcome the surface does not deliver is the defect, so the fix is a
   *  mechanism rather than nicer wording: a toast may carry its destination, and the
   *  destination is a route the SPA actually serves. Hash-only by construction — `external` is
   *  not plumbed, so this cannot become an off-app link surface.
   *
   *  Optional, and most toasts have no destination. The notification toast
   *  (`lib/notificationToasts.ts`) is deliberately left alone here: #343 closed without a link,
   *  and giving it one is its own change, not a rider on this one. */
  href?: string
  /** The link's text. It must say where it goes; "Open" alone is what a toast is replacing. */
  hrefLabel?: string
}

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
      // The error cue point (PERSONALITY-THEMES §S2). ERRORS ONLY — the assertive
      // live region below draws the same line, and a chime on every "Saved" would
      // make the feature unusable within a minute. Silent unless the user opted in;
      // every gate lives inside playCue, so this call site carries no policy.
      if (level === 'error') playCue('error')
      const id = ++seq
      // Only a HASH route is accepted. A dispatcher that hands over an `http(s)`/`javascript:`
      // target gets no link at all rather than one the host renders: this is a global event any
      // contributed app can fire (`appSdk.notify`), so the host decides what a toast may link to.
      const href = String(d.href ?? '')
      const linked = href.startsWith('#/') ? href : ''
      const hrefLabel = String(d.hrefLabel ?? '').trim() || 'Open'
      setToasts((prev) => [...prev, { id, message, level, href: linked || undefined, hrefLabel }])
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
    <div className="pointer-events-none fixed bottom-4 right-4 z-[var(--z-toast)] flex max-w-sm flex-col items-stretch gap-2">
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
              <div className="min-w-0 flex-1">
                <span aria-hidden data-type="body-m" className="text-on-surface">{t.message}</span>
                {/* The DESTINATION, when the dispatcher named one. `ink="emphasis"` because the
                    card's ground is `.glass`, not `--color-surface` (see TextLink's contrast
                    table). Not aria-hidden: the live regions own the message, but a link is a
                    control and must stay in the tree — and its own label is where the a11y tree
                    learns where it goes, since the sentence around it is hidden. */}
                {t.href && (
                  <div className="mt-1">
                    <TextLink href={t.href} size="xs" ink="emphasis" aria-label={`${t.hrefLabel}: ${t.message}`}>
                      {t.hrefLabel}
                    </TextLink>
                  </div>
                )}
              </div>
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
