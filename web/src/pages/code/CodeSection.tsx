import { useEffect, useState } from 'react'
import { Code2, Plus, Loader2, Trash2, FolderOpen, AlertTriangle, X, RotateCcw } from 'lucide-react'
import type { CodeDraft } from './codeDraft'
import { CodePlanReview } from './CodePlanReview'
import { CodePlanningView } from './CodePlanningView'
import { CodeCockpitPage } from './CodeCockpitPage'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Button } from '../../ui/Button'
import { ListControls } from '../../ui/ListControls'
import { FilterMenu, type FilterSectionDef } from '../../ui/FilterMenu'
import { ListSkeleton } from '../../ui/ListScaffold'
import { confirm } from '../../ui/dialog'
import { WorkspacePicker } from './WorkspacePicker'
import { api, sdlcStageLabel, type Loop, type LoopPhase } from '../../lib/api'
import { loopStatusLabel, effectiveLoopStatus, loopStatusTone } from '../../lib/loopStatus'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import type { RouteProps } from '../../app/useQueryState'

/** Code navigation — URL-driven, mirroring the Goal section:
 *    #/code            → the NEW task composer (default landing)
 *    #/code/history    → the project list
 *    #/code/<id>       → that project's cockpit (the mini-IDE)
 *  A freshly composed task becomes a draft, goes through Plan Review, then
 *  launches into the cockpit.
 */
