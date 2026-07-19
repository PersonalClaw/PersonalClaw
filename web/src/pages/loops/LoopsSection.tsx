import { useEffect, useState } from 'react'
import { LoopsListPage } from './LoopsListPage'
import type { LoopDraft } from './loopDraft'
import { LoopPlanReview } from './LoopPlanReview'
import { LoopPlanningView } from './LoopPlanningView'
import { LoopCockpitPage } from './LoopCockpitPage'
import { DesignCockpitPage } from './DesignCockpitPage'
import type { RouteProps } from '../../app/useQueryState'
import { api, type Loop } from '../../lib/api'
import { invalidateCache } from '../../lib/useCachedData'

/** Goal navigation — URL-driven so loops are deep-linkable, shareable, and
 *  survive refresh (the user can re-open a loop by its link):
 *    #/loops            → the NEW goal composer (the default landing)
 *    #/loops/history    → the goal list (history)
 *    #/loops/<id>       → that loop's cockpit
 *  The Plan Review is a transient post-create step (it holds an in-memory draft),
 *  so it lives in component state rather than the URL; once launched the cockpit
 *  is the durable address. `sub` is the path segment after `loops/`. */
export function LoopsSection({ sub, navigate, query, setQuery }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  const [review, setReview] = useState<LoopDraft | null>(null)
  // A draft whose stepwise planning walkthrough is running — shows the live
  // Planning view until the plan lands (status review), then flips to Plan Review.
  const [planningId, setPlanningId] = useState<string | null>(null)

  // Escape: an explicit navigation to history or a DIFFERENT concrete loop must
  // win over a lingering in-memory review/planning (don't trap the navigation).
  useEffect(() => {
    if (review && (seg === 'history' || (seg && seg !== review.loopId))) setReview(null)
  }, [seg, review])
  // The bare #/loops composer folded into the unified Loop front door (#/loop) —
  // redirect there unless we're mid create→plan→review (an in-memory draft/planning
  // id still needs this section's Plan Review/walkthrough). Bare #/loops stays: it's
  // the transient plan-review address and internal onBack target, not a legacy alias.
  useEffect(() => {
    if (!seg && !review && !planningId) navigate('loop', { replace: true })
  }, [seg, review, planningId, navigate])
  useEffect(() => {
    if (planningId && (seg === 'history' || (seg && seg !== planningId))) setPlanningId(null)
  }, [seg, planningId])

  // Resume the right PRE-EXECUTION surface on a direct URL/refresh/list-click: a
  // loop still in `planning` reopens the walkthrough, one in `review` opens Plan
  // Review — NOT the cockpit (which is for launched loops). Mirrors CodeSection.
  const [resume, setResume] = useState<{ id: string; status: 'planning' | 'review'; loop: Loop } | null>(null)
  useEffect(() => {
    let alive = true
    const concrete = seg && seg !== 'history'
    if (!concrete || planningId === seg || review?.loopId === seg) { setResume(null); return }
    api.uLoop(seg).then((l) => {
      if (!alive) return
      setResume(l && (l.status === 'planning' || l.status === 'review') ? { id: seg, status: l.status as 'planning' | 'review', loop: l } : null)
    }).catch(() => { if (alive) setResume(null) })
    return () => { alive = false }
  }, [seg, planningId, review])

  // Build a Plan-Review draft from a resumed unified Loop. The classification carries
  // the role-phased execution_plan in kind_config (LoopPlanReview reads it from there),
  // matching the composer's create payload — no flat legacy fields.
  const draftFromLoop = (l: Loop): LoopDraft => {
    const kc = (l.kind_config || {}) as Record<string, unknown>
    return {
      loopId: l.id,
      classification: {
        kind: 'goal', execution: l.execution ?? 'solo', roster: l.roster,
        plan: l.plan ?? [], success_criteria: l.success_criteria ?? undefined,
        kind_config: kc,
      } as LoopDraft['classification'],
      rigor: l.intake_rigor || 'grill', agent: '', model: '',
      granularity: (kc.granularity as LoopDraft['granularity']) || 'balanced', attended: !!l.attended,
    }
  }

  const activePlanningId = planningId || (resume?.status === 'planning' ? resume.id : null)
  if (activePlanningId && !review) {
    return <LoopPlanningView loopId={activePlanningId}
      // Design's review surface IS its cockpit (phase trail + tokens + Start), NOT the
      // goal-shaped LoopPlanReview (Goal-type/sub-goals/granularity would mislabel it
      // and clobber its kind_config on save). Route a finished design walkthrough
      // straight to the cockpit; every other kind goes to Plan Review.
      onReady={(l) => { setPlanningId(null); setResume(null); if (l.kind === 'design') navigate(`loops/${l.id}`); else setReview(draftFromLoop(l)) }}
      onBack={() => { setPlanningId(null); setResume(null); navigate('loops') }} />
  }

  // Plan Review — from the create flow's in-memory draft, OR resumed from a
  // `review`-status loop reached by URL/list. Design is excluded (its cockpit is its
  // review): a resumed design loop in `review` opens the cockpit via CockpitRouter below.
  const reviewDraft = review || (resume?.status === 'review' && resume.loop.kind !== 'design' ? draftFromLoop(resume.loop) : null)
  if (reviewDraft) {
    // Invalidate the SWR list cache on both exits — the draft was persisted at create
    // and launch flips ready→running, so the list (useCachedData('loops')) would else
    // paint a stale snapshot (missing the draft / still 'ready') until its next poll.
    // Mirrors the Code section's fix + the list's own in-place mutations.
    return <LoopPlanReview draft={reviewDraft}
      onBack={() => { setReview(null); setResume(null); invalidateCache('loops'); navigate('loops/history') }}
      onLaunched={(id) => { setReview(null); setResume(null); invalidateCache('loops'); navigate(`loops/${id}`) }} />
  }

  // #/loops/history → the goal list (history). A "new goal" button sits on top.
  if (seg === 'history') {
    return <LoopsListPage onCreate={() => navigate('loops')} onOpen={(id) => navigate(`loops/${id}`)} query={query} setQuery={setQuery} />
  }

  // #/loops/<id> → cockpit (deep-linkable; refresh-safe). The router fetches the loop
  // once and dispatches by kind: a design loop renders the dedicated Design cockpit
  // (live canvas/tokens/palette/exports); every other kind renders the goal-style
  // cockpit (which renders any kind's spine). The removed legacy alias #/loops/new
  // falls through here and gets the router's honest missing-loop state.
  if (seg) {
    return <CockpitRouter key={seg} id={seg} navigate={navigate} query={query} setQuery={setQuery} />
  }

  // bare #/loops → redirected to #/loop (the unified composer) by the effect above.
  // Render nothing during the redirect tick.
  return null
}

