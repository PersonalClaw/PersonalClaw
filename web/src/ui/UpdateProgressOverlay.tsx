import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, Ban, Check, CheckCircle2, DownloadCloud, Loader2, RefreshCw } from 'lucide-react'
import { spring, physics } from '../design/motion'
import { useChatSocket } from '../lib/useChatSocket'
import { api } from '../lib/api'
import { readableErrText } from '../lib/errText'
import { notify } from '../app/appSdk'
import { Button } from './Button'
import { useFocusTrap } from './useFocusTrap'
import { accentChip } from '../design/accent'

// ── Update progress overlay ────────────────────────────────────────────────
// The self-update pipeline (POST /api/update) broadcasts `update_progress` WS
// events as it walks pulling → installing → building → restarting (plus
// error/failed/cancelled/done and non-step `warning` notes from the frontend build).
// A PLAIN restart (POST /api/system/restart) also pushes `update_progress`
// with step=restarting — distinguished here by being the FIRST step received
// (no prior pulling/installing/building), which renders a simplified single-
// line "Restarting gateway" with a spinner instead of the 4-step stepper.
//
// This is the ONE shell-level surface that renders progress: a modal overlay
// that appears on the first step from ANY page, tracks the pipeline live, and
// says how an update ended. Mounted once in the app shell next to <Toaster>/<DialogHost>.
//
// 🔴 CANCEL IS OFFERED ONLY WHERE IT STOPS SOMETHING. It used to be offered at every step and to
// close the overlay on the spot, while the gateway answered "Update cancelled by user" and the
// update went on: the installer kept running and the gateway restarted into the new release. Now
// Cancel (POST /api/update/cancel) is offered while the update moves or installs, which is what
// the gateway stops: it stops the installer, puts the checkout back, and answers once that is done
// with what it left, which the sheet shows in place of the steps (`cancelled` when nothing was
// changed, `error` when something is not as it was). Once the new release is installed the update
// builds and restarts, which nothing puts back, so the sheet says it will finish and offers Hide,
// which only closes the sheet. A plain restart offers Hide too: a restart cannot be cancelled.
// Dismiss (POST /api/update/dismiss) closes what an update ended with, and never stops one.

const STEPS = [
  { id: 'pulling', label: 'Pulling' },
  { id: 'installing', label: 'Installing dependencies' },
  { id: 'building', label: 'Building frontend' },
  { id: 'restarting', label: 'Restarting' },
] as const

type StepId = (typeof STEPS)[number]['id']
type Phase = StepId | 'done' | 'error' | 'cancelled'
const STEP_IDS = new Set<string>(STEPS.map((s) => s.id))
// Steps that PRECEDE restarting in the full update pipeline — if we see
// restarting without any of these having fired, it's a restart-only.
const PRE_RESTART_STEPS = new Set(['pulling', 'installing', 'building'])
/** The steps the gateway can stop: before the new release is installed. */
const CANCELLABLE = new Set<Phase>(['pulling', 'installing'])
/** The phases an update has ended in, which Dismiss closes. */
const ENDED = new Set<Phase>(['error', 'cancelled'])

export interface UpdateProgress {
  phase: Phase
  detail: string
  /** True when the overlay entered directly at "restarting" with no prior update
   *  pipeline steps — i.e. a plain gateway restart, not an update. */
  restartOnly: boolean
  /** Cancel was pressed, and the gateway is stopping the update. */
  cancelling?: boolean
  /** Why a Cancel did not stop the update (the gateway's sentence: it will finish), or that the
   *  gateway did not answer it. */
  refused?: string
}

/** What the sheet's buttons do. */
export interface UpdateActions {
  /** Stop the update in progress (Cancel). */
  cancel: () => void
  /** Close what an update ended with (Dismiss). */
  dismiss: () => void
  /** Close the sheet while the update finishes (Hide). Stops nothing. */
  hide: () => void
}

/** Listen to `update_progress` WS events and expose the live update state plus
 *  the sheet's actions. Also hydrates from GET /api/status on mount (the
 *  status snapshot carries `update_progress`) so a page opened MID-update shows
 *  the overlay, and treats a WS reconnect during `restarting` as completion —
 *  the re-exec'd gateway never sends `done` (the old process image is gone). */
