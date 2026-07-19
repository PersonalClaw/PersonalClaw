import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Code2, Plus, Trash2, X, Rocket, Hand, ChevronLeft, Loader2, ChevronUp, ChevronDown, FileText, Sparkle, Workflow, Check, FolderOpen, Gauge } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Button } from '../../ui/Button'
import { Markdown } from '../../ui/Markdown'
import { spring } from '../../design/motion'
import { api, SDLC_STAGES, sdlcStageLabel, type Loop, type CodeStage, type PlanStep, type SkillItem, type WorkflowItem, type SkillSearchResult } from '../../lib/api'
import type { CodeDraft } from './codeDraft'
import { WorkspacePicker } from './WorkspacePicker'

// Code-kind field accessors over the unified Loop (entry_stage/project_kind/
// verify_command/test_command live in kind_config; the stage list is `plan`).
const kc = (p: Loop) => (p.kind_config || {}) as Record<string, unknown>

/** Plan Review — the single confirm-and-edit screen between create and launch.
 *  Shows the full SDLC stage plan the classifier proposed; the user can edit each
 *  stage's objective + exit criteria, reorder/remove stages, add a stage, then
 *  launch. On launch it persists the edited plan (updateULoop) and starts the
 *  loop (uLoopAction 'start'), which provisions the Tasks Project + per-stage
 *  TaskLists and arms the autonomous worker. */
