import { Check, ListChecks, GitBranch, Boxes, Lightbulb } from 'lucide-react'
import { PlanningWalkthrough, ArtifactSection, artifactList, artifactStrings, type WalkthroughConfig } from '../../ui/PlanningWalkthrough'
import { api, type Loop } from '../../lib/api'
import { PlanningArtifactDoc } from '../loops/PlanningArtifactDoc'
import type { CommentTarget } from '../../ui/content/commentTarget'

/** The Code SDLC planning walkthrough — the shared PlanningWalkthrough wired to the
 *  unified loop plan API + a per-kind SDLC artifact renderer (requirements/design/
 *  context_map/decomposition). The walkthrough shell + gate live in ui/. */
const CFG: WalkthroughConfig = {
  // The unified planner session is keyed loop-plan-<id> (the WS feed filter).
  planSessionKey: (id) => `loop-plan-${id}`,
  api: {
    getSession: (id) => api.uLoopPlanSession(id),
    start: (id) => api.uLoopPlanStart(id),
    retry: (id) => api.uLoopPlanRetry(id),
    approve: (id, sid) => api.uLoopPlanApprove(id, sid),
    comment: (id, sid, text) => api.uLoopPlanComment(id, sid, text),
    edit: (id, sid, md) => api.uLoopPlanEdit(id, sid, md),
    isReady: (id) => api.uLoop(id).then((p) => p.status === 'review').catch(() => false),
  },
  copy: {
    subtitle: 'walking the SDLC steps — approve or comment on each',
    activityLabel: 'Investigation',
    activityEmpty: 'Starting the planner… it reads the workspace, fetches relevant docs, and searches as needed before drafting each step.',
    cancel: 'Cancel and edit the task',
  },
  renderArtifact: (kind, artifact, commentTarget) => <ArtifactView kind={kind} artifact={artifact} commentTarget={commentTarget} />,
}

export function CodePlanningView({ projectId, onReady, onBack }: {
  projectId: string
  onReady: (project: Loop) => void
  onBack: () => void
}) {
  return (
    <PlanningWalkthrough id={projectId} cfg={CFG} onBack={onBack}
      onReady={() => { api.uLoop(projectId).then(onReady).catch(() => {}) }} />
  )
}

/** Render a Code step artifact per its kind: the markdown body always renders as
 *  real markdown, plus kind-specific structured views — requirements → user
 *  stories, design → decisions, context_map → entities, decomposition → the
 *  phase/task breakdown with dependencies. */
function ArtifactView({ kind, artifact, commentTarget }: { kind: string; artifact: Record<string, unknown>; commentTarget?: CommentTarget }) {
  const md = typeof artifact.markdown === 'string' ? artifact.markdown : ''
  const keyPoints = artifactStrings(artifact.key_points)
  const stories = artifactStrings(artifact.stories)
  const decisions = artifactList(artifact.decisions)
  const entities = artifactList(artifact.entities)
  const phases = artifactList(artifact.phases)
  const empty = !md && !keyPoints.length && !stories.length && !decisions.length && !entities.length && !phases.length

  return (
    <div className="flex flex-col gap-3 rounded-lg bg-surface-high/40 p-3 text-[0.8125rem]">
      {md && <PlanningArtifactDoc markdown={md} docId={`code-plan-${kind}`} label={`${kind} plan`} commentTarget={commentTarget} />}

      {(kind === 'requirements' && stories.length > 0) && (
        <ArtifactSection icon={<ListChecks size={13} className="text-primary" />} label={`User stories (${stories.length})`}>
          <ul className="flex flex-col gap-1">
            {stories.map((s, i) => (
              <li key={i} className="flex items-start gap-1.5 text-on-surface-var">
                <Check size={12} className="mt-0.5 shrink-0 text-on-surface-low" />{s}
              </li>
            ))}
          </ul>
        </ArtifactSection>
      )}

      {decisions.length > 0 && (
        <ArtifactSection icon={<Lightbulb size={13} className="text-primary" />} label={`Decisions (${decisions.length})`}>
          <div className="flex flex-col gap-1.5">
            {decisions.map((d, i) => (
              <div key={i} className="rounded-md bg-surface p-2">
                <div className="text-on-surface">{String(d.title || d.decision || d.name || `Decision ${i + 1}`)}</div>
                {!!(d.rationale || d.why) && <div className="text-on-surface-low text-[0.75rem]">{String(d.rationale || d.why)}</div>}
              </div>
            ))}
          </div>
        </ArtifactSection>
      )}

      {entities.length > 0 && (
        <ArtifactSection icon={<Boxes size={13} className="text-primary" />} label={`Entities (${entities.length})`}>
          <div className="flex flex-wrap gap-1.5">
            {entities.map((e, i) => (
              <span key={i} className="rounded-md bg-surface px-2 py-1 text-on-surface-var text-[0.75rem]" title={String(e.description || e.role || '')}>
                {String(e.name || e.title || e.entity || `Entity ${i + 1}`)}
              </span>
            ))}
          </div>
        </ArtifactSection>
      )}

      {phases.length > 0 && (
        <ArtifactSection icon={<GitBranch size={13} className="text-primary" />} label={`Execution phases (${phases.length})`}>
          <div className="flex flex-col gap-2">
            {phases.map((p, i) => {
              const tasks = artifactList(p.tasks)
              const exit = artifactStrings(p.exit_criteria)
              return (
                <div key={i} className="rounded-md bg-surface p-2.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-on-surface">{i + 1}. {String(p.title || p.stage || 'Phase')}</span>
                    {!!p.stage && <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.65rem]">{String(p.stage)}</span>}
                  </div>
                  {!!p.objective && <div className="mt-0.5 text-on-surface-low text-[0.75rem]">{String(p.objective)}</div>}
                  {tasks.length > 0 && (
                    <ol className="mt-1.5 flex flex-col gap-1">
                      {tasks.map((t, j) => {
                        const deps = Array.isArray(t.depends_on) ? (t.depends_on as unknown[]).map((x) => Number(x) + 1).filter((n) => !Number.isNaN(n)) : []
                        return (
                          <li key={j} className="flex items-start gap-1.5 text-on-surface-var text-[0.78rem]">
                            <span className="mt-0.5 shrink-0 text-on-surface-low">{i + 1}.{j + 1}</span>
                            <span>
                              {String(t.title || `Task ${j + 1}`)}
                              {!!deps.length && <span className="ml-1 text-on-surface-low text-[0.68rem]">↳ after {deps.map((d) => `${i + 1}.${d}`).join(', ')}</span>}
                            </span>
                          </li>
                        )
                      })}
                    </ol>
                  )}
                  {exit.length > 0 && (
                    <div className="mt-1.5 text-on-surface-low text-[0.7rem]">Done when: {exit.join('; ')}</div>
                  )}
                </div>
              )
            })}
          </div>
        </ArtifactSection>
      )}

      {/* Key points render for every kind EXCEPT requirements, where the stories block
          is the richer view — but if a requirements artifact came back with key_points
          and NO stories (planner output varies), fall back to key points here so the
          box never renders blank with content silently dropped. */}
      {keyPoints.length > 0 && !(kind === 'requirements' && stories.length > 0) && (
        <ArtifactSection icon={<ListChecks size={13} className="text-primary" />} label="Key points">
          <ul className="ml-4 list-disc text-on-surface-var">{keyPoints.map((k, i) => <li key={i}>{k}</li>)}</ul>
        </ArtifactSection>
      )}

      {empty && <span className="text-on-surface-low">No artifact content.</span>}
    </div>
  )
}
