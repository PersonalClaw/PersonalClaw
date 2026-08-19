import { useCallback, useEffect, useState } from 'react'
import { Check, MessageSquarePlus, Pencil, X } from 'lucide-react'
import { Button } from '../Button'
import { Markdown } from '../Markdown'
import { api, type PlanStep, type TaskMode } from '../../lib/api'

/** The chat's plan review gate (CHAT-CRAFT CC-8).
 *
 *  It renders the CURRENT step of the chat's `planning/session.py` walkthrough — the
 *  same `PlanSession`/`PlanStep` shape and the same approve / comment / edit transitions
 *  the loop and code planners drive. Nothing about the state machine lives here: every
 *  action is one POST that lands on `PS.approve_step` / `PS.comment_step` /
 *  `PS.edit_artifact`, and the server's own `awaiting_step_id` decides whether the gate
 *  is open (so what the user sees and what the tool guard enforces can't disagree).
 *
 *  Why not `ui/PlanningWalkthrough`: that is the full-PAGE walkthrough — a TopBar, a
 *  back contract, and a left rail that streams a *hidden planner session's* tool calls
 *  over the WS. A chat's plan is drafted by the chat's own visible turn, so the chat
 *  needs only the gate half, inline above the composer, and none of the page shell.
 *
 *  Mounted unconditionally by the host and renders NOTHING until a plan session exists,
 *  which is the state of every chat that never used the composer affordance.
 */
export function ChatPlanGate({ session, refreshKey, onTaskMode }: {
  /** Chat session key. */
  session: string
  /** Bumped by the host when a turn lands, so the gate picks up the new draft. */
  refreshKey: number
  /** The server-restored task mode after approve/cancel — the host syncs its pill. */
  onTaskMode: (mode: TaskMode) => void
}) {
  const [step, setStep] = useState<PlanStep | null>(null)
  const [awaiting, setAwaiting] = useState('')
  const [parked, setParked] = useState(false)
  const [comment, setComment] = useState('')
  const [editText, setEditText] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api.chatPlanSession(session).then((d) => {
      const steps = d.session?.steps ?? []
      // The step the walkthrough is ON = the first not-yet-approved one, the same
      // definition as planning.session.current_step.
      setStep(steps.find((s) => s.status !== 'approved') ?? null)
      setAwaiting(d.awaiting_step_id)
      setParked(!!d.binding?.parked)
    }).catch(() => { setStep(null); setAwaiting('') })
  }, [session])
  useEffect(load, [load, refreshKey])
  // A new draft replaces whatever was being written about the old one.
  useEffect(() => { setComment(''); setEditText(null); setErr('') }, [awaiting])

  if (!step) return null
  const open = awaiting === step.id
  const markdown = typeof step.artifact?.markdown === 'string' ? step.artifact.markdown : ''

  async function run(fn: () => Promise<unknown>) {
    if (busy) return
    setBusy(true); setErr('')
    try { await fn() } catch (e) { setErr(String((e as Error)?.message || e)) } finally { setBusy(false); load() }
  }

  return (
    <div className="mb-1 rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3">
      <div className="mb-1.5 flex items-center gap-2">
        <span data-type="title-s" className="text-on-surface">{step.title}</span>
        <span className="rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] text-on-surface-var">
          {open ? 'Awaiting your review' : 'Drafting…'}
        </span>
        {parked && (
          <span className="rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] text-on-surface-var">
            Run parked — resumes when you approve
          </span>
        )}
      </div>
      {step.objective && <p className="mb-2 text-[0.8125rem] text-on-surface-low">{step.objective}</p>}
      {!open ? (
        <p className="text-[0.8125rem] text-on-surface-low">
          Nothing runs until you approve. The plan appears here when this turn finishes.
        </p>
      ) : editText !== null ? (
        <div className="flex flex-col gap-2">
          <textarea autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} rows={12}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void run(() => api.chatPlanEdit(session, step.id, editText)) } }}
            aria-label="Plan markdown"
            placeholder="Write the plan in markdown…"
            className="w-full resize-y rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 font-mono text-[0.75rem] text-on-surface outline-none placeholder:text-on-surface-low focus:border-primary" />
          <div className="flex items-center gap-2">
            <Button size="xs" onClick={() => void run(() => api.chatPlanEdit(session, step.id, editText))} disabled={busy}>
              <Check size={14} /> Save edits
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setEditText(null)} disabled={busy}>
              <X size={14} /> Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <div className="group/plan relative">
            {/* Faintly visible rather than hover-only: a hover-gated action is
                unreachable on touch (the PlanningWalkthrough edit affordance, same class). */}
            <Button size="xs" variant="ghost" className="absolute right-0 top-0 z-10 opacity-60 group-hover/plan:opacity-100"
              onClick={() => setEditText(markdown)} ariaLabel="Edit this plan">
              <Pencil size={12} /> Edit
            </Button>
            <Markdown className="[&_p]:text-[0.8125rem]">{markdown}</Markdown>
          </div>
          {!!step.comments?.length && (
            <div className="mt-2 flex flex-col gap-1.5">
              {step.comments.map((c, i) => (
                <div key={i} className="flex items-start gap-1.5 rounded-lg border border-outline-variant/40 bg-surface-container/40 px-2.5 py-1.5 text-[0.75rem]">
                  <MessageSquarePlus size={12} className="mt-0.5 shrink-0 text-on-surface-low" />
                  <span className="min-w-0 whitespace-pre-wrap text-on-surface-var">{c.text}</span>
                </div>
              ))}
            </div>
          )}
          <div className="mt-2 flex flex-col gap-2">
            <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2}
              onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && comment.trim()) { e.preventDefault(); void run(() => api.chatPlanComment(session, step.id, comment.trim())) } }}
              aria-label="Comment on this plan"
              placeholder="Comment to refine the plan (⌘↵ to send), or approve as-is…"
              className="w-full resize-none rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 text-[0.8125rem] text-on-surface outline-none focus:border-primary" />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="xs" disabled={busy}
                onClick={() => void run(async () => { const r = await api.chatPlanApprove(session, step.id); if (r.complete) onTaskMode(r.task_mode) })}>
                <Check size={14} /> Approve &amp; run it
              </Button>
              <Button size="xs" variant="secondary" disabled={busy || !comment.trim()}
                disabledReason={!comment.trim() ? 'Write a comment first' : undefined}
                onClick={() => void run(() => api.chatPlanComment(session, step.id, comment.trim()))}>
                <MessageSquarePlus size={14} /> Send comment &amp; redraft
              </Button>
              <Button size="xs" variant="ghost" disabled={busy}
                onClick={() => void run(async () => { const r = await api.chatPlanCancel(session); onTaskMode(r.task_mode) })}>
                <X size={14} /> Cancel plan mode
              </Button>
            </div>
          </div>
        </>
      )}
      {err && <p role="alert" className="mt-2 text-[0.8125rem]" style={{ color: 'var(--color-danger)' }}>{err}</p>}
    </div>
  )
}