/** Fetches a loop by id ONCE and dispatches to the kind-appropriate cockpit. Keeping
 *  the kind in this component's own state (not a sibling state in LoopsSection) avoids
 *  a parallel-state render race: the goal cockpit would otherwise mount first and never
 *  yield to the design cockpit. */
function CockpitRouter({ id, navigate, query, setQuery }: { id: string } & Pick<RouteProps, 'navigate' | 'query' | 'setQuery'>) {
  const [kind, setKind] = useState<Loop['kind'] | null>(null)
  const [missing, setMissing] = useState(false)
  useEffect(() => {
    let alive = true
    setKind(null); setMissing(false)
    api.uLoop(id).then((l) => { if (alive) setKind(l?.kind ?? null) })
      .catch(() => { if (alive) setMissing(true) })
    return () => { alive = false }
  }, [id])

  if (missing) {
    return (
      <div className="grid h-full place-items-center text-on-surface-low text-sm">
        This loop no longer exists.
      </div>
    )
  }
  if (kind === null) {
    return <div className="grid h-full place-items-center text-on-surface-low text-sm">Loading…</div>
  }
  if (kind === 'design') {
    return <DesignCockpitPage id={id}
      onBack={() => navigate('loops/history')}
      onDeleted={() => { invalidateCache('loops'); navigate('loops/history') }}
      onOpenProject={(pid) => navigate(`projects/${pid}`)}
      // D4 agentic build: open a project-bound chat seeded to build/mix components for
      // THIS design system on the canvas. The seed hands the agent the loop id (so its
      // react artifacts tag loop:<id> → render on the canvas) + the token contract; the
      // project binding gives it the shared context dir (DESIGN.md, token_overrides).
      onBuildWithChat={(l) => {
        const seed = `Help me build and refine components for the design system in design loop \`${l.id}\`. `
          + `Read its current tokens via the design token set, and when you create a React component, save it with `
          + `artifact_save(kind='react', tags=['loop:${l.id}']) so it renders live on this loop's canvas. `
          + `Style every component from the design system's token values. First, what component should we build?`
        // Scope the chat to the loop's project (explicit project_id, else the
        // auto-provisioned tasks_project_id) so it shares the design system's context
        // dir; empty when neither (a project-less but still loop-aware chat).
        const pid = l.project_id || l.tasks_project_id || ''
        // Route to chat/new (a fresh chat) so the seed always lands — a bare #/chat with
        // no project falls to the history page (seed lost). project= is honored on both
        // the project-bound and project-less new-chat paths.
        navigate(`chat/new?project=${encodeURIComponent(pid)}&seed=${encodeURIComponent(seed)}`)
      }}
      query={query} setQuery={setQuery} />
  }
  return <LoopCockpitPage id={id}
    onBack={() => navigate('loops/history')}
    // Invalidate the cached list before navigating so the deleted loop doesn't ghost
    // in the list (SWR paints the stale snapshot on mount). Matches the Code fix.
    onDeleted={() => { invalidateCache('loops'); navigate('loops/history') }}
    onOpenArtifact={(slug) => navigate(`files/${slug}`)}
    onOpenTask={(taskId) => navigate(`tasks?open=${encodeURIComponent(taskId)}`)}
    onOpenProject={(pid) => navigate(`projects/${pid}`)}
    query={query} setQuery={setQuery} />
}
