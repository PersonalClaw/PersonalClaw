import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Check, CheckCircle2, DownloadCloud, Loader2, RefreshCw } from 'lucide-react'
import { spring, bounce } from '../design/motion'
import { useChatSocket } from '../lib/useChatSocket'
import { api } from '../lib/api'

// ── Update progress overlay ────────────────────────────────────────────────
// The self-update pipeline (POST /api/update) broadcasts `update_progress` WS
// events as it walks pulling → installing → building → restarting (plus
// error/failed/done and non-step `warning` notes from the frontend build).
// A PLAIN restart (POST /api/system/restart) also pushes `update_progress`
// with step=restarting — distinguished here by being the FIRST step received
// (no prior pulling/installing/building), which renders a simplified single-
// line "Restarting gateway" with a spinner instead of the 4-step stepper.
//
// This is the ONE shell-level surface that renders progress: a modal overlay
// that appears on the first step from ANY page, tracks the pipeline live, and
// offers Cancel (POST /api/update/cancel — the backend's dismiss-stuck-overlay
// endpoint). Mounted once in the app shell next to <Toaster>/<DialogHost>.

const STEPS = [
  { id: 'pulling', label: 'Pulling' },
  { id: 'installing', label: 'Installing dependencies' },
  { id: 'building', label: 'Building frontend' },
  { id: 'restarting', label: 'Restarting' },
] as const

type StepId = (typeof STEPS)[number]['id']
type Phase = StepId | 'done' | 'error'
const STEP_IDS = new Set<string>(STEPS.map((s) => s.id))
// Steps that PRECEDE restarting in the full update pipeline — if we see
// restarting without any of these having fired, it's a restart-only.
const PRE_RESTART_STEPS = new Set(['pulling', 'installing', 'building'])

export interface UpdateProgress {
  phase: Phase
  detail: string
  /** True when the overlay entered directly at "restarting" with no prior update
   *  pipeline steps — i.e. a plain gateway restart, not an update. */
  restartOnly: boolean
}

/** Listen to `update_progress` WS events and expose the live update state plus
 *  a cancel/dismiss action. Also hydrates from GET /api/status on mount (the
 *  status snapshot carries `update_progress`) so a page opened MID-update shows
 *  the overlay, and treats a WS reconnect during `restarting` as completion —
 *  the re-exec'd gateway never sends `done` (the old process image is gone). */
export function useUpdateProgress() {
  const [state, setState] = useState<UpdateProgress | null>(null)
  // Track whether we've seen any pre-restart update step in the current pipeline.
  // If we receive "restarting" without ever seeing pulling/installing/building,
  // it's a restart-only (plain gateway restart, not an update).
  const seenPreRestartStep = useRef(false)
  // After a user cancel the backend broadcasts one `failed: Update cancelled`
  // event before clearing — suppress briefly so the overlay doesn't reopen as
  // an error the moment the user dismissed it.
  const suppressUntil = useRef(0)

  const apply = useCallback((step: string, detail: string) => {
    if (Date.now() < suppressUntil.current) return
    if (step === 'done') {
      seenPreRestartStep.current = false
      setState({ phase: 'done', detail: detail || 'Update complete', restartOnly: false })
    } else if (step === 'error' || step === 'failed') {
      setState((prev) => ({ phase: 'error', detail: detail || 'Update failed', restartOnly: prev?.restartOnly ?? false }))
    }
    // `warning` is a non-step note (frontend-build fallbacks) — keep the current
    // phase, surface the message. Ignore it if the overlay isn't open.
    else if (step === 'warning') setState((p) => (p ? { ...p, detail } : p))
    else if (STEP_IDS.has(step)) {
      if (PRE_RESTART_STEPS.has(step)) seenPreRestartStep.current = true
      const isRestartOnly = step === 'restarting' && !seenPreRestartStep.current
      setState({ phase: step as StepId, detail, restartOnly: isRestartOnly })
    }
  }, [])

  useChatSocket(
    useCallback((m) => {
      if (m.type !== 'update_progress') return
      const d = m.data || {}
      apply(String(d.step ?? ''), String(d.detail ?? ''))
    }, [apply]),
    // Socket reopened after a drop: if we were mid-restart, the new gateway is
    // up — that IS success (the replaced process can't broadcast `done`).
    useCallback(() => {
      setState((p) => {
        if (p && p.phase === 'restarting') {
          seenPreRestartStep.current = false
          return { phase: 'done', detail: p.restartOnly ? 'Restart complete' : 'Update complete', restartOnly: p.restartOnly }
        }
        return p
      })
    }, []),
  )

  // Hydrate: a page loaded while an update is in flight still gets the overlay.
  useEffect(() => {
    api.status().then((s) => {
      const p = s.update_progress
      if (p && typeof p.step === 'string' && p.step) apply(p.step, String(p.detail ?? ''))
    }).catch(() => {})
  }, [apply])

  // `done` lingers briefly (let the user read the completion message), then clears.
  useEffect(() => {
    if (state?.phase !== 'done') return
    const t = window.setTimeout(() => setState(null), 2000)
    return () => window.clearTimeout(t)
  }, [state?.phase])

  /** Cancel a running update / dismiss a stuck or failed overlay. Clears the
   *  server-side progress state too, so a reload doesn't resurrect it. */
  const cancel = useCallback(() => {
    suppressUntil.current = Date.now() + 2500
    seenPreRestartStep.current = false
    setState(null)
    api.cancelUpdate().catch(() => {})  // dismiss locally regardless
  }, [])

  return { progress: state, cancel }
}

