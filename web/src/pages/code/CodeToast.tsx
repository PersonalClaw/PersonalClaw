import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, HelpCircle, X, XCircle } from 'lucide-react'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { overlayEnter } from '../../design/motion'
import { fvs } from '../../design/fontWeight'
import { clipWords } from '../../lib/clipWords'

/** The code cockpit's toast, and the one rule for when it may stand over the page.
 *
 *  A `question` or `conflict` toast POINTS AT the Tasks rail's answer panel: its Respond focuses that
 *  panel's steer box, and the panel itself says what the worker is waiting on. It is for a user who
 *  cannot see that panel. Shown while she could, it sat bottom-right exactly over the panel and its
 *  "Use your best judgment" button, stayed until dismissed, and came back on the next Resume — the
 *  click it asked for could not be made. So a pointing toast stands only while its panel is off
 *  screen (the rail collapsed, a task's detail open in its place, or the panel scrolled away), and
 *  says what the panel says rather than "a question" for what may be a status. */
export type CodeToastKind = 'question' | 'conflict' | 'error' | 'ok'

export interface CodeToastState { kind: CodeToastKind; text: string }

/** Whether a toast points at the rail's answer panel (its Respond focuses the steer box there). */
export function pointsAtAnswer(kind: CodeToastKind): boolean {
  return kind === 'question' || kind === 'conflict'
}

/** How much of the worker's pending question the toast repeats. The panel holds all of it. */
const QUESTION_CHARS = 140

/** A `ref` for the answer panel that reports whether it is on screen, and `false` once it is gone.
 *
 *  Without an `IntersectionObserver` a mounted panel reports itself on screen: the toast exists for
 *  a panel she cannot see, and covering one she can is the defect this replaces. */
export function useOnScreenReport(report?: (onScreen: boolean) => void): (el: HTMLElement | null) => void {
  const reportRef = useRef(report)
  reportRef.current = report
  const [el, setEl] = useState<HTMLElement | null>(null)
  useEffect(() => {
    if (!el) return
    const send = (onScreen: boolean) => reportRef.current?.(onScreen)
    if (typeof IntersectionObserver === 'undefined') {
      send(true)
      return () => send(false)
    }
    const io = new IntersectionObserver((entries) => {
      const last = entries[entries.length - 1]
      if (last) send(last.isIntersecting)
    })
    io.observe(el)
    return () => { io.disconnect(); send(false) }
  }, [el])
  return setEl
}

/** The cockpit's toast slot: nothing while a pointing toast's panel is on screen, else the toast,
 *  with a question toast saying the worker's own words. */
export function CockpitToast({ toast, answerOnScreen, question, onDismiss, onRespond }: {
  toast: CodeToastState | null
  /** The rail's answer panel is on screen (`useOnScreenReport`). */
  answerOnScreen: boolean
  /** The worker's pending question, as the panel shows it. */
  question?: string
  onDismiss: () => void
  onRespond: () => void
}) {
  if (!toast) return null
  if (pointsAtAnswer(toast.kind) && answerOnScreen) return null
  const text = toast.kind === 'question'
    ? (clipWords(question || '', QUESTION_CHARS) || 'It paused and is waiting on you.')
    : toast.text
  return (
    <CodeToast kind={toast.kind} text={text} onDismiss={onDismiss}
      onRespond={pointsAtAnswer(toast.kind) ? onRespond : undefined} />
  )
}

/** A prominent, dismissable toast (bottom-right). Portaled over everything so it is seen
 *  regardless of scroll position; "Respond" focuses the steer box. */
function CodeToast({ kind, text, onDismiss, onRespond }: {
  kind: CodeToastKind; text: string; onDismiss: () => void; onRespond?: () => void
}) {
  // The `ok` toast auto-dismisses (a success confirmation — purely informational,
  // e.g. "saved as artifact"). error / conflict / question PERSIST: each needs the
  // user to read + act/acknowledge, so they stay until dismissed.
  // The timer keys off the toast IDENTITY (kind+text) only — NOT onDismiss, which the
  // parent passes as a fresh arrow each render. A running project re-renders often
  // (SSE/poll); depending on onDismiss restarted the timer every re-render, so a
  // transient toast could outlive its timeout indefinitely. A ref holds the latest
  // onDismiss so the fire-once timer still calls the current closure.
  const dismissRef = useRef(onDismiss); dismissRef.current = onDismiss
  useEffect(() => {
    if (kind !== 'ok') return
    const t = setTimeout(() => dismissRef.current(), 4000)
    return () => clearTimeout(t)
  }, [kind, text])
  const tone = kind === 'error' ? 'var(--color-danger)'
    : kind === 'conflict' ? 'var(--color-warn)' : kind === 'ok' ? 'var(--color-ok)' : 'var(--color-info)'
  const Icon = kind === 'error' ? XCircle : kind === 'conflict' ? AlertTriangle : kind === 'ok' ? CheckCircle2 : HelpCircle
  return createPortal(
    <motion.div role="alert" aria-live="assertive"
      variants={overlayEnter} initial="initial" animate="animate" exit="exit"
      className="fixed bottom-4 right-4 z-[var(--z-toast)] w-[360px] max-w-[calc(100vw-2rem)] rounded-xl border border-outline-variant/50 bg-surface-container p-3.5 shadow-lg">
      <div className="flex items-start gap-2.5">
        <Icon size={18} className="mt-0.5 shrink-0" style={{ color: tone }} />
        <div className="min-w-0 flex-1">
          <p data-type="label-s" className="text-on-surface" style={fvs(600)}>
            {kind === 'error' ? "That didn't work" : kind === 'conflict' ? 'Merge conflict — needs you' : kind === 'ok' ? 'Done' : 'The worker needs your input'}
          </p>
          <p data-type="caption" className="mt-0.5 text-on-surface-var">{text}</p>
          <div className="mt-s flex items-center gap-s">
            {/* Respond keeps its per-kind tone background (error/conflict/input) —
                a dynamic solid fill the Button variants deliberately don't cover. */}
            {onRespond && <button type="button" onClick={onRespond}
              data-type="caption" className="rounded-md px-2.5 py-1" style={{ background: tone, color: 'var(--color-on-primary)' }}>Respond</button>}
            <Button variant="ghost" size="xs" onClick={onDismiss}>Dismiss</Button>
          </div>
        </div>
        <IconButton icon={X} label="Dismiss" onClick={onDismiss} size={24} iconSize={14} className="shrink-0" />
      </div>
    </motion.div>,
    document.body,
  )
}