export function CodeSection({ sub, navigate, query, setQuery }: RouteProps) {
  const seg = (sub || '').split('/')[0]
  // A created project awaiting Plan Review takes precedence over the URL (it holds
  // an in-memory draft) until the user launches (→ cockpit) or backs out.
  const [review, setReview] = useState<CodeDraft | null>(null)
  // A draft whose agentic deep planner is running — shows the live Planning view
  // until the plan lands (status review), then it flips into Plan Review.
  const [planningId, setPlanningId] = useState<string | null>(null)

  // An explicit URL navigation to a DIFFERENT, concrete project (sidebar click,
  // notification deep-link, bookmark) or to history must win over a lingering
  // in-memory review — otherwise the review screen traps the user, silently
  // swallowing the navigation. Navigating to the review's OWN project keeps the
  // review (that is the project being reviewed); the composer (`new`/empty) keeps
  // it too (that's where onDraft set it).
  useEffect(() => {
    if (!review) return
    if (seg === 'history' || (seg && seg !== review.projectId)) setReview(null)
  }, [seg, review])
  // The same escape for the planning view (don't trap a navigation away).
  useEffect(() => {
    if (!planningId) return
    if (seg === 'history' || (seg && seg !== planningId)) setPlanningId(null)
  }, [seg, planningId])
  // Bare #/code folded into the unified Loop front door (#/loop) — redirect there
  // unless mid create→plan→review (bare #/code is the transient plan-review address
  // and internal onBack target). The reuse-workspace flow is
  // #/loop?kind=code&ws=<dir> (the cockpit's "New target").
  useEffect(() => {
    if (!seg && !review && !planningId) navigate('loop', { replace: true })
  }, [seg, review, planningId, navigate])

  // Resume the right PRE-EXECUTION surface on a direct URL/refresh/deep-link/list-
  // click: a project still in `intake`/`planning` must reopen the live breakdown
  // walkthrough, and one in `review` (planning done, not launched) must open Plan
  // Review — NOT drop into the (empty) cockpit, which is for launched projects only.
  // We probe the status for a concrete-id route and route accordingly.
  const [resume, setResume] = useState<{ id: string; status: 'planning' | 'review'; project: Loop } | null>(null)
  useEffect(() => {
    let alive = true
    const concrete = seg && seg !== 'history'
    if (!concrete || planningId === seg || review?.projectId === seg) { setResume(null); return }
    api.uLoop(seg).then((p) => {
      if (!alive) return
      // intake (classifier running) + planning both belong in the walkthrough.
      const planningLike = p && (p.status === 'planning' || p.status === 'intake')
      setResume(p && (planningLike || p.status === 'review')
        ? { id: seg, status: (planningLike ? 'planning' : 'review'), project: p } : null)
    }).catch((e) => {
      if (!alive) return
      // Only a real 404 means the project is gone → clear the resume routing and let
      // the cockpit show its own missing state. On a transient error (network blip /
      // 5xx) DON'T clear: clearing would fall a still-planning/review project through
      // to the cockpit (wrong surface — the execution mini-IDE instead of the
      // walkthrough / Plan Review). Keep any prior resume; the effect re-runs + recovers.
      if ((e as { status?: number })?.status === 404) setResume(null)
    })
    return () => { alive = false }
  }, [seg, planningId, review])

  const activePlanningId = planningId || (resume?.status === 'planning' ? resume.id : null)

  if (activePlanningId && !review) {
    return <CodePlanningView projectId={activePlanningId}
      onReady={(p) => { setPlanningId(null); setResume(null); setReview({ projectId: p.id, classification: { stage_plan: p.plan, entry_stage: kindCfg(p).entry_stage, summary: p.summary } as unknown as CodeDraft['classification'], rigor: p.intake_rigor || 'grill', attended: !!p.attended }) }}
      onBack={() => { setPlanningId(null); setResume(null); navigate('code') }} />
  }

  // Plan Review — from the create flow's in-memory draft, OR resumed from a
  // `review`-status project reached by URL/list (build a minimal draft from it).
  const reviewDraft: CodeDraft | null = review || (resume?.status === 'review'
    ? { projectId: resume.id, classification: { stage_plan: resume.project.plan, entry_stage: kindCfg(resume.project).entry_stage, summary: resume.project.summary } as unknown as CodeDraft['classification'], rigor: resume.project.intake_rigor || 'grill', attended: !!resume.project.attended }
    : null)
  if (reviewDraft) {
    // Both exits can follow a create/launch mutation (the draft was persisted during
    // create, and launch flips it ready→running) — invalidate the SWR list cache so the
    // list shows the new project + its fresh status when next visited, instead of the
    // stale snapshot (missing the draft, or showing it still 'ready'). Matches the
    // delete path; the list's mount-revalidate alone would lag a poll behind.
    return <CodePlanReview draft={reviewDraft}
      onBack={() => { setReview(null); setResume(null); invalidateCache('code:projects'); navigate('code/history') }}
      onLaunched={(id) => { setReview(null); setResume(null); invalidateCache('code:projects'); navigate(`code/${id}`) }} />
  }

  if (seg === 'history') {
    return <CodeListPage onCreate={() => navigate('code')} onOpen={(id) => navigate(`code/${id}`)} />
  }

  if (seg) {
    // The cockpit shows its own loader while it fetches the project, so a deep-link
    // to a still-pre-launch project briefly shows that loader, then the resume probe
    // (above) re-routes it to the walkthrough / Plan Review. No wrong content flashes.
    return <CodeCockpitPage key={seg} id={seg}
      onBack={() => navigate('code/history')}
      // Invalidate the cached project list BEFORE navigating to it — else the SWR cache
      // paints the just-deleted project as a ghost row (clickable → "no longer exists")
      // until the next 6s poll. The list's own delete already does this via load(); the
      // cockpit-delete path didn't.
      onDeleted={() => { invalidateCache('code:projects'); navigate('code/history') }}
      // "New target" reuses this project's codebase → the unified composer, pre-set to
      // Code with the workspace pre-bound (the reuse-workspace flow, folded into #/loop).
      onNewTarget={(ws) => navigate(`loop?kind=code&ws=${encodeURIComponent(ws)}`)}
      onOpenProject={(pid) => navigate(`projects/${pid}`)}
      onStartNew={() => navigate('code')}
      query={query} setQuery={setQuery} />
  }

  // bare #/code (and any non-detail seg) → redirected to #/loop (handled above → null).
  return null
}

