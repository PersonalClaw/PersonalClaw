import { useEffect, useRef, useState } from 'react'
import { api, type CodeProject } from '../../../../lib/api'
import { useOnScreenReport } from '../../CodeToast'
import { autoGrowTextarea } from '../cockpitShared'
import { missingCommandNotices } from '../cockpitCommands'

// ── The cockpit footer's controller ───────────────────────────────────────────────────────────
//
// What the project's footer keeps and does: the project-level steer box, guarded against a double
// send, with the steers you sent shown until the run confirms them and a failed one said and its
// text kept; the cursor taken when the toast's Respond asks for it; whether the worker itself is
// asking; and Resume for a loop a spend cap paused. `ProjectFooter.tsx` draws it.

export function useProjectFooter({ project, onNudged, onAnswerOnScreen, answerWanted, onAnswerShown }: {
  project: CodeProject; onNudged: () => void; onAnswerOnScreen?: (onScreen: boolean) => void
  answerWanted?: boolean; onAnswerShown?: () => void
}) {
  // The answer panel reports whether it is on screen, so the cockpit's toast that points here
  // never stands over it (`CodeToast`).
  const answerRef = useOnScreenReport(onAnswerOnScreen)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  // Synchronous double-send guard (sending state is async — rapid double Enter/click
  // both pass before the re-render → a double nudge). Mirrors the task steer's guard.
  const sendingRef = useRef(false)
  // The steer textarea — focused when the user clicks "Respond" on the needs-input
  // toast, so answering is one click away wherever they were.
  const steerRef = useRef<HTMLTextAreaElement>(null)
  // Auto-grow the box to fit a multi-line steer (and shrink back after send clears it).
  useEffect(() => { autoGrowTextarea(steerRef.current) }, [text])
  // A standing Respond, whether this footer was on screen when it was pressed or mounted after it
  // (the rail opened, a task's detail closed): take the cursor, then say it is done.
  useEffect(() => {
    if (!answerWanted) return
    steerRef.current?.focus()
    steerRef.current?.scrollIntoView({ block: 'nearest' })
    onAnswerShown?.()
  }, [answerWanted, onAnswerShown])
  const [steers, setSteers] = useState<{ text: string; failed?: boolean }[]>([])
  const findings = project.findings ?? []
  const missingCommands = missingCommandNotices(project.command_runnability)
  // The worker asked something only when the pause is its question: a merge waiting, a conflict and
  // a spend cap are the scheduler's, each answered on its own card above, not in the steer box; a
  // wait after your Deny is the loop's, which your steer settles.
  const pq = project.pending_question
  const workerAsks = project.status === 'needs_input' && !pq?.merge && !pq?.conflict && !pq?.spend_cap && !pq?.declined

  // Resume a loop a spend cap paused, from the notice that says so (the header's Resume does the same).
  const [resuming, setResuming] = useState(false)
  async function resume() {
    if (resuming) return
    setResuming(true)
    try { await api.uLoopAction(project.id, 'resume'); onNudged() }
    catch (e) {
      window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't resume this project: ${(e as Error).message || 'unknown error'}` } }))
    } finally { setResuming(false) }
  }

  async function steer(explicit?: string) {
    const t = (explicit ?? text).trim()
    if (!t || sending || sendingRef.current) return
    sendingRef.current = true
    setSending(true)
    setSteers((s) => [...s, { text: t }])
    if (explicit === undefined) setText('')
    try { await api.uLoopNudge(project.id, t); onNudged() }
    catch {
      setSteers((s) => s.map((m, i) => (i === s.length - 1 ? { ...m, failed: true } : m)))
      // Restore the typed text so a failed send isn't lost — the user shouldn't have
      // to retype a (possibly long) steer because the worker was briefly unreachable.
      if (explicit === undefined) setText((cur) => cur || t)
    }
    finally { setSending(false); sendingRef.current = false }
  }

  const lastSteer = (() => {
    const persisted = project.nudges ?? []
    // The last optimistic steer is still "pending" (not yet in persisted) ONLY if it
    // hasn't been confirmed by the backend. Count by SUCCESSFUL optimistic sends, not
    // steers.length: a FAILED steer never enters `persisted`, so slicing by raw length
    // would drift after any failure — making later successful steers show their
    // optimistic copy (no "applied cycle N") forever instead of the confirmed one. A
    // failed steer surfaces only when it's genuinely the latest event.
    const lastOptimistic = steers[steers.length - 1]
    const succeededCount = steers.filter((s) => !s.failed).length
    // A trailing failure, or more successful sends than persisted yet → show optimistic.
    if (lastOptimistic && (lastOptimistic.failed || succeededCount > persisted.length)) return lastOptimistic
    return persisted[persisted.length - 1] || lastOptimistic || null
  })()
  return {
    answerRef, text, setText, sending, steerRef, findings, missingCommands, workerAsks, resuming, resume,
    steer, lastSteer,
  }
}