/** Detail line for the restart-only view. A plain restart's backend detail
 *  ("Restarting gateway…") is redundant with the title, so it becomes the
 *  reconnect hint; a MEANINGFUL note (the degraded update-apply's "Already up
 *  to date — restarting…" / "No upstream configured — restarting…") is kept,
 *  with its trailing "— restarting…" swapped for the reconnect hint. */
function restartOnlyDetail(detail: string): string {
  const d = detail.replace(/\s*—\s*restarting…?\s*$/i, '').trim()
  return d && !/^restarting/i.test(d) ? `${d} — reconnecting shortly…` : 'Reconnecting shortly…'
}

function StepRow({ label, status }: { label: string; status: 'done' | 'active' | 'pending' }) {
  return (
    <div className="flex items-center gap-3">
      <span className="grid size-6 shrink-0 place-items-center rounded-full"
        style={status === 'done' ? { background: 'color-mix(in srgb, var(--color-success) 18%, transparent)', color: 'var(--color-success)' }
          : status === 'active' ? { background: 'color-mix(in srgb, var(--color-primary) 16%, transparent)', color: 'var(--color-primary)' }
          : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
        {status === 'done' ? <Check size={13} />
          : status === 'active' ? <Loader2 size={13} className="animate-spin" />
          : <span className="size-1.5 rounded-full bg-current opacity-60" />}
      </span>
      <span className="text-[0.875rem]"
        style={{ color: status === 'pending' ? 'var(--color-on-surface-low)' : 'var(--color-on-surface)' }}>
        {label}
      </span>
    </div>
  )
}

/** The modal itself — mount ONCE in the app shell. Renders nothing until an
 *  update or restart starts; then a centered sheet with either the 4-step
 *  progression (full update) or a single "Restarting gateway" spinner (plain
 *  restart). Cancel/Dismiss on failure. */
export function UpdateProgressOverlay() {
  const { progress, cancel } = useUpdateProgress()
  const stepIdx = progress ? STEPS.findIndex((s) => s.id === progress.phase) : -1
  const isError = progress?.phase === 'error'
  const isDone = progress?.phase === 'done'
  const isRestartOnly = progress?.restartOnly ?? false

  // Title adapts: restart-only vs full update vs error vs done
  const title = isError
    ? (isRestartOnly ? 'Restart failed' : 'Update failed')
    : isDone
      ? (isRestartOnly ? 'Restart complete' : 'Update complete')
      : isRestartOnly
        ? 'Restarting gateway'
        : 'Updating PersonalClaw'

  return createPortal(
    <AnimatePresence>
      {progress && (
        <motion.div key="update-overlay" className="fixed inset-0 z-[80] flex items-center justify-center p-2xl"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
          <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" />
          <motion.div role="alertdialog" aria-modal="true" aria-label="Update progress"
            className="relative w-full max-w-[400px] overflow-hidden rounded-xl bg-surface shadow-sheet"
            initial={{ opacity: 0, scale: 0.97, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: 6 }} transition={bounce.lift}>
            <div className="flex items-start gap-3 px-l pt-l">
              <span className="mt-0.5 shrink-0" style={{ color: isError ? 'var(--color-danger)' : isDone ? 'var(--color-success)' : 'var(--color-primary)' }}>
                {isError ? <AlertTriangle size={18} />
                  : isDone ? <CheckCircle2 size={18} />
                  : isRestartOnly ? <RefreshCw size={18} className="animate-spin" />
                  : <DownloadCloud size={18} />}
              </span>
              <div className="min-w-0 flex-1">
                <div data-type="title-l" className="text-on-surface">{title}</div>
                {progress.detail && (
                  <div className="mt-1 text-[0.8125rem]" style={{ color: isError ? 'var(--color-danger)' : 'var(--color-on-surface-var)' }}>
                    {isRestartOnly && !isError && !isDone ? restartOnlyDetail(progress.detail) : progress.detail}
                  </div>
                )}
              </div>
            </div>

            {/* Full 4-step stepper only for REAL updates (not restart-only, not error) */}
            {!isError && !isRestartOnly && (
              <div className="mt-4 flex flex-col gap-2.5 px-l">
                {STEPS.map((s, i) => (
                  <StepRow key={s.id} label={s.label}
                    status={isDone || i < stepIdx ? 'done' : i === stepIdx ? 'active' : 'pending'} />
                ))}
              </div>
            )}

            <div className="flex justify-end px-l py-l">
              {!isDone && (
                <button type="button" onClick={cancel}
                  className="rounded-pill px-4 h-9 text-[0.875rem] text-on-surface-var bg-surface-high hover:bg-surface-highest transition-colors">
                  {isError ? 'Dismiss' : 'Cancel'}
                </button>
              )}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  )
}