export function CodePlanReview({ draft, onBack, onLaunched }: {
  draft: CodeDraft
  onBack: () => void
  onLaunched: (id: string) => void
}) {
  const [project, setProject] = useState<Loop | null>(null)
  const [stages, setStages] = useState<CodeStage[]>([])
  const [launching, setLaunching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A brownfield project needs a bound workspace before it can start — Launch calls
  // uLoopAction('start') directly, which the backend rejects without one. This surface
  // is reachable for such a project (resume of a `review` project, or a walkthrough that
  // never bound a folder), so offer the picker inline instead of a raw start failure.
  const [pickWs, setPickWs] = useState(false)
  // How execution is driven once launched (the vision's explicit choice): autopilot
  // = the system queues + drives the phased tasks; one-by-one = the user queues each.
  const [autopilot, setAutopilot] = useState(true)
  // The non-decomposition artifacts the user shaped in the walkthrough (problem
  // framing, requirements, design, …) — surfaced read-only here so the context the
  // user just approved carries into Plan Review instead of being dropped. The
  // decomposition artifact IS the stage plan below, so it's excluded.
  const [artifacts, setArtifacts] = useState<PlanStep[]>([])
  // Installed skills/workflows, to resolve the loop's baseline skill_ids/workflow_ids
  // (threaded from the planner's suggestions at create) to human names for a read-only
  // "Capabilities" summary — so the user SEES what the worker will load actively every
  // cycle before launching, instead of those picks being silently applied.
  const [installedSkills, setInstalledSkills] = useState<SkillItem[]>([])
  const [installedWorkflows, setInstalledWorkflows] = useState<WorkflowItem[]>([])
  // The capabilities the loop will load actively every cycle — seeded from the loop's
  // baseline (the planner's threaded suggestions) and editable here before launch, so
  // the user can drop a mis-suggested skill or add one the planner missed. Persisted on
  // launch. Sets for cheap toggle.
  const [skillIds, setSkillIds] = useState<Set<string>>(new Set())
  const [workflowIds, setWorkflowIds] = useState<Set<string>>(new Set())
  // Marketplace skills the planner suggested INSTALLING (not yet on disk). In-flight +
  // already-installed-this-session tracking, mirroring the Goal Plan Review.
  const [installing, setInstalling] = useState<Record<string, boolean>>({})
  const [installed, setInstalled] = useState<Set<string>>(new Set())
  const marketplaceSuggestions = draft.classification.marketplace_suggestions ?? []

  // Install a planner-suggested marketplace skill, then auto-select it into the loop's
  // capabilities (match the freshly-installed dir's basename — the local key often
  // differs from the marketplace id/name). Mirrors LoopPlanReview.installMarketplaceSkill.
  async function installMarketplaceSkill(s: SkillSearchResult) {
    setInstalling((m) => ({ ...m, [s.id]: true }))
    try {
      const res = await api.installSkill(s.id, s.source || 'skills.sh')
      setInstalled((prev) => new Set(prev).add(s.id))
      const installedKey = res?.path ? res.path.replace(/\/+$/, '').split('/').pop() : undefined
      const fresh = await api.skills().catch(() => installedSkills)
      setInstalledSkills(fresh)
      const match = fresh.find((x) => x.key === installedKey)
        || fresh.find((x) => x.key === s.id || x.name === s.name)
      if (match) setSkillIds((prev) => new Set(prev).add(match.key))
    } catch (e) {
      setError(`Couldn't install “${s.name}”: ${(e as Error).message || 'try again'}`)
    } finally { setInstalling((m) => ({ ...m, [s.id]: false })) }
  }

  useEffect(() => {
    api.uLoop(draft.projectId).then((p) => {
      setProject(p)
      setStages(((p.plan ?? []) as unknown as CodeStage[]).map((s) => ({ ...s, exit_criteria: [...(s.exit_criteria ?? [])] })))
      setAutopilot(p.autopilot !== false)
      setSkillIds(new Set(p.skill_ids ?? []))
      setWorkflowIds(new Set(p.workflow_ids ?? []))
    }).catch(() => setError('Could not load the project.'))
    api.skills().then(setInstalledSkills).catch(() => {})
    api.workflows().then((w) => setInstalledWorkflows(w.filter((x) => x.enabled !== false))).catch(() => {})
    api.uLoopPlanSession(draft.projectId).then((s) => {
      if (s) setArtifacts(s.steps.filter((st) => st.kind !== 'decomposition' && st.artifact && Object.keys(st.artifact).length > 0))
    }).catch(() => {})
  }, [draft.projectId])

  // Effective stage keys that appear more than once — the backend dedupes these on
  // launch (task_list_ids/stage_status key on `stage || title`), so a repeat is
  // silently dropped. Surface it as a warning. Covers BOTH a duplicate canonical
  // stage id AND two stageless rows sharing a title (the store's dedupe keys
  // stageless rows by title too), matching the launch-time keying exactly.
  const dupStages = (() => {
    const seen = new Set<string>(); const dups = new Set<string>()
    for (const s of stages) {
      const key = ((s.stage || '').trim() || (s.title || '').trim()).toLowerCase()
      if (!key) continue
      if (seen.has(key)) dups.add((s.stage || '').trim() || (s.title || '').trim()); else seen.add(key)
    }
    return [...dups]
  })()

  function patchStage(i: number, patch: Partial<CodeStage>) {
    setStages((prev) => prev.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  }
  function removeStage(i: number) { setStages((prev) => prev.filter((_, j) => j !== i)) }
  function addStage() {
    setStages((prev) => {
      // The new stage appends at the end, so default its type to the first unused
      // SDLC type AT OR AFTER the latest stage already in the plan — a new stage
      // should continue the ladder forward (a plan ending at 'implementation' gets
      // 'verification', not 'ideation' placed backwards). Fall back to any unused
      // type, then 'implementation', so it always starts VALID (a duplicate id is
      // dropped on launch + warns).
      const used = new Set(prev.map((s) => s.stage))
      const lastIdx = prev.reduce((mx, s) => Math.max(mx, SDLC_STAGES.indexOf(s.stage as typeof SDLC_STAGES[number])), -1)
      const forward = SDLC_STAGES.find((sg, i) => i >= lastIdx && !used.has(sg))
      const stage = forward ?? SDLC_STAGES.find((sg) => !used.has(sg)) ?? 'implementation'
      return [...prev, {
        stage, title: 'New stage', objective: '', exit_criteria: [],
        deliverable: '', task_list_name: 'New stage',
      }]
    })
  }
  function move(i: number, dir: -1 | 1) {
    setStages((prev) => {
      const j = i + dir
      if (j < 0 || j >= prev.length) return prev
      const next = [...prev]; const [it] = next.splice(i, 1); next.splice(j, 0, it); return next
    })
  }

  // Brownfield + no bound workspace → can't start. Gate Launch on it (the backend
  // would 422 otherwise) and route the click to the picker instead.
  const needsWorkspace = !!project && String(kc(project).project_kind ?? '') === 'brownfield' && !project.workspace_dir

  async function launch() {
    if (launching) return
    // A brownfield project can't start without a workspace — open the picker rather
    // than firing a start that the backend rejects with a bare error + no recourse.
    if (needsWorkspace) { setPickWs(true); return }
    setLaunching(true); setError(null)
    try {
      // Persist the edited stage plan (+ task_list_name defaults), then start.
      const cleaned = stages
        .filter((s) => (s.objective || '').trim() || (s.title || '').trim())
        .map((s) => ({
          ...s,
          task_list_name: (s.task_list_name || s.title || s.stage || '').trim(),
          // drop blank task rows the user left empty so they aren't seeded
          tasks: (s.tasks ?? []).filter((t) => (t.title || '').trim()),
        }))
      await api.updateULoop(draft.projectId, {
        plan: cleaned, autopilot,
        skill_ids: [...skillIds], workflow_ids: [...workflowIds],
      })
      await api.uLoopAction(draft.projectId, 'start')
      onLaunched(draft.projectId)
    } catch (e) {
      setError((e as Error).message || 'Could not launch the project'); setLaunching(false)
    }
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar
        left={<div className="flex items-center gap-2"><Code2 size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">Review the plan</span></div>}
        right={<HeaderActions><HeaderControl icon={ChevronLeft} label="Back" onClick={onBack} priority="primary" /></HeaderActions>} />

      <div className="min-h-0 flex-1 overflow-y-auto px-l py-l">
        <div className="mx-auto flex w-full flex-col gap-4" style={{ maxWidth: 'var(--content-width)' }}>
          {!project ? (
            <div className="flex h-40 items-center justify-center"><Loader2 size={22} className="animate-spin text-on-surface-low" /></div>
          ) : (
            <>
              {/* summary header */}
              <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialFast}
                className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-4">
                <p className="text-on-surface text-[0.9375rem]">{project.task}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[0.75rem]">
                  <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">entry: <b className="text-on-surface">{sdlcStageLabel(String(kc(project).entry_stage ?? ''))}</b></span>
                  <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var capitalize">{String(kc(project).project_kind ?? '')}</span>
                  {project.workspace_dir && <span className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-var" title={project.workspace_dir}>{project.workspace_dir.split('/').slice(-2).join('/')}</span>}
                  {/* build + test commands the supervisor will independently gate on
                      (C50/C51) — show them at review so the launch decision is informed. */}
                  {!!kc(project).verify_command && <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">build: <code className="text-on-surface">{String(kc(project).verify_command)}</code></span>}
                  {!!kc(project).test_command && <span className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-var">tests: <code className="text-on-surface">{String(kc(project).test_command)}</code></span>}
                </div>
                {project.success_criteria && (
                  <p className="mt-2 text-on-surface-low text-[0.8125rem]"><span className="text-on-surface-var">Done when:</span> {project.success_criteria}</p>
                )}
              </motion.div>

              {/* The planning artifacts the user shaped + approved in the walkthrough
                  (problem framing, requirements, design, …) — read-only, collapsible,
                  so that context carries into the launch decision. */}
              {artifacts.length > 0 && <PlanArtifacts steps={artifacts} />}

              {/* Capabilities the worker will load actively every cycle (the planner's
                  suggested skills/workflows, threaded onto the loop at create). Editable
                  here — toggle off a mis-suggested skill or add one the planner missed;
                  persisted on launch. */}
              <PlanCapabilities skills={installedSkills} workflows={installedWorkflows}
                skillIds={skillIds} workflowIds={workflowIds}
                onToggleSkill={(k) => setSkillIds((prev) => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })}
                onToggleWorkflow={(id) => setWorkflowIds((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })}
                suggested={new Set(project.skill_ids ?? [])} suggestedWf={new Set(project.workflow_ids ?? [])}
                marketplace={marketplaceSuggestions} installing={installing} installed={installed}
                onInstall={installMarketplaceSkill} />

              {/* the stage plan */}
              <div className="flex items-center justify-between">
                <span className="text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>Stages ahead ({stages.length})</span>
                <button type="button" onClick={addStage} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[0.75rem] text-on-surface-low hover:text-on-surface hover:bg-surface-high"><Plus size={13} /> Add stage</button>
              </div>

              {/* Stages that collide on launch (one TaskList + one status entry per
                  effective key = stage type, or title for an untyped stage) — the
                  backend drops the duplicate, so warn the user to disambiguate it
                  rather than silently lose a stage. */}
              {dupStages.length > 0 && (
                <div role="alert" className="rounded-lg px-3 py-2 text-[0.8125rem]"
                  style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
                  Stages collide on <b>{dupStages.join(', ')}</b> — only the first is kept on launch. Give each a distinct type (or title, if untyped).
                </div>
              )}

              <div className="flex flex-col gap-3">
                {stages.map((s, i) => (
                  <StageCard key={i} index={i} count={stages.length} stage={s}
                    onPatch={(p) => patchStage(i, p)} onRemove={() => removeStage(i)} onMove={(d) => move(i, d)} />
                ))}
                {stages.length === 0 && (
                  <p className="rounded-lg border border-dashed border-outline-variant/40 py-8 text-center text-on-surface-low text-[0.8125rem]">
                    No stages — add one, or launch to let the worker plan as it goes.
                  </p>
                )}
              </div>

              {error && (
                <div role="alert" className="rounded-lg px-4 py-3 text-[0.8125rem]"
                  style={{ background: 'color-mix(in srgb, var(--color-danger) 8%, transparent)', color: 'var(--color-danger)' }}>{error}</div>
              )}

              {/* Drive mode — choose before launch how the phased tasks execute. */}
              <div className="flex flex-col gap-1.5 rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3">
                <span className="text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>How should it run?</span>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setAutopilot(true)} aria-pressed={autopilot}
                    className={`flex flex-1 items-start gap-2 rounded-lg border p-2.5 text-left transition-colors ${autopilot ? 'border-primary/60 bg-primary/10' : 'border-outline-variant/50 hover:bg-surface-high'}`}>
                    <Rocket size={15} className={`mt-0.5 shrink-0 ${autopilot ? 'text-primary' : 'text-on-surface-low'}`} />
                    <span>
                      <span className="block text-on-surface text-[0.8125rem]">Autopilot</span>
                      <span className="block text-on-surface-low text-[0.72rem]">The system queues + drives every phase to completion.</span>
                    </span>
                  </button>
                  <button type="button" onClick={() => setAutopilot(false)} aria-pressed={!autopilot}
                    className={`flex flex-1 items-start gap-2 rounded-lg border p-2.5 text-left transition-colors ${!autopilot ? 'border-primary/60 bg-primary/10' : 'border-outline-variant/50 hover:bg-surface-high'}`}>
                    <Hand size={15} className={`mt-0.5 shrink-0 ${!autopilot ? 'text-primary' : 'text-on-surface-low'}`} />
                    <span>
                      <span className="block text-on-surface text-[0.8125rem]">One-by-one</span>
                      <span className="block text-on-surface-low text-[0.72rem]">You queue tasks yourself, at your own pace.</span>
                    </span>
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-end gap-2 pb-4">
                <Button variant="ghost" size="sm" onClick={onBack}>Cancel</Button>
                {/* Block launch while stages collide — launching would silently drop
                    the duplicate (the warning banner above says so), losing a stage the
                    user shaped. Make the warning actionable: resolve it first. The span
                    carries a tooltip explaining the disable (a disabled Button has
                    pointer-events:none, so the title must live on a hoverable wrapper) —
                    else, if the warning banner scrolled off, the dead button is a mystery. */}
                {/* A brownfield project with no workspace can't launch — but rather than
                    disable the button (a dead end here, no picker visible), keep it
                    enabled and relabel it to OPEN the picker, so the missing binding is
                    fixable in one click. dup-collision still hard-disables. */}
                <span title={dupStages.length > 0 ? `Resolve the colliding stage${dupStages.length === 1 ? '' : 's'} (${dupStages.join(', ')}) before launching — each needs a distinct type or title.` : needsWorkspace ? 'This brownfield project needs a workspace folder — choosing one starts it.' : undefined}>
                  <Button size="md" onClick={launch} disabled={launching || dupStages.length > 0}>
                    {launching ? <Loader2 size={15} className="animate-spin" /> : needsWorkspace ? <FolderOpen size={15} /> : <Rocket size={15} />} {launching ? 'Launching…' : needsWorkspace ? 'Choose workspace & launch' : 'Launch'}
                  </Button>
                </span>
              </div>
            </>
          )}
        </div>
      </div>
      {pickWs && (
        <WorkspacePicker mode="brownfield" onClose={() => setPickWs(false)}
          onPick={async (dir) => {
            setPickWs(false); setError(null); setLaunching(true)
            // Bind the chosen folder, then start — surface a failure (bad dir, 422,
            // agent gone) the same way launch() does, and reflect the binding so the
            // header chip + needsWorkspace gate update if the start half fails.
            try {
              setProject(await api.updateULoop(draft.projectId, { workspace_dir: dir }))
              await api.uLoopAction(draft.projectId, 'start')
              onLaunched(draft.projectId)
            } catch (e) {
              setError((e as Error).message || 'Could not launch with that folder'); setLaunching(false)
            }
          }} />
      )}
    </div>
  )
}

/** Editable capabilities the loop loads actively every cycle — the planner's suggested
 *  skills/workflows (threaded onto loop.skill_ids/workflow_ids at create) PLUS every
 *  other installed one, as toggle chips. Suggested-first ordering; a ✓ marks selected.
 *  The user drops a mis-suggested skill or adds one the planner missed; persisted on
 *  launch. PLUS planner-suggested marketplace skills not yet on disk — installed in
 *  place (then auto-selected). Renders nothing when there's nothing installed AND
 *  nothing to install (the agent still trigger-matches skills as it goes). */
function PlanCapabilities({ skills, workflows, skillIds, workflowIds, onToggleSkill, onToggleWorkflow, suggested, suggestedWf, marketplace, installing, installed, onInstall }: {
  skills: SkillItem[]; workflows: WorkflowItem[]
  skillIds: Set<string>; workflowIds: Set<string>
  onToggleSkill: (key: string) => void; onToggleWorkflow: (id: string) => void
  suggested: Set<string>; suggestedWf: Set<string>
  marketplace: SkillSearchResult[]; installing: Record<string, boolean>; installed: Set<string>
  onInstall: (s: SkillSearchResult) => void
}) {
  // Hide marketplace suggestions already on disk (installed this session OR present in
  // the installed catalog under a key/name that often differs from the marketplace id).
  const norm = (x: string) => x.toLowerCase().replace(/[^a-z0-9]+/g, '')
  const installedKeys = new Set<string>()
  for (const sk of skills) { installedKeys.add(norm(sk.key)); if (sk.name) installedKeys.add(norm(sk.name)) }
  const marketplaceToShow = marketplace.filter((m) => !(installedKeys.has(norm(m.id)) || installedKeys.has(norm(m.name)) || installed.has(m.id)))
  if (!skills.length && !workflows.length && !marketplaceToShow.length) return null
  // Suggested (planner-picked) first, then the rest — so the recommendations are top.
  const orderedSkills = [...skills].sort((a, b) => Number(suggested.has(b.key)) - Number(suggested.has(a.key)))
  const orderedWorkflows = [...workflows].sort((a, b) => Number(suggestedWf.has(b.id)) - Number(suggestedWf.has(a.id)))
  const selectedCount = skillIds.size + workflowIds.size
  return (
    <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
      <div className="mb-1 inline-flex items-center gap-1.5 text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>
        <Sparkle size={14} className="text-primary" /> Capabilities loaded every cycle
      </div>
      <p className="mb-2 text-on-surface-low text-[0.75rem]">
        The worker loads these actively each cycle. The planner pre-selected what looks relevant — adjust freely.{' '}
        {selectedCount > 0 ? `${selectedCount} selected.` : 'None selected — the agent still trigger-matches skills as it goes.'}
      </p>
      {(!!orderedSkills.length || !!orderedWorkflows.length) && (
        <div className="flex flex-wrap gap-1.5">
          {orderedSkills.map((s) => (
            <CapChip key={`s-${s.key}`} label={s.name || s.key} icon={<Sparkle size={11} />}
              on={skillIds.has(s.key)} suggested={suggested.has(s.key)} onToggle={() => onToggleSkill(s.key)} title={s.description} />
          ))}
          {orderedWorkflows.map((w) => (
            <CapChip key={`w-${w.id}`} label={w.name || w.id} icon={<Workflow size={11} />}
              on={workflowIds.has(w.id)} suggested={suggestedWf.has(w.id)} onToggle={() => onToggleWorkflow(w.id)} title={w.description} />
          ))}
        </div>
      )}
      {/* Planner-suggested marketplace skills not yet installed — install in place. */}
      {marketplaceToShow.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1.5 border-t border-outline-variant/40 pt-2.5">
          <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Suggested to install</span>
          {marketplaceToShow.map((m) => (
            <div key={m.id} className="flex items-center gap-2 rounded-md bg-surface-high/50 px-2.5 py-1.5 text-[0.8125rem]">
              <Sparkle size={12} className="shrink-0 text-primary" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-on-surface-var">{m.name}</div>
                {m.description && <div className="truncate text-on-surface-low text-[0.72rem]">{m.description}</div>}
              </div>
              <button type="button" disabled={!!installing[m.id]} onClick={() => onInstall(m)}
                className="shrink-0 inline-flex items-center gap-1 rounded-md bg-primary/15 px-2 py-0.5 text-[0.72rem] text-primary hover:bg-primary/25 disabled:opacity-50">
                {installing[m.id] ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />} {installing[m.id] ? 'Installing…' : 'Install'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** One toggleable capability chip — selected (primary fill + ✓) vs not; a small dot
 *  marks a planner suggestion so the user sees what was recommended even after toggling. */
function CapChip({ label, icon, on, suggested, onToggle, title }: {
  label: string; icon: React.ReactNode; on: boolean; suggested: boolean; onToggle: () => void; title?: string
}) {
  return (
    <button type="button" onClick={onToggle} aria-pressed={on} title={title || label}
      className={`inline-flex items-center gap-1 rounded-pill px-2 py-0.5 text-[0.75rem] transition-colors ${on
        ? 'bg-primary/15 text-primary hover:bg-primary/25'
        : 'bg-surface-high text-on-surface-low hover:bg-surface-highest hover:text-on-surface-var'}`}>
      {on ? <Check size={11} /> : icon} <span className="truncate max-w-[180px]">{label}</span>
      {suggested && !on && <span className="ml-0.5 size-1 rounded-full bg-primary/60" title="Planner-suggested" />}
    </button>
  )
}

/** The approved walkthrough artifacts (problem framing / requirements / design /
 *  …) shown read-only + collapsible above the stage plan, so the planning context
 *  the user shaped is visible at the launch decision instead of dropped. */
function PlanArtifacts({ steps }: { steps: PlanStep[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        aria-label={open ? 'Hide plan artifacts' : 'Show plan artifacts'}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>
        <FileText size={14} className="text-primary" />
        Plan artifacts ({steps.length})
        <span className="text-on-surface-low text-[0.7rem]">{steps.map((s) => s.kind.replace(/_/g, ' ')).join(' · ')}</span>
        {open ? <ChevronUp size={15} className="ml-auto text-on-surface-low" /> : <ChevronDown size={15} className="ml-auto text-on-surface-low" />}
      </button>
      {open && (
        <div className="flex flex-col gap-3 border-t border-outline-variant/40 px-4 py-3">
          {steps.map((s) => {
            const md = typeof s.artifact?.markdown === 'string' ? s.artifact.markdown.trim() : ''
            // Fall back to the structured fields when the planner emitted no markdown
            // body — so an artifact with real content never reads "No detail." (mirrors
            // the cockpit's render-by-shape rather than markdown-only assumption).
            const structured = (() => {
              if (md || !s.artifact) return ''
              const rest = Object.fromEntries(Object.entries(s.artifact).filter(([k]) => k !== 'markdown'))
              return Object.keys(rest).length ? JSON.stringify(rest, null, 2) : ''
            })()
            return (
              <div key={s.id} className="flex flex-col gap-1">
                <div className="flex items-center gap-1.5 text-on-surface text-[0.8125rem]">
                  {s.title}
                  <span className="rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.65rem]">{s.kind.replace(/_/g, ' ')}</span>
                </div>
                {md
                  ? <div className="text-on-surface-var text-[0.8125rem]"><Markdown>{md}</Markdown></div>
                  : structured
                    ? <pre className="overflow-x-auto rounded-md bg-surface-high/60 p-2 text-on-surface-var text-[0.7rem] leading-snug whitespace-pre-wrap break-words">{structured}</pre>
                    : <span className="text-on-surface-low text-[0.75rem]">No detail.</span>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// P6 per-stage quality gate editor. metric_pass (0-5) is the bar the supervisor's
// third-party judge must clear for the stage to advance; metric_hold is the marginal
// floor below which a structurally-passing cycle still HOLDs to refine. Opt-in: when
// off, the stage advances on its structural exit criteria alone (metric_pass undefined).
// Turning it on seeds the planner defaults (3.5 / 2.0); a number outside [0,5] or a
// hold above pass is clamped so the saved plan is always valid for the tick engine.
function StageQualityGate({ stage, onPatch }: { stage: CodeStage; onPatch: (p: Partial<CodeStage>) => void }) {
  const on = typeof stage.metric_pass === 'number'
  const pass = stage.metric_pass ?? 3.5
  const hold = stage.metric_hold ?? 2.0
  function toggle() {
    if (on) onPatch({ metric_pass: undefined, metric_hold: undefined })
    else onPatch({ metric_pass: 3.5, metric_hold: 2.0 })
  }
  function setPass(v: number) {
    const p = Math.max(0, Math.min(5, v))
    onPatch({ metric_pass: p, metric_hold: Math.min(hold, p) })  // hold can't exceed pass
  }
  function setHold(v: number) {
    onPatch({ metric_hold: Math.max(0, Math.min(pass, v)) })     // hold ≤ pass, ≥ 0
  }
  return (
    <div className="mt-2 flex flex-col gap-1">
      <button type="button" onClick={toggle} aria-pressed={on}
        className="flex w-fit items-center gap-1.5 text-on-surface-low text-[0.7rem] uppercase tracking-wide hover:text-on-surface-var">
        <Gauge size={12} className={on ? 'text-primary' : ''} />
        <span>Quality bar</span>
        <span className={`rounded-pill px-1.5 py-0.5 text-[0.6rem] normal-case tracking-normal ${on ? 'bg-primary/15 text-primary' : 'bg-surface-high text-on-surface-low'}`}>
          {on ? 'on' : 'off'}
        </span>
      </button>
      {on && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md bg-surface-high/60 px-2.5 py-2 text-[0.75rem] text-on-surface-var">
          <label className="flex items-center gap-1.5">
            <span className="text-on-surface-low">Pass ≥</span>
            <input type="number" min={0} max={5} step={0.5} value={pass}
              onChange={(e) => setPass(parseFloat(e.target.value))} aria-label="Quality pass score"
              className="w-14 rounded bg-surface-high px-1.5 py-0.5 text-on-surface tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-on-surface-low">Hold ≥</span>
            <input type="number" min={0} max={pass} step={0.5} value={hold}
              onChange={(e) => setHold(parseFloat(e.target.value))} aria-label="Quality hold floor"
              className="w-14 rounded bg-surface-high px-1.5 py-0.5 text-on-surface tabular-nums outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          </label>
          <span className="text-on-surface-low text-[0.7rem]">score 0–5 · below hold rolls back</span>
        </div>
      )}
    </div>
  )
}

function StageCard({ index, count, stage, onPatch, onRemove, onMove }: {
  index: number; count: number; stage: CodeStage
  onPatch: (p: Partial<CodeStage>) => void; onRemove: () => void; onMove: (dir: -1 | 1) => void
}) {
  const [crit, setCrit] = useState('')
  const [taskTitle, setTaskTitle] = useState('')
  function addCrit() {
    const c = crit.trim()
    if (!c) return
    onPatch({ exit_criteria: [...(stage.exit_criteria ?? []), c] })
    setCrit('')
  }
  function removeCrit(ci: number) {
    onPatch({ exit_criteria: (stage.exit_criteria ?? []).filter((_, j) => j !== ci) })
  }
  // The per-stage checklist seeded into this stage's TaskList at launch — editable
  // so the user finalizes the ACTUAL set of tasks the worker executes (the
  // directive's "come up with a final set of tasks" the user reviews), not just a
  // read-only preview of the planner's guess.
  function patchTask(ti: number, patch: Partial<{ title: string; description?: string }>) {
    onPatch({ tasks: (stage.tasks ?? []).map((t, j) => (j === ti ? { ...t, ...patch } : t)) })
  }
  function removeTask(ti: number) {
    onPatch({ tasks: (stage.tasks ?? []).filter((_, j) => j !== ti) })
  }
  function addTask() {
    const t = taskTitle.trim()
    if (!t) return
    onPatch({ tasks: [...(stage.tasks ?? []), { title: t, description: '' }] })
    setTaskTitle('')
  }
  return (
    <div className="rounded-xl border border-outline-variant/50 bg-surface-container/60 p-3.5">
      <div className="flex items-start gap-2">
        <div className="mt-1 flex flex-col items-center gap-0.5 text-on-surface-low">
          <button type="button" onClick={() => onMove(-1)} disabled={index === 0} aria-label="Move stage up" className="rounded hover:bg-surface-high hover:text-on-surface disabled:opacity-30"><ChevronUp size={14} /></button>
          <span className="text-[0.7rem] tabular-nums">{index + 1}</span>
          <button type="button" onClick={() => onMove(1)} disabled={index === count - 1} aria-label="Move stage down" className="rounded hover:bg-surface-high hover:text-on-surface disabled:opacity-30"><ChevronDown size={14} /></button>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <input value={stage.title} onChange={(e) => onPatch({ title: e.target.value })} placeholder="Stage title"
              className="min-w-0 flex-1 rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface text-[0.875rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
            {/* Stage type — the canonical SDLC id (drives capabilities + gate
                tracking). Editable so a user can re-type a stage or fix an added
                one; the SDLC ladder is the only valid set. */}
            <select value={SDLC_STAGES.includes(stage.stage as typeof SDLC_STAGES[number]) ? stage.stage : ''}
              onChange={(e) => onPatch({ stage: e.target.value })} aria-label="Stage type"
              className="shrink-0 rounded-md bg-surface-high px-2 py-1.5 text-[0.7rem] text-on-surface-var outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50">
              {!SDLC_STAGES.includes(stage.stage as typeof SDLC_STAGES[number]) && <option value="">— type —</option>}
              {SDLC_STAGES.map((sg) => <option key={sg} value={sg}>{sg}</option>)}
            </select>
            <button type="button" onClick={onRemove} aria-label="Remove stage" className="shrink-0 rounded-md p-1.5 text-on-surface-low hover:bg-surface-highest hover:text-danger"><Trash2 size={13} /></button>
          </div>
          <textarea value={stage.objective} onChange={(e) => onPatch({ objective: e.target.value })} rows={2} placeholder="What this stage accomplishes…"
            className="mt-2 w-full resize-none rounded-md bg-surface-high px-2.5 py-1.5 text-on-surface-var text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          {/* exit criteria */}
          <div className="mt-2 flex flex-col gap-1">
            <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Done when</span>
            {(stage.exit_criteria ?? []).map((c, ci) => (
              <div key={ci} className="flex items-center gap-1.5 rounded-md bg-surface-high/60 px-2 py-1 text-[0.8125rem] text-on-surface-var">
                <span className="min-w-0 flex-1">{c}</span>
                <button type="button" onClick={() => removeCrit(ci)} aria-label="Remove criterion" className="shrink-0 text-on-surface-low hover:text-danger"><X size={12} /></button>
              </div>
            ))}
            {/* Commit on blur too — else a criterion typed but not Enter'd is silently
                lost when the user clicks Launch (which just blurs this input). */}
            <input value={crit} onChange={(e) => setCrit(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addCrit() } }}
              onBlur={addCrit}
              placeholder="Add a concrete, checkable condition…"
              className="rounded-md bg-surface-high px-2.5 py-1.5 text-[0.8125rem] text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 placeholder:text-on-surface-low" />
          </div>
          {/* P6 quality gate — the per-stage metric bar (metric_pass/metric_hold). The
              exit criteria are the structural gate (met/not-met); the quality bar is the
              graded gate on TOP of it: even a structurally-passing cycle HOLDs to refine
              if the supervisor's judge scores it below `pass`. Opt-in per stage; planner
              seeds it on verification/review. Surfaced here so the bar is user-tunable,
              not just a backend default. */}
          <StageQualityGate stage={stage} onPatch={onPatch} />
          {/* planned tasks for this stage — the checklist seeded into its TaskList
              at launch. Editable: rename, remove, or add the work items the worker
              executes one by one. */}
          <div className="mt-2 flex flex-col gap-1">
            <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Tasks{(stage.tasks ?? []).length ? ` (${stage.tasks!.length})` : ''}</span>
            {(stage.tasks ?? []).map((t, ti) => (
              <div key={ti} className="flex items-start gap-1.5 rounded-md bg-surface-high/60 px-2 py-1 text-[0.8125rem]">
                <span className="mt-1.5 size-3 shrink-0 rounded-full border border-outline-variant/60" />
                <div className="flex min-w-0 flex-1 flex-col">
                  <input value={t.title} onChange={(e) => patchTask(ti, { title: e.target.value })}
                    placeholder="Task…"
                    className="min-w-0 flex-1 bg-transparent text-on-surface-var outline-none placeholder:text-on-surface-low" />
                  {/* the task's substance — visible + editable, not hidden in a hover
                      tooltip, so the user reviews WHAT each task does, not just its name. */}
                  <input value={t.description ?? ''} onChange={(e) => patchTask(ti, { description: e.target.value })}
                    placeholder="how / details (optional)…"
                    className="min-w-0 flex-1 bg-transparent text-on-surface-low text-[0.75rem] outline-none placeholder:text-on-surface-low/60" />
                </div>
                <button type="button" onClick={() => removeTask(ti)} aria-label="Remove task" className="mt-1 shrink-0 text-on-surface-low hover:text-danger"><X size={12} /></button>
              </div>
            ))}
            <div className="flex items-center gap-1.5 rounded-md px-2 py-1">
              <span className="size-3 shrink-0 rounded-full border border-dashed border-outline-variant/50" />
              <input value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addTask() } }}
                onBlur={addTask}
                placeholder="Add a task…"
                className="min-w-0 flex-1 bg-transparent text-[0.8125rem] text-on-surface outline-none placeholder:text-on-surface-low" />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
