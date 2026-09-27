import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, MessageSquarePlus, Pencil, X } from 'lucide-react'
import { Button } from '../Button'
import { Markdown } from '../Markdown'
import { StaleWriteNotice } from '../StaleWriteNotice'
import { api, type PlanStep, type TaskMode } from '../../lib/api'
import { HELD_CHANGE_REASON, rebaseText, type Revisioned } from '../../lib/staleWrite'
import { useStaleWriteGuard } from '../../lib/useStaleWriteGuard'
import { BUSY_REASON } from '../../ui/unavailable'

/** A step's plan as the page shows it — the markdown an edit replaces — with the revision the same
 *  read reported. */
function draftOf(step: PlanStep): Revisioned<string> {
  return { value: typeof step.artifact?.markdown === 'string' ? step.artifact.markdown : '', revision: step.revision ?? '' }
}

/** The chat's plan review gate.
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
  // 🔴 AN EDIT REPLACES THE WHOLE DRAFT, so it names the draft it was made on: the step's markdown
  // when Edit was clicked, with the revision the same read reported. A comment sent from another tab
  // redrafts the plan while this editor is open, and saving here used to put the old draft — plus
  // the edit — back over the new one without a word. Now that save is refused and offered back
  // (`ui/StaleWriteNotice`), and the change is kept even when a new draft closes the editor.
  const [editBase, setEditBase] = useState<(Revisioned<string> & { step: string }) | null>(null)
  // The step the last edit was saved to: what a refused edit's recovery re-reads and writes, even
  // after the gate has moved on to another step.
  const editedStep = useRef('')
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

  const guard = useStaleWriteGuard<string>({
    read: async () => {
      const stored = (await api.chatPlanSession(session)).session?.steps.find((s) => s.id === editedStep.current)
      if (!stored) throw new Error('this plan step is no longer in the walkthrough')
      return draftOf(stored)
    },
    write: (next, revision) => api.chatPlanEdit(session, editedStep.current, next, revision),
    // Saving RETURNS TO REVIEW (see the Save button below), however the save landed.
    onSaved: () => { setEditText(null); load() },
    onDiscard: () => { setEditText(null); load() },
  })

  if (!step) return null
  const open = awaiting === step.id
  const markdown = draftOf(step).value

  async function run(fn: () => Promise<unknown>) {
    if (busy) return
    setBusy(true); setErr('')
    try { await fn() } catch (e) { setErr(String((e as Error)?.message || e)) } finally { setBusy(false); load() }
  }
  // Not `run`: a refused edit keeps the editor and its text exactly as they are, and a re-read here
  // could close the editor (a redraft in flight changes `awaiting`) while the notice says the change
  // is kept. A landed one re-reads through `onSaved`.
  async function saveEdits(text: string) {
    if (busy || !editBase) return
    setBusy(true); setErr('')
    editedStep.current = editBase.step
    try { await guard.save(editBase, text, rebaseText(editBase.value, text)) }
    catch (e) { setErr(String((e as Error)?.message || e)) }
    finally { setBusy(false) }
  }
  const blocked = guard.conflict !== null

  return (
    <div className="mb-1 rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3">
      <div className="mb-1.5 flex items-center gap-2">
        <span data-type="title-s" className="text-on-surface">{step.title}</span>
        <span data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">
          {open ? 'Awaiting your review' : 'Drafting…'}
        </span>
        {parked && (
          <span data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">
            Run parked — resumes when you approve
          </span>
        )}
      </div>
      {step.objective && <p data-type="body-s" className="mb-2 text-on-surface-low">{step.objective}</p>}
      {!open ? (
        <p data-type="body-s" className="text-on-surface-low">
          Nothing runs until you approve. The plan appears here when this turn finishes.
        </p>
      ) : editText !== null ? (
        <div className="flex flex-col gap-2">
          {/* Read-only while a refused edit is held: what the notice reapplies is the text as it was
              refused, so typing on would be dropped by the reapply. */}
          <textarea autoFocus value={editText} onChange={(e) => setEditText(e.target.value)} rows={12} readOnly={blocked}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !blocked) { e.preventDefault(); void saveEdits(editText) } }}
            aria-label="Plan markdown"
            placeholder="Write the plan in markdown…"
            data-type="caption"
            className="w-full resize-y rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 font-mono text-on-surface outline-none placeholder:text-on-surface-low focus:border-primary" />
          <div className="flex items-center gap-2">
            {/* Saving RETURNS TO REVIEW. Leaving the editor open after a successful save
                left "Cancel" as the only way out — a word that reads as "discard what I
                just wrote", so the one path back to Approve looked like the path that
                threw the edit away. The save persisted and the panel stayed
                in edit mode. */}
            <Button size="xs" onClick={() => void saveEdits(editText)} disabled={busy || blocked}
              disabledReason={blocked ? HELD_CHANGE_REASON : BUSY_REASON}>
              <Check size={14} /> Save edits
            </Button>
            {/* Cancelling a refused edit drops the kept change, exactly as "Discard my change" does. */}
            <Button size="xs" variant="ghost" onClick={() => { if (blocked) guard.discard(); else setEditText(null) }} disabled={busy} disabledReason={BUSY_REASON}>
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
              onClick={() => { setEditBase({ ...draftOf(step), step: step.id }); setEditText(markdown) }} ariaLabel="Edit this plan">
              <Pencil size={12} /> Edit
            </Button>
            <Markdown className="[&_p]:text-[0.8125rem]">{markdown}</Markdown>
          </div>
          {!!step.comments?.length && (
            <div className="mt-2 flex flex-col gap-1.5">
              {step.comments.map((c, i) => (
                <div key={i} data-type="caption" className="flex items-start gap-1.5 rounded-lg border border-outline-variant/40 bg-surface-container/40 px-2.5 py-1.5">
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
              data-type="body-s"
              className="w-full resize-none rounded-lg border border-outline-variant/60 bg-surface px-3 py-2 text-on-surface outline-none focus:border-primary" />
            <div className="flex flex-wrap items-center gap-2">
              <Button size="xs" disabled={busy} disabledReason={BUSY_REASON}
                onClick={() => void run(async () => { const r = await api.chatPlanApprove(session, step.id); if (r.complete) onTaskMode(r.task_mode) })}>
                <Check size={14} /> Approve &amp; run it
              </Button>
              <Button size="xs" variant="secondary" disabled={busy || !comment.trim()}
                disabledReason={!comment.trim() ? 'Write a comment first' : BUSY_REASON}
                onClick={() => void run(() => api.chatPlanComment(session, step.id, comment.trim()))}>
                <MessageSquarePlus size={14} /> Send comment &amp; redraft
              </Button>
              <Button size="xs" variant="ghost" disabled={busy} disabledReason={BUSY_REASON}
                onClick={() => void run(async () => { const r = await api.chatPlanCancel(session); onTaskMode(r.task_mode) })}>
                <X size={14} /> Cancel plan mode
              </Button>
            </div>
          </div>
        </>
      )}
      {/* Outside the branches: a refused edit is kept until the user reapplies or drops it, including
          after a new draft closed the editor — then reapplying puts it onto that new draft. */}
      <StaleWriteNotice guard={guard} what="This plan" className="mt-s" />
      {err && <p role="alert" data-type="body-s" className="mt-2" style={{ color: 'var(--color-danger)' }}>{err}</p>}
    </div>
  )
}