export function useUpdateProgress() {
  const [state, setState] = useState<UpdateProgress | null>(null)
  // Track whether we've seen any pre-restart update step in the current pipeline.
  // If we receive "restarting" without ever seeing pulling/installing/building,
  // it's a restart-only (plain gateway restart, not an update).
  const seenPreRestartStep = useRef(false)

  const apply = useCallback((step: string, detail: string) => {
    if (step === 'done') {
      seenPreRestartStep.current = false
      setState({ phase: 'done', detail: detail || 'Update complete', restartOnly: false })
    } else if (step === 'error' || step === 'failed') {
      seenPreRestartStep.current = false
      setState((prev) => ({ phase: 'error', detail: detail || 'Update failed', restartOnly: prev?.restartOnly ?? false }))
    } else if (step === 'cancelled') {
      seenPreRestartStep.current = false
      setState({ phase: 'cancelled', detail: detail || 'The update was cancelled.', restartOnly: false })
    }
    // `warning` is a non-step note (frontend-build fallbacks) — keep the current
    // phase, surface the message. Ignore it if the overlay isn't open.
    else if (step === 'warning') setState((p) => (p ? { ...p, detail } : p))
    else if (STEP_IDS.has(step)) {
      if (PRE_RESTART_STEPS.has(step)) seenPreRestartStep.current = true
      const isRestartOnly = step === 'restarting' && !seenPreRestartStep.current
      // A Cancel still in flight stays in flight while the update has not passed the point where
      // it can stop; past it, the gateway's answer says why it will finish.
      setState((prev) => ({
        phase: step as StepId,
        detail,
        restartOnly: isRestartOnly,
        cancelling: !!prev?.cancelling && CANCELLABLE.has(step as Phase),
      }))
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

  /** Close the sheet and clear what the gateway holds for it, so a reload does not show it again. */
  const dismiss = useCallback(() => {
    seenPreRestartStep.current = false
    setState(null)
    api.dismissUpdate().catch(() => {})  // the sheet is closed; this only clears server-side progress
  }, [])

  const hide = useCallback(() => setState(null), [])

  /** Ask the gateway to stop the update, and show what it answers: what the stop left, that it is
   *  still stopping, or why the update will finish. The sheet stays open until it has answered. */
  const cancel = useCallback(() => {
    setState((p) => (p ? { ...p, cancelling: true, refused: '' } : p))
    const stillRunning = (p: UpdateProgress | null) => !!p && !ENDED.has(p.phase) && p.phase !== 'done'
    api.cancelUpdate().then((r) => {
      if (r.status === 'not_running') {
        dismiss()
        notify(r.detail || 'No update is running, so there was nothing to cancel.', 'info')
        return
      }
      const p = r.update_progress
      if (r.status === 'stopped' && p?.step) apply(p.step, String(p.detail ?? ''))
      // Still stopping: what it left arrives on the socket.
      else setState((s) => (stillRunning(s) ? { ...s!, cancelling: true } : s))
    }).catch((e: unknown) => {
      const said = readableErrText(e) || 'PersonalClaw did not answer, so it is not known whether the update stopped.'
      setState((s) => (stillRunning(s) ? { ...s!, cancelling: false, refused: said } : s))
    })
  }, [apply, dismiss])

  return { progress: state, actions: { cancel, dismiss, hide } satisfies UpdateActions }
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
    <div className="flex items-center gap-m">
      <span className="grid size-6 shrink-0 place-items-center rounded-full"
        style={status === 'done' ? { background: 'color-mix(in srgb, var(--color-success) 18%, transparent)', color: 'var(--color-success)' }
          : status === 'active' ? accentChip
          : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
        {status === 'done' ? <Check size={13} />
          : status === 'active' ? <Loader2 size={13} className="animate-spin" />
          : <span className="size-1.5 rounded-full bg-current opacity-60" />}
      </span>
      <span data-type="body-s"
        style={{ color: status === 'pending' ? 'var(--color-on-surface-low)' : 'var(--color-on-surface)' }}>
        {label}
      </span>
    </div>
  )
}

/** The modal itself — mount ONCE in the app shell. Renders nothing until an
 *  update or restart starts; then a centered sheet with either the 4-step
 *  progression (full update) or a single "Restarting gateway" spinner (plain
 *  restart), and how the update ended. */
export function UpdateProgressOverlay() {
  const { progress, actions } = useUpdateProgress()
  return createPortal(
    <AnimatePresence>
      {progress && <UpdateSheet progress={progress} actions={actions} />}
    </AnimatePresence>,
    document.body,
  )
}

/** The overlay's sheet, split out so `useFocusTrap` mounts and unmounts WITH THE DIALOG.
 *
 *  The hook is a mount/unmount contract (its effect has a `[]` dep list, and it captures the
 *  previously-focused element during its FIRST RENDER). Calling it in `UpdateProgressOverlay`
 *  would run it at app start — no dialog, a null ref, and the "restore focus on close" capture
 *  taken from whatever happened to be focused when the app booted.
 *
 *  Why this needed fixing at all: the sheet already declared `role="alertdialog"` +
 *  `aria-modal="true"`, which is a PROMISE that focus is owned. Measured on the live DOM before:
 *  focus stayed on `<body>` when the overlay appeared, and ONE Tab press landed on the nav's
 *  "Home" button behind the scrim. Its two sibling dialogs (`Modal`, `dialog/DialogShell`) both
 *  use this hook; this one declared the contract without honouring it.
 *
 *  What the sheet says is in two live regions mounted with it, so a change of what it says is
 *  announced like a toast (`Toaster`): progress and a cancel that changed nothing politely, a
 *  failure (an `error`, a cancel that left something to put right) and a refused Cancel
 *  assertively. */
function UpdateSheet({ progress, actions }: { progress: UpdateProgress; actions: UpdateActions }) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const stepIdx = STEPS.findIndex((s) => s.id === progress.phase)
  const isError = progress.phase === 'error'
  const isDone = progress.phase === 'done'
  const isCancelled = progress.phase === 'cancelled'
  const isRestartOnly = progress.restartOnly
  const ended = ENDED.has(progress.phase)
  const canCancel = !isRestartOnly && CANCELLABLE.has(progress.phase)
  const finishing = !ended && !isDone && !canCancel

  // A step that swaps the sheet's button (Cancel for Hide, or for Dismiss once it has ended)
  // unmounts the one that had focus, which leaves focus on the page behind the scrim or on the
  // sheet itself. It goes to the sheet's button instead.
  useEffect(() => {
    const root = trapRef.current
    const active = document.activeElement
    if (!root || (active !== root && root.contains(active))) return
    root.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus()
  }, [progress.phase, trapRef])

  // Title adapts: restart-only vs full update vs error vs cancelled vs done
  const title = isError
    ? (isRestartOnly ? 'Restart failed' : 'Update failed')
    : isCancelled
      ? 'Update cancelled'
      : isDone
        ? (isRestartOnly ? 'Restart complete' : 'Update complete')
        : progress.cancelling
          ? 'Cancelling the update'
          : isRestartOnly
            ? 'Restarting gateway'
            : 'Updating PersonalClaw'

  const detail = progress.cancelling
    ? 'Stopping the update…'
    : isRestartOnly && !ended && !isDone ? restartOnlyDetail(progress.detail) : progress.detail
  const urgent = isError ? detail : progress.refused || ''
  const calm = isError ? '' : detail

  return (
    <motion.div key="update-overlay" className="fixed inset-0 z-[var(--z-modal)] flex items-center justify-center p-2xl"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
          <div className="absolute inset-0 bg-canvas/70 backdrop-blur-sm" />
          <motion.div ref={trapRef} role="alertdialog" aria-modal="true" aria-label="Update progress"
            className="relative w-full max-w-[400px] overflow-hidden rounded-xl bg-surface shadow-sheet"
            initial={{ opacity: 0, scale: 0.97, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: 6 }} transition={physics.playful}>
            <div className="flex items-start gap-m px-l pt-l">
              <span className="mt-0.5 shrink-0" style={{ color: isError ? 'var(--color-danger)' : isDone ? 'var(--color-success)' : isCancelled ? 'var(--color-on-surface-var)' : 'var(--color-primary)' }}>
                {isError ? <AlertTriangle size={18} />
                  : isDone ? <CheckCircle2 size={18} />
                  : isCancelled ? <Ban size={18} />
                  : isRestartOnly ? <RefreshCw size={18} className="animate-spin" />
                  : <DownloadCloud size={18} />}
              </span>
              <div className="min-w-0 flex-1">
                <div data-type="title-l" className="text-on-surface">{title}</div>
                {/* Both regions stay mounted while the sheet is: a live region created with its
                    words is not reliably announced. An empty one takes no space. */}
                <div role="status" aria-live="polite" data-type="body-s" className={calm ? 'mt-xs' : undefined}
                  style={{ color: isCancelled ? 'var(--color-on-surface)' : 'var(--color-on-surface-var)' }}>
                  {calm}
                </div>
                <div role="alert" aria-live="assertive" data-type="body-s" className={urgent ? 'mt-xs' : undefined}
                  style={{ color: 'var(--color-danger)' }}>
                  {urgent}
                </div>
              </div>
            </div>

            {/* Full 4-step stepper only while a REAL update runs (not restart-only, not ended) */}
            {!ended && !isRestartOnly && (
              <div className="mt-l flex flex-col gap-2.5 px-l">
                {STEPS.map((s, i) => (
                  <StepRow key={s.id} label={s.label}
                    status={isDone || i < stepIdx ? 'done' : i === stepIdx ? 'active' : 'pending'} />
                ))}
              </div>
            )}

            <div className="flex items-center justify-end gap-m px-l py-l">
              {finishing && !isRestartOnly && !progress.refused && (
                <span data-type="body-s" className="mr-auto text-on-surface-var">
                  The new release is installed, so this can no longer be cancelled.
                </span>
              )}
              {canCancel && (
                <Button variant="secondary" size="sm" onClick={actions.cancel} loading={!!progress.cancelling}>
                  Cancel
                </Button>
              )}
              {ended && (
                <Button variant="secondary" size="sm" onClick={actions.dismiss}>Dismiss</Button>
              )}
              {finishing && (
                <Button variant="secondary" size="sm" onClick={actions.hide}>Hide</Button>
              )}
            </div>
      </motion.div>
    </motion.div>
  )
}