// Code-kind field accessors over the unified Loop: the SDLC entry_stage/project_kind
// live in kind_config; the stage plan + per-stage status are the unified plan/
// phase_status. Centralized here so the list rows read cleanly off one Loop shape.
const kindCfg = (p: Loop): Record<string, unknown> => (p.kind_config || {}) as Record<string, unknown>
const entryStage = (p: Loop): string => String(p.kind_config?.entry_stage ?? '')
const projectKind = (p: Loop): string => String(p.kind_config?.project_kind ?? '')
const stagePlan = (p: Loop): LoopPhase[] => (p.plan ?? []) as LoopPhase[]
const stageStatus = (p: Loop): Record<string, string> => (p.phase_status ?? {}) as Record<string, string>

// A COMPLETE project carrying an error_message finished NON-genuinely (budget/
// exhaustion) → the synthetic 'ended_early' (warn tone + label), so it doesn't read as
// an identical green "Complete". Shared helper so the distinction matches everywhere.
const effectiveStatus = (p: Loop): string => effectiveLoopStatus(p.status, p.error_message)

// Pill tone for the list rows — the shared map (18% bg for a touch more presence in
// a scanned list). needs_input/review/ready=info, blocked/stagnant/ended_early=warn,
// running/intake/planning=primary; the rest neutral.
const statusPill = (status: string): React.CSSProperties => loopStatusTone(status, 18)

// Attention-priority order for the list: projects waiting on the USER come
// first, then actively running, then idle/pre-launch, then finished. The API
// returns newest-first; this re-tiers so an actionable project never sits buried
// below older finished ones. Lower rank = higher in the list.
const STATUS_RANK: Record<string, number> = {
  needs_input: 0, blocked: 1, stagnant: 2, failed: 3,    // need the user
  running: 4, planning: 5, intake: 6,        // in flight (planner/classifier actively working)
  paused: 7,                                 // in flight, deactivated
  review: 8, ready: 9,                        // pre-launch (awaiting the user's launch)
  complete: 10, stopped: 11,                  // terminal
}
const _RANK_FALLBACK = 12  // unknown/future status → sorts AFTER everything known
function byAttention(a: Loop, b: Loop): number {
  const ra = STATUS_RANK[a.status] ?? _RANK_FALLBACK, rb = STATUS_RANK[b.status] ?? _RANK_FALLBACK
  if (ra !== rb) return ra - rb
  return (b.created_at ?? 0) - (a.created_at ?? 0)  // newest-first within a tier
}

// A brownfield project that hasn't launched yet and has no workspace bound can't
// start — the cockpit prompts for one, but flag it on the list row too.
function needsWorkspace(p: Loop): boolean {
  return projectKind(p) === 'brownfield' && !p.workspace_dir && (p.status === 'ready' || p.status === 'review')
}

// Status filter (mirrors the Goal Loop list's Active/All/Needs-you/Done): a
// growing project list stays scannable by lifecycle, not just findable by name.
type CodeFilter = 'active' | 'attention' | 'all' | 'done'
const _ACTIVE_ST = ['running', 'planning', 'intake', 'paused']
const _ATTENTION_ST = ['needs_input', 'blocked', 'stagnant', 'failed', 'review', 'ready']
const _DONE_ST = ['complete', 'stopped']
function matchesFilter(p: Loop, f: CodeFilter): boolean {
  return f === 'all' ? true
    : f === 'active' ? _ACTIVE_ST.includes(p.status)
    : f === 'attention' ? _ATTENTION_ST.includes(p.status)
    : _DONE_ST.includes(p.status)  // done
}

