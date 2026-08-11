import { useCallback, useEffect, useState } from 'react'
import { MessageSquarePlus, Send, Gavel } from 'lucide-react'
import { Button } from '../../ui/Button'
import { TextArea } from '../../ui/forms'
import { QuietButton } from '../../ui/QuietButton'
import { api, type WorkflowNodeState } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { promptForm } from '../../ui/dialog'
import { nodeLabel } from './workflowMeta'
import { canSteerComment, judgeComment, steerTextFromComment } from './steeringMeta'

/** Mid-run steering + judge-comment triage for a live workflow run (LOOPS-EVOLUTION R14 /
 *  criterion 8).
 *
 *  Three affordances, one panel, because they are one conversation with a running job:
 *   • the INTERRUPT QUEUE — a free-text instruction the user queues (`/steer`), consumed at
 *     the next iteration boundary. Rendered from `/steering` so a queued instruction the user
 *     cannot see is not indistinguishable from one that was dropped (they would queue it
 *     again).
 *   • JUDGE-COMMENT TRIAGE — a node whose judge flagged it (a degraded reason, a verification
 *     failure) offers an "Accept & steer" that sends that comment to the worker verbatim, so
 *     an accepted judge comment literally reaches the worker session via the same queue.
 *   • the PER-PROJECT JUDGE GUIDANCE override — standing guidance that rides the project
 *     context block into this run's worker AND judge sessions (there is no dedicated judge-
 *     prompt store; the project's instructions are the real, shipped channel that reaches
 *     both). Shown only for a project-scoped run.
 *
 *  Only mounted while the run is live — a terminal run cannot act on any of this, and the
 *  backend refuses a steer on one anyway, so offering the controls there would teach the user
 *  the UI lies. */
export function SteeringPanel({
  runId,
  projectId,
  nodes,
  onSteered,
}: {
  runId: string
  projectId?: string
  nodes: WorkflowNodeState[]
  onSteered?: () => void
}) {
  const [pending, setPending] = useState<Array<{ text: string; queued_at: string }>>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  const refetch = useCallback(async () => {
    try {
      const res = await api.workflowSteering(runId)
      setPending(res.pending ?? [])
    } catch {
      /* a transient read keeps the last list rather than blanking it */
    }
  }, [runId])
  useEffect(() => { refetch() }, [refetch])

  const steer = useCallback(async (text: string) => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      const res = await api.steerWorkflowRun(runId, { text: t })
      if (res.ok === false) {
        notify(res.error?.message ?? 'Could not queue that instruction.')
        return
      }
      setDraft('')
      await refetch()
      onSteered?.()
    } catch (e) {
      // A failed steer keeps the draft so the user can retry rather than silently losing it.
      notify(e instanceof Error ? e.message : 'Could not queue that instruction.')
    } finally {
      setBusy(false)
    }
  }, [busy, runId, refetch, onSteered])

  // Nodes carrying a judge comment worth acting on (a failure cause/remediation or a degraded
  // reason). The run is live by construction here (the panel only mounts then), so every one
  // is steerable.
  const flagged = nodes.filter((n) => canSteerComment(n, true))

  const setJudgeGuidance = useCallback(async () => {
    if (!projectId) return
    // The project's own agent-instructions template IS the standing guidance that reaches
    // both the worker and the isolated judge sessions through the project context block —
    // there is no separate judge-prompt store to write, so this is the real channel, scoped
    // to every run under the project rather than this one alone (which the label makes plain).
    let current = ''
    try {
      current = (await api.project(projectId)).agent_instructions_template ?? ''
    } catch {
      /* fall through with an empty field — the PUT still lands the new value */
    }
    const answers = await promptForm({
      title: 'Judge guidance for this project',
      body: 'Standing guidance for every run under this project — it reaches the worker and the judge. Applies to future cycles, not the ones already scored.',
      fields: [{
        name: 'guidance',
        label: 'Guidance',
        type: 'textarea',
        initial: current,
        placeholder: 'e.g. Prefer primary sources; reject a summary that cites none.',
      }],
      confirmLabel: 'Save guidance',
    })
    if (answers === null) return
    try {
      await api.updateProject(projectId, { agent_instructions_template: answers.guidance ?? '' })
      notify('Judge guidance saved for this project.')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not save the guidance.')
    }
  }, [projectId])

  return (
    <div className="flex flex-col gap-l">
      {/* Judge-comment triage: accept a flagged node's comment and it becomes a steer. */}
      {flagged.length > 0 && (
        <div className="flex flex-col gap-s">
          <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Judge comments</div>
          {flagged.map((n) => {
            const comment = judgeComment(n)
            return (
              <div key={n.instance_path} className="rounded-lg bg-surface-high px-m py-2">
                <div className="text-on-surface text-[0.8125rem]">{nodeLabel(n)}</div>
                <div className="mt-0.5 text-on-surface-low text-[0.75rem]">{comment}</div>
                <div className="mt-1.5 flex justify-end">
                  <QuietButton
                    onClick={() => steer(steerTextFromComment(nodeLabel(n), comment))}
                    title="Send this feedback to the worker — applied at the next iteration"
                  >
                    <Send size={12} /> Accept &amp; steer
                  </QuietButton>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* The interrupt queue — a free-text instruction consumed at the next boundary. */}
      <div className="flex flex-col gap-s">
        <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Steer this run</div>
        <TextArea
          value={draft}
          onChange={setDraft}
          rows={3}
          ariaLabel="Steering instruction"
          placeholder="Guide the next iteration — focus an angle, or answer what the run is stuck on."
        />
        <div className="flex justify-end">
          <Button size="sm" onClick={() => steer(draft)} disabled={!draft.trim() || busy}
            disabledReason={!draft.trim() ? 'Write a steering note first' : undefined}>
            <MessageSquarePlus size={14} /> {busy ? 'Queuing…' : 'Queue instruction'}
          </Button>
        </div>
        {pending.length > 0 && (
          <div className="rounded-lg px-m py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-info) 8%, transparent)', border: '1px dashed color-mix(in srgb, var(--color-info) 30%, transparent)' }}>
            <div className="flex items-center gap-1.5 text-info text-[0.75rem] uppercase tracking-wide mb-1">
              <MessageSquarePlus size={12} /> queued — applies next iteration
            </div>
            {pending.map((p, i) => <p key={i} className="text-on-surface-var">{p.text}</p>)}
          </div>
        )}
      </div>

      {/* Per-project standing judge guidance — only for a project-scoped run. */}
      {projectId && (
        <div className="flex flex-col gap-s">
          <div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide">Project</div>
          <QuietButton onClick={setJudgeGuidance} title="Standing guidance for every run under this project — reaches the worker and the judge">
            <Gavel size={12} /> Judge guidance…
          </QuietButton>
        </div>
      )}
    </div>
  )
}
