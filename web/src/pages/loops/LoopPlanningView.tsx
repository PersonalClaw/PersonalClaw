import { ListChecks, Users, GitBranch, Target } from 'lucide-react'
import { PlanningWalkthrough, ArtifactSection, artifactList, artifactStrings, type WalkthroughConfig } from '../../ui/PlanningWalkthrough'
import { api, type Loop } from '../../lib/api'
import { DesignStepPreview } from './DesignStepPreview'
import { PlanningArtifactDoc } from './PlanningArtifactDoc'
import type { CommentTarget } from '../../ui/content/commentTarget'

/** The unified planning walkthrough (goal/general/design) — the shared
 *  PlanningWalkthrough wired to the loop plan API + a per-kind artifact renderer.
 *  Built as a factory so the renderer closes over the loopId (the design token-step
 *  preview needs it to merge overrides + fetch the live whole-system preview). */
function makeCfg(loopId: string): WalkthroughConfig {
  return {
    // The unified planner session is keyed loop-plan-<id> for EVERY kind (the WS feed
    // filter must match plan_walkthrough.planner_session_key); the old goal-plan-<id>
    // key never matched the unified backend, so live planner activity didn't stream.
    planSessionKey: (id) => `loop-plan-${id}`,
    api: {
      getSession: (id) => api.uLoopPlanSession(id),
      start: (id) => api.uLoopPlanStart(id),
      retry: (id) => api.uLoopPlanRetry(id),
      approve: (id, sid) => api.uLoopPlanApprove(id, sid),
      comment: (id, sid, text) => api.uLoopPlanComment(id, sid, text),
      edit: (id, sid, md) => api.uLoopPlanEdit(id, sid, md),
      isReady: (id) => api.uLoop(id).then((l) => l.status === 'review').catch(() => false),
    },
    // Kind-neutral copy — this walkthrough serves every non-code kind (goal/general/
    // design), so it speaks of "the plan"/"the task", not "the goal".
    copy: {
      subtitle: 'shaping the plan with you — approve or comment on each step',
      activityLabel: 'Planner activity',
      activityEmpty: 'Starting the planner… it works out the intent, decomposition, the right agents, and a phased plan — one step at a time.',
      cancel: 'Cancel and edit the task',
    },
    renderArtifact: (kind, artifact, commentTarget) => <ArtifactView kind={kind} artifact={artifact} loopId={loopId} commentTarget={commentTarget} />,
  }
}

export function LoopPlanningView({ loopId, onReady, onBack }: {
  loopId: string
  onReady: (loop: Loop) => void
  onBack: () => void
}) {
  return (
    <PlanningWalkthrough id={loopId} cfg={makeCfg(loopId)} onBack={onBack}
      onReady={() => { api.uLoop(loopId).then(onReady).catch(() => {}) }} />
  )
}

/** Render a goal-planning artifact per kind: markdown body + the structured view —
 *  intent → success criteria; sub_goals → list; quorum → roster; execution_plan →
 *  the phased plan. */
function ArtifactView({ kind, artifact, loopId, commentTarget }: { kind: string; artifact: Record<string, unknown>; loopId: string; commentTarget?: CommentTarget }) {
  const md = typeof artifact.markdown === 'string' ? artifact.markdown : ''
  const subGoals = artifactStrings(artifact.sub_goals)
  const roster = artifactList(artifact.roster)
  const phases = artifactList(artifact.execution_plan)
  const sc = typeof artifact.success_criteria === 'string' ? artifact.success_criteria : ''
  // Design token-bearing steps (foundations/palette/typography) carry a token_overrides
  // patch — D3: merge it onto the loop + render the editable whole-system live preview.
  const tokenOverrides = (artifact.token_overrides && typeof artifact.token_overrides === 'object')
    ? artifact.token_overrides as Record<string, unknown> : null
  const empty = !md && !subGoals.length && !roster.length && !phases.length && !sc && !tokenOverrides

  return (
    <div className="flex flex-col gap-3 rounded-lg bg-surface-high/40 p-3 text-[0.8125rem]">
      {md && <PlanningArtifactDoc markdown={md} docId={`plan-${loopId}-${kind}`} label={`${kind} plan`} commentTarget={commentTarget} />}

      {tokenOverrides && <DesignStepPreview loopId={loopId} stepKind={kind} overrides={tokenOverrides} />}

      {kind === 'intent' && sc && (
        <ArtifactSection icon={<Target size={13} className="text-primary" />} label="Definition of done">
          <p className="text-on-surface-var">{sc}</p>
        </ArtifactSection>
      )}
      {subGoals.length > 0 && (
        <ArtifactSection icon={<ListChecks size={13} className="text-primary" />} label={`Sub-goals (${subGoals.length})`}>
          <ol className="ml-4 list-decimal text-on-surface-var">{subGoals.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </ArtifactSection>
      )}
      {roster.length > 0 && (
        <ArtifactSection icon={<Users size={13} className="text-primary" />} label={`Agent quorum (${roster.length})`}>
          <div className="flex flex-col gap-1.5">
            {roster.map((r, i) => (
              <div key={i} className="rounded-md bg-surface p-2">
                <div className="text-on-surface">{String(r.role || r.persona || `Member ${i + 1}`)}</div>
                {!!(r.role_hint || r.persona) && <div className="text-on-surface-low text-[0.75rem]">{String(r.role_hint || r.persona)}</div>}
              </div>
            ))}
          </div>
        </ArtifactSection>
      )}
      {phases.length > 0 && (
        <ArtifactSection icon={<GitBranch size={13} className="text-primary" />} label={`Execution phases (${phases.length})`}>
          <div className="flex flex-col gap-2">
            {phases.map((p, i) => (
              <div key={i} className="rounded-md bg-surface p-2.5">
                <div className="flex items-center gap-1.5">
                  <span className="text-on-surface">{i + 1}. {String(p.role || 'phase')}</span>
                  {!!p.min_cycles && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.65rem]">≥{String(p.min_cycles)} cycles</span>}
                </div>
                {!!p.target && <div className="mt-0.5 text-on-surface-low text-[0.75rem]">{String(p.target)}</div>}
                {!!p.phase_exit && <div className="mt-0.5 text-on-surface-low text-[0.7rem]">Advance when: {String(p.phase_exit)}</div>}
              </div>
            ))}
          </div>
        </ArtifactSection>
      )}
      {empty && <span className="text-on-surface-low">No artifact content.</span>}
    </div>
  )
}