/** Minimal project list (full board/search lands with the cockpit cycle). */
function CodeListPage({ onCreate, onOpen }: { onCreate: () => void; onOpen: (id: string) => void }) {
  // Cached list (instant paint on revisit) that still polls — persist:false so the
  // live build status + stage progress never go stale across a hard reload.
  // Don't swallow a failed list load into [] — that rendered the "No code projects
  // yet" empty state on a transient 5xx / gateway blip / network drop, a FALSE-empty
  // that hid the user's real projects (and could prompt them to recreate work they
  // already have). Let useCachedData capture the error; render a distinct, retryable
  // error state below. data stays undefined on a first-load failure (→ the error
  // branch), and a prior successful list stays shown if a later poll fails.
  const { data: projects, error: loadErr, refresh } = useCachedData('code:projects', () => api.uLoops({ kind: 'code' }), { persist: false })
  // After a mutation, revalidate against the changed list.
  const load = () => { invalidateCache('code:projects'); refresh() }
  // The project we're choosing a workspace for (from the 'needs workspace' pill) —
  // lets the user resolve the brownfield blocker without opening the cockpit.
  const [pickFor, setPickFor] = useState<Loop | null>(null)
  // Name/stage/kind search — matches the nav-search every other list page now has,
  // so a growing project list stays findable.
  const [q, setQ] = useState('')
  // Lifecycle filter (Active / Needs you / All / Done) — parity with the Goal Loop
  // list so a long project list is scannable by state, not just searchable by name.
  const [filter, setFilter] = useState<CodeFilter>('all')
  // A transient error from a list-level mutation (delete / workspace-pick) — surfaced
  // in an inline banner instead of the old silent .catch(){} so a failed action
  // (worker gone, bad dir, 5xx) tells the user why nothing changed.
  const [actionErr, setActionErr] = useState<string | null>(null)
  // Live-refresh while away (skips when the tab is hidden) so a running build's
  // status + stage progress update without a manual reload. Gated on a live project:
  // a list of only finished projects never changes, so pass null to disable polling
  // (no point hammering the API every 6s for static data) until a live one appears.
  const codeHasLive = (projects ?? []).some((p) => !['complete', 'stopped', 'failed'].includes(p.status))
  useVisiblePoll(refresh, codeHasLive ? 6000 : null)

  async function del(p: Loop) {
    setActionErr(null)
    // Warn explicitly when the project is mid-flight — deleting it tears down the
    // running worker. The files clause depends on WHERE the code lives: a bound
    // (brownfield) workspace_dir is external + left untouched, but a greenfield
    // project with no bound dir keeps its files in the loop's own managed folder —
    // which delete DESTROYS — so don't falsely promise the files are safe there.
    const body = `${['running', 'planning', 'intake'].includes(p.status)
      ? `"${p.name}" is still working — deleting it stops the worker and removes its plan. `
      : `"${p.name}" and its plan will be removed. `}${p.workspace_dir
      ? 'Your workspace folder and its files are left untouched.'
      : 'This project keeps its files in its own managed folder — deleting it also removes those files. Move anything you want to keep out first.'}`
    if (!(await confirm({ title: `Delete project "${p.name}"?`, body, danger: true, confirmLabel: 'Delete' }))) return
    try { await api.deleteULoop(p.id) }
    catch (e) { setActionErr(`Couldn't delete that project: ${(e as Error).message || 'unknown error'}`) }
    load()
  }

  // done-stages / total, for an at-a-glance progress sense. For a LIVE project, also
  // name the stage in play (first not-done) — "2/4 stages · Implementation" — so the
  // list conveys WHAT's happening now, not just how far along (parity with the Goal
  // list's live cycle position). The active-stage label is humanized like everywhere.
  const progress = (p: Loop) => {
    const plan = stagePlan(p)
    const total = plan.length
    if (!total) return ''
    const ss = stageStatus(p)
    const done = Object.values(ss).filter((s) => s === 'done').length
    const base = `${done}/${total} stages`
    // Name the stage in play (first not-done) for any MID-RUN state — not just running.
    // A blocked / needs_input / stagnant / paused project is stalled AT a stage, and
    // that's exactly what the user needs to see in the list to triage where it's stuck
    // ("2/4 stages · Implementation"). Pre-launch + terminal states show the bare count
    // (no single stage is "in play" there).
    const MID_RUN = ['running', 'paused', 'blocked', 'needs_input', 'stagnant']
    if (!MID_RUN.includes(p.status) || done >= total) return base
    // Key EXACTLY as the backend phase_key (`stage.strip() || title.strip()`): a
    // stageless-but-titled row (stage==='') is keyed by its TITLE. The old `?? `
    // (nullish) kept the empty stage → keyed by '' → the stage_status lookup missed,
    // so a project with a stageless row showed the wrong stage-in-play. (Same fix as
    // SdlcProgressCard; mirrors CodeCockpitPage's stageKey.)
    const active = plan.find((s) => (ss[(String(s.stage ?? '').trim() || String(s.title ?? '').trim())] ?? 'pending') !== 'done')
    const name = active ? String(active.title || active.stage || '') : ''
    return name ? `${base} · ${sdlcStageLabel(name)}` : base
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={<div className="flex items-center gap-2"><Code2 size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">Code projects</span></div>}
        right={<HeaderActions><HeaderControl icon={Plus} label="New project" onClick={onCreate} variant="primary" priority="primary" /></HeaderActions>} />
      {/* Search + a lifecycle filter live on the page (pinned below the header). */}
      {!!projects?.length && (
        <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search projects', label: 'Search projects' }}>
          <FilterMenu sections={[{
            title: 'Show', value: filter, defaultKey: 'all',
            onChange: (k) => setFilter(k as CodeFilter),
            options: [
              { key: 'all', label: 'All', count: projects!.length },
              { key: 'active', label: 'Active', count: projects!.filter((p) => matchesFilter(p, 'active')).length },
              { key: 'attention', label: 'Needs you', count: projects!.filter((p) => matchesFilter(p, 'attention')).length },
              { key: 'done', label: 'Done', count: projects!.filter((p) => matchesFilter(p, 'done')).length },
            ],
          } satisfies FilterSectionDef]} />
        </ListControls>
      )}
      {/* Inline error for a failed list-level action (delete / workspace-pick) —
          dismissible; replaces the old silent .catch(){}. */}
      {actionErr && (
        <div role="alert" className="mx-l mt-2 flex items-center gap-2 rounded-lg px-3 py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 10%, transparent)', color: 'var(--color-danger)' }}>
          <AlertTriangle size={14} className="shrink-0" />
          <span className="min-w-0 flex-1">{actionErr}</span>
          <button type="button" onClick={() => setActionErr(null)} aria-label="Dismiss" className="shrink-0 hover:opacity-70"><X size={14} /></button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto px-l py-l">
        <div className="mx-auto w-full" style={{ maxWidth: 'var(--content-width)' }}>
          {projects === undefined && loadErr ? (
            // First-load failure — distinct from a genuine empty list. Offer a retry
            // instead of an eternal skeleton or a misleading "no projects yet".
            <div className="flex flex-col items-center gap-l py-2xl text-center">
              <AlertTriangle size={32} className="text-danger opacity-70" />
              <div>
                <h2 data-type="headline-s" className="text-on-surface">Couldn't load your projects</h2>
                <p className="mt-1 text-on-surface-low text-[0.9375rem] max-w-[400px]">{(loadErr as Error)?.message || 'The server didn’t respond. Your projects are safe — this is just a load error.'}</p>
              </div>
              <Button size="sm" onClick={load}><RotateCcw size={15} /> Retry</Button>
            </div>
          ) : projects === undefined ? (
            <ListSkeleton rows={6} />
          ) : projects.length === 0 ? (
            <div className="flex flex-col items-center gap-l py-2xl text-center">
              <Code2 size={36} className="text-on-surface-low opacity-40" />
              <div>
                <h2 data-type="headline-s" className="text-on-surface">No code projects yet</h2>
                <p className="mt-1 text-on-surface-low text-[0.9375rem] max-w-[400px]">Describe an SDLC task — an idea, a spec, a task list, or a bugfix — and an agent will detect its stage, plan the work, and execute it in a workspace.</p>
              </div>
              <Button size="sm" onClick={onCreate}><Plus size={15} /> Start a project</Button>
            </div>
          ) : (() => {
            const needle = q.trim().toLowerCase()
            const shown = projects
              .filter((p) => matchesFilter(p, filter))
              // Include the human status label in the haystack so a user can find a
              // project by state ("failed", "running", "needs you", "ended early") —
              // not just by name/stage/kind. effectiveStatus surfaces the synthetic
              // 'ended_early' so that's searchable too.
              .filter((p) => {
                if (!needle) return true
                const es = effectiveStatus(p)
                return `${p.name} ${entryStage(p)} ${sdlcStageLabel(entryStage(p))} ${projectKind(p)} ${es} ${loopStatusLabel(es)}`.toLowerCase().includes(needle)
              })
              .slice().sort(byAttention)
            if (shown.length === 0) return (
              <div className="py-16 text-center text-on-surface-low text-[0.875rem]">
                {needle ? `No projects match “${q.trim()}”.` : 'No projects in this view.'}
              </div>
            )
            return (
            <div className="flex flex-col gap-2">
              {shown.map((p) => (
                // role=button (not <button>) so the nested action buttons (needs-
                // workspace, delete) are valid HTML and their clicks don't bubble to
                // the row's open-navigation.
                <div key={p.id} role="button" tabIndex={0} onClick={() => onOpen(p.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(p.id) } }}
                  className="group flex cursor-pointer items-center gap-3 rounded-xl border border-outline-variant/50 bg-surface-container/60 px-4 py-3 text-left transition-colors hover:bg-surface-high">
                  <Code2 size={16} className="shrink-0 text-on-surface-low" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-on-surface text-[0.9375rem]">{p.name}</div>
                    <div className="truncate text-on-surface-low text-[0.75rem]">{sdlcStageLabel(entryStage(p))} · {projectKind(p)}{progress(p) ? ` · ${progress(p)}` : ''}</div>
                  </div>
                  {/* A brownfield project that's pre-launch with no workspace can't start
                      until one is chosen — flag it on the row AND let the user fix it
                      in place (opens the workspace picker) without entering the cockpit. */}
                  {needsWorkspace(p) && (
                    <button type="button" onClick={(e) => { e.stopPropagation(); setPickFor(p) }}
                      className="shrink-0 inline-flex items-center gap-1 rounded-pill px-2 py-0.5 text-[0.7rem] transition-colors hover:brightness-110"
                      style={{ background: 'color-mix(in srgb, var(--color-warn) 16%, transparent)', color: 'var(--color-warn)' }}
                      title="Choose a workspace folder before this project can start">
                      <FolderOpen size={11} /> needs workspace
                    </button>
                  )}
                  {(p.status === 'running' || p.status === 'planning' || p.status === 'intake') && <Loader2 size={12} className="shrink-0 animate-spin text-primary" />}
                  {(() => { const es = effectiveStatus(p); return (
                    // Show the reason on hover for ANY status that carries one — blocked
                    // (the stall-pause explanation), failed, and the synthetic ended_early
                    // — so the user can triage which project needs them without opening it.
                    <span className="shrink-0 rounded-pill px-2 py-0.5 text-[0.7rem]" style={statusPill(es)}
                      title={p.error_message || undefined}>{loopStatusLabel(es)}</span>
                  ) })()}
                  {/* focus-visible:opacity-100 so a keyboard user who tabs to Delete can
                      actually see it — a hover-only reveal hid this destructive action
                      from keyboard + touch entirely (matches LoopPlanReview's pattern). */}
                  <button type="button" onClick={(e) => { e.stopPropagation(); del(p) }} aria-label="Delete project"
                    className="shrink-0 rounded-md p-1.5 text-on-surface-low opacity-0 transition-opacity hover:bg-surface-highest hover:text-danger group-hover:opacity-100 focus-visible:opacity-100">
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
            )
          })()}
        </div>
      </div>
      {pickFor && (
        <WorkspacePicker mode="brownfield" onClose={() => setPickFor(null)}
          onPick={async (dir) => {
            const id = pickFor.id
            setPickFor(null); setActionErr(null)
            // Surface a failed bind (dir vanished, permission, 5xx) — a silent catch
            // left the 'needs workspace' pill stuck with no explanation.
            try { await api.updateULoop(id, { workspace_dir: dir }) }
            catch (e) { setActionErr(`Couldn't set the workspace: ${(e as Error).message || 'unknown error'}`) }
            load()
          }} />
      )}
    </div>
  )
}
