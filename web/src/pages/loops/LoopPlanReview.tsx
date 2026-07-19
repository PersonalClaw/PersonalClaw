import { useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ArrowLeft, ArrowRight, Play, Plus, X, Sparkles, HelpCircle, AlertTriangle, Check, Download, Sparkle, Workflow, ChevronUp, ChevronDown, Loader2, Eye } from 'lucide-react'
import { Modal } from '../../ui/Modal'
import { Markdown } from '../../ui/Markdown'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { Segmented } from '../../ui/Segmented'
import { spring } from '../../design/motion'
import { api, type GoalLoop, type GoalType, type SkillItem, type WorkflowItem, type SkillSearchResult, type GrillPhase } from '../../lib/api'
import type { LoopDraft } from './loopDraft'
import { loopToGoalLoop } from './goalAdapter'

const GOAL_TYPES: { id: GoalType; label: string }[] = [
  { id: 'verifiable', label: 'Verifiable' },
  { id: 'open_ended', label: 'Open-ended' },
  { id: 'monitor', label: 'Monitor' },
]

/** Plain-language stop behavior derived from type + dial (§10.2). */
function stopBehavior(loop: GoalLoop, goalType: GoalType): string {
  if (goalType === 'monitor') return "I'll watch this continuously — you stop it when you're done."
  if (goalType === 'verifiable') {
    return loop.verify_command
      ? `I'll keep cycling until \`${loop.verify_command}\` passes, then stop.`
      : "I'll keep cycling until the success check passes, then stop."
  }
  if (loop.granularity === 'forever') return "I'll keep going indefinitely — you stop it when you're satisfied."
  const dial: Record<string, string> = {
    quick: 'as soon as a cycle stops adding much',
    balanced: 'when gains shrink for a couple of cycles',
    exhaustive: 'only once it’s truly dry',
  }
  return `I'll stop when new cycles stop adding value — ${dial[loop.granularity] ?? 'when returns drop'} (${loop.granularity}).`
}

// One question in the guided walk. Two sources feed the SAME walk model (no dual
// path): flat classify clarifications → id `g<n>`, no phase; grill's guided-
// decomposition tree (#16) → id `p<phase>s<step>`, tagged with its phase title +
// index so the walk chrome can show "Phase N of M · <title>". `phaseKey` groups the
// launch-time answer store into kind_config.phase_answers.
interface WalkQuestion {
  id: string; prompt: string
  phase?: string          // phase title (guided decomposition only)
  phaseIndex?: number     // 0-based phase ordinal (guided decomposition only)
  phaseCount?: number     // total phases (guided decomposition only)
}

// One phase of the role-phased execution plan (IT-6). Capabilities are per-phase.
interface PlanPhase {
  role: string; agent_name: string; target: string; min_cycles: number
  phase_exit: string; skill_ids: string[]; workflow_ids: string[]
}

export function LoopPlanReview({ draft, onLaunched, onBack }: {
  draft: LoopDraft
  onLaunched: (loopId: string) => void
  onBack: () => void
}) {
  const [loop, setLoop] = useState<GoalLoop | null>(null)
  const [title, setTitle] = useState(draft.classification.title || '')
  const [editingTitle, setEditingTitle] = useState(false)
  const [subGoals, setSubGoals] = useState<string[]>([])
  const [goalType, setGoalType] = useState<GoalType>('open_ended')
  const [verifyCommand, setVerifyCommand] = useState('')
  const [answers, setAnswers] = useState<Record<string, string>>(() =>
    (((draft.classification.kind_config as Record<string, unknown> | undefined)?.phase_answers as Record<string, string> | undefined)) ?? {})
  const [step, setStep] = useState(0)   // 0 = overview; 1 = capabilities; 2..N = questions; N+1 = launch
  const [launching, setLaunching] = useState(false)
  const [launchError, setLaunchError] = useState<string | null>(null)

  // ── guided decomposition (#16): grill's memory-checked question-TREE, the richer
  // intake behind intake_rigor='thorough'. When phases load they REPLACE the flat
  // clarify walk (same WalkQuestion model, phase-tagged). Auto-fetched for a thorough
  // classification; also user-triggerable from the overview. `null` = not fetched.
  // Seed from kind_config.grill_phases so a RESUMED review-status loop rehydrates its
  // phases (+ prior phase_answers) without a re-fetch (kind_config round-trips whole).
  const seededPhases = ((draft.classification.kind_config as Record<string, unknown> | undefined)?.grill_phases as GrillPhase[] | undefined) ?? null
  const [grillPhases, setGrillPhases] = useState<GrillPhase[] | null>(seededPhases && seededPhases.length ? seededPhases : null)
  const [grillMemoryHits, setGrillMemoryHits] = useState(0)
  const [grillLoading, setGrillLoading] = useState(false)
  const [grillError, setGrillError] = useState<string | null>(null)
  const isThorough = (draft.rigor || draft.classification.intake_rigor || '').toLowerCase() === 'thorough'

  async function runGuidedDecomposition() {
    if (grillLoading) return
    setGrillLoading(true); setGrillError(null)
    try {
      const res = await api.grillTree(draft.loopId)
      if (res.phases?.length) { setGrillPhases(res.phases); setGrillMemoryHits(res.memory_hits || 0) }
      else setGrillError('The planner returned no phases — the flat questions still apply.')
    } catch (e) {
      setGrillError((e as Error).message || 'Could not build the guided decomposition.')
    } finally { setGrillLoading(false) }
  }
  // Purely OPT-IN (the "Guide me" button) — not auto-run. Non-minimal goals already
  // pass through the planner-agent walkthrough (which decomposes intent/sub-goals/
  // plan), so auto-firing a second question-tree here would re-interrogate after the
  // fact. `isThorough` only surfaces a soft "recommended" nudge on the affordance.

  // ── capabilities (IT-4): installed skills/workflows the loop loads each cycle,
  // pre-checked from the planner's suggestions; plus marketplace skills to install.
  const [installedSkills, setInstalledSkills] = useState<SkillItem[]>([])
  const [installedWorkflows, setInstalledWorkflows] = useState<WorkflowItem[]>([])
  const [skillIds, setSkillIds] = useState<Set<string>>(new Set(draft.classification.suggested_skill_ids ?? []))
  const [workflowIds, setWorkflowIds] = useState<Set<string>>(new Set(draft.classification.suggested_workflow_ids ?? []))
  const [installing, setInstalling] = useState<Record<string, boolean>>({})
  const [installed, setInstalled] = useState<Set<string>>(new Set())
  const marketplaceSuggestions = draft.classification.marketplace_suggestions ?? []

  // ── execution plan (IT-6): the planner's role-phased plan, each phase carrying
  // its own capabilities (loaded only during that phase). Editable; persisted on
  // launch. Present only when the planner emitted phases.
  const [phases, setPhases] = useState<PlanPhase[]>(
    // Unified shape: the role-phased execution_plan lives in kind_config (goal-specific),
    // not at the classification top level.
    (((draft.classification.kind_config as Record<string, unknown> | undefined)?.execution_plan as Record<string, unknown>[] | undefined) ?? []).map((p) => ({
      role: String(p.role ?? ''), agent_name: String(p.agent_name ?? ''),
      target: String(p.target ?? ''), min_cycles: Number(p.min_cycles ?? 1) || 1,
      phase_exit: String(p.phase_exit ?? ''),
      skill_ids: Array.isArray(p.skill_ids) ? p.skill_ids.map(String) : [],
      workflow_ids: Array.isArray(p.workflow_ids) ? p.workflow_ids.map(String) : [],
    })),
  )
  const hasPlan = phases.length > 0
  // Saved agent definitions — for the per-phase agent dropdown (replaces the
  // freeform text box); the planner's suggested agent_name is pre-selected.
  const [agentNames, setAgentNames] = useState<string[]>([])

  useEffect(() => {
    let alive = true
    api.uLoop(draft.loopId).then((raw) => {
      if (!alive) return
      const l = loopToGoalLoop(raw)
      setLoop(l); setSubGoals(l.sub_goals ?? []); setGoalType(l.goal_type)
      setVerifyCommand(l.verify_command ?? '')
      if (!title) setTitle(l.name || '')
    }).catch(() => {})
    api.savedAgents().then((list) => { if (alive) setAgentNames(list.map((a) => a.name)) }).catch(() => {})
    // Installed capabilities for the picker (best-effort).
    api.skills().then((s) => { if (alive) setInstalledSkills(s) }).catch(() => {})
    api.workflows().then((w) => { if (alive) setInstalledWorkflows(w.filter((x) => x.enabled !== false)) }).catch(() => {})
    return () => { alive = false }
  }, [draft.loopId])  // eslint-disable-line

  // The clarifying-question walk — ONE model, two sources (no dual path):
  //  - guided decomposition (#16): grill's memory-checked phases, each step tagged
  //    with its phase title/index so the walk shows "Phase N of M". Takes precedence.
  //  - otherwise the flat clarifications from classify (id `g<n>`, no phase).
  const questions = useMemo<WalkQuestion[]>(() => {
    if (grillPhases && grillPhases.length) {
      const out: WalkQuestion[] = []
      grillPhases.forEach((ph, pi) => {
        ph.steps.forEach((st, si) => {
          out.push({ id: `p${pi}s${si}`, prompt: st.prompt, phase: ph.title || `Phase ${pi + 1}`,
                     phaseIndex: pi, phaseCount: grillPhases.length })
        })
      })
      return out
    }
    return (draft.classification.clarifying_questions ?? []).map((q, i) => ({ id: `g${i}`, prompt: q }))
  }, [grillPhases, draft.classification.clarifying_questions])

  // Steps: 0 overview · 1 capabilities · [2 plan, if the planner emitted one] ·
  // questions · launch. The plan step inserts a one-index offset when present.
  const planOffset = hasPlan ? 1 : 0
  const qStart = 2 + planOffset                 // first question's step index
  const totalSteps = questions.length + 3 + planOffset
  const onOverview = step === 0
  const onCapabilities = step === 1
  const onPlan = hasPlan && step === 2
  const onLaunch = step === totalSteps - 1
  const curQuestion = step >= qStart && !onLaunch ? questions[step - qStart] : null

  function toggleId(set: Set<string>, setter: (s: Set<string>) => void, id: string) {
    const next = new Set(set)
    next.has(id) ? next.delete(id) : next.add(id)
    setter(next)
  }

  async function installMarketplaceSkill(s: SkillSearchResult) {
    setInstalling((m) => ({ ...m, [s.id]: true }))
    try {
      // The install response's `path` is the installed skill DIR — its basename is
      // the local skill key (which often differs from the marketplace id/name), so
      // match on it to reliably move the freshly-installed skill into the available
      // list AND auto-select it. Fall back to id/name matching if path is absent.
      const res = await api.installSkill(s.id, s.source || 'skills.sh')
      setInstalled((prev) => new Set(prev).add(s.id))
      const installedKey = res?.path ? res.path.replace(/\/+$/, '').split('/').pop() : undefined
      const fresh = await api.skills().catch(() => installedSkills)
      setInstalledSkills(fresh)
      const match = fresh.find((x) => x.key === installedKey)
        || fresh.find((x) => x.key === s.id || x.name === s.name)
      if (match) setSkillIds((prev) => new Set(prev).add(match.key))
    } catch { /* leave un-installed; user can retry */ }
    finally { setInstalling((m) => ({ ...m, [s.id]: false })) }
  }

  function answerValue(q: WalkQuestion): string {
    return answers[q.id] ?? ''
  }

  function next() {
    setStep((s) => Math.min(totalSteps - 1, s + 1))
  }

  async function launch() {
    if (launching || !loop) return
    setLaunching(true); setLaunchError(null)
    try {
      // Fold the answered clarifications into the goal task so the worker's brief
      // reflects them (pre-launch nudges are rejected by the unified backend; the
      // durable spec is the task text + kind_config). Mirrors CodeCreatePage.
      // Guided decomposition (#16) groups the fold BY PHASE for a richer scoped brief;
      // the flat walk stays a single "Clarifications:" block (identical to before).
      const answered = questions.map((q) => ({ q, a: (answers[q.id] || '').trim() })).filter((x) => x.a)
      let taskText = loop.goal
      if (grillPhases && grillPhases.length && answered.length) {
        const byPhase = grillPhases.map((ph, pi) => {
          const rows = answered.filter((x) => x.q.phaseIndex === pi)
          return rows.length ? `${ph.title || `Phase ${pi + 1}`}:\n${rows.map((x) => `- ${x.q.prompt} → ${x.a}`).join('\n')}` : ''
        }).filter(Boolean)
        if (byPhase.length) taskText = `${loop.goal}\n\nScoping (guided decomposition):\n\n${byPhase.join('\n\n')}`
      } else if (answered.length) {
        taskText = `${loop.goal}\n\nClarifications:\n${answered.map((x) => `- ${x.q.prompt} → ${x.a}`).join('\n')}`
      }
      // Structured phase answers persisted alongside the phases so a resumed/inspected
      // loop keeps the guided-decomposition record (kind_config round-trips wholesale).
      const phaseAnswers = grillPhases ? Object.fromEntries(answered.map((x) => [x.q.id, x.a])) : undefined
      // Unified update: spine fields at top level, goal-specific in kind_config
      // (goal_type/sub_goals/granularity/verify_command/execution_plan). Flat goal
      // fields would be dropped by update_spec, so they MUST go through kind_config.
      await api.updateULoop(draft.loopId, {
        name: title.trim() || loop.name,
        task: taskText,
        // The sub-goal list is the unified `plan` (rows keyed by title).
        plan: subGoals.map((s) => ({ title: s })),
        // Capabilities the user confirmed → injected actively each cycle. Flat ids =
        // the always-on baseline; per-phase ids ride in kind_config.execution_plan.
        skill_ids: [...skillIds], workflow_ids: [...workflowIds],
        kind_config: {
          goal_type: goalType,
          sub_goals: subGoals,
          ...(hasPlan ? { execution_plan: phases } : {}),
          ...(goalType === 'verifiable' ? { verify_command: verifyCommand.trim() } : {}),
          // Guided decomposition (#16): persist the memory-checked phases + the
          // structured answers so the record survives resume/inspect.
          ...(grillPhases && grillPhases.length ? { grill_phases: grillPhases, phase_answers: phaseAnswers } : {}),
        },
      }).catch(() => {})
      // start re-runs pre-flight validation server-side; surface a rejection
      // (e.g. the worker agent no longer resolves) instead of silently resetting.
      await api.uLoopAction(draft.loopId, 'start')
      onLaunched(draft.loopId)
    } catch (e) {
      setLaunchError((e as Error).message || 'Could not launch the loop')
      setLaunching(false)
    }
  }

  if (!loop) return <div className="flex h-full items-center justify-center text-on-surface-low text-[0.875rem]">Analyzing the plan…</div>

  // Header: back + (editable) generated title + a compact step indicator.
  const header = (
    <TopBar
      left={
        <div className="flex items-center gap-s min-w-0">
          <IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} />
          {editingTitle ? (
            <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)}
              onBlur={() => setEditingTitle(false)} onKeyDown={(e) => { if (e.key === 'Enter') setEditingTitle(false) }}
              className="h-8 min-w-[16rem] rounded-md bg-surface-high px-m text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          ) : (
            <button type="button" onClick={() => setEditingTitle(true)} title="Edit title"
              className="truncate text-on-surface text-[0.9375rem] hover:text-on-surface-var" style={{ fontVariationSettings: '"wght" 500' }}>
              {title || 'Untitled loop'}
            </button>
          )}
        </div>
      }
      right={<span className="text-on-surface-low text-[0.75rem] tabular-nums">Step {step + 1} / {totalSteps}</span>}
    />
  )

  return (
    <div className="flex h-full flex-col">
      {header}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
          <AnimatePresence mode="wait">
            <motion.div key={step} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={spring.spatialFast}>
              {onOverview ? (
                <OverviewStep
                  loop={loop} goalType={goalType} setGoalType={setGoalType} rigor={draft.rigor}
                  subGoals={subGoals} setSubGoals={setSubGoals}
                  verifyCommand={verifyCommand} setVerifyCommand={setVerifyCommand}
                  strategyId={loop.strategy_id} multiAgent={draft.classification.execution === 'multi_agent'}
                  unclassified={draft.classification.classified === false}
                  guided={{ phases: grillPhases, memoryHits: grillMemoryHits, loading: grillLoading,
                            error: grillError, isThorough, run: runGuidedDecomposition,
                            clear: () => { setGrillPhases(null); setGrillError(null) } }}
                />
              ) : onCapabilities ? (
                <CapabilitiesStep
                  skills={installedSkills} workflows={installedWorkflows}
                  skillIds={skillIds} workflowIds={workflowIds}
                  onToggleSkill={(id) => toggleId(skillIds, setSkillIds, id)}
                  onToggleWorkflow={(id) => toggleId(workflowIds, setWorkflowIds, id)}
                  suggestedSkillIds={draft.classification.suggested_skill_ids ?? []}
                  suggestedWorkflowIds={draft.classification.suggested_workflow_ids ?? []}
                  marketplace={marketplaceSuggestions} installed={installed} installing={installing}
                  onInstall={installMarketplaceSkill}
                />
              ) : onPlan ? (
                <PlanStep phases={phases} setPhases={setPhases}
                  skills={installedSkills} workflows={installedWorkflows}
                  agentNames={agentNames} />
              ) : onLaunch ? (
                <LaunchStep loop={loop} title={title} goalType={goalType} subGoals={subGoals}
                  verifyCommand={verifyCommand}
                  skillIds={[...skillIds]} workflowIds={[...workflowIds]}
                  installedSkills={installedSkills} installedWorkflows={installedWorkflows}
                  phases={phases}
                  answered={questions.filter((q) => answerValue(q).trim()).length} totalQ={questions.length} />
              ) : curQuestion ? (
                <QuestionStep
                  q={curQuestion} index={step - qStart + 1} total={questions.length}
                  value={answerValue(curQuestion)}
                  onChange={(v) => setAnswers((a) => ({ ...a, [curQuestion.id]: v }))}
                  onAdvance={next}
                />
              ) : null}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      {/* Launch rejection (e.g. server-side pre-flight validation failed). */}
      {launchError && onLaunch && (
        <div role="alert" className="shrink-0 px-l pb-1" style={{ marginInline: 'auto', width: '100%', maxWidth: 'var(--content-width)' }}>
          <div className="rounded-lg px-4 py-2.5 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-error) 8%, transparent)', color: 'var(--color-error)' }}>{launchError}</div>
        </div>
      )}

      {/* Footer nav — Back/Skip/Next, Launch on the final step. */}
      <div className="shrink-0 border-t border-outline-variant/30 px-l py-m flex items-center justify-between" style={{ marginInline: 'auto', width: '100%', maxWidth: 'var(--content-width)' }}>
        <Button variant="ghost" size="sm" onClick={() => step === 0 ? onBack() : setStep((s) => s - 1)}>
          <ArrowLeft size={15} /> {step === 0 ? 'Cancel' : 'Back'}
        </Button>
        {onLaunch ? (
          <Button onClick={launch} disabled={launching}><Play size={16} /> {launching ? 'Launching…' : 'Launch'}</Button>
        ) : curQuestion ? (
          <div className="flex items-center gap-s">
            <Button variant="ghost" size="sm" onClick={() => setStep((s) => s + 1)}>Skip</Button>
            <Button size="sm" onClick={next}>Next <ArrowRight size={15} /></Button>
          </div>
        ) : (
          <Button size="sm" onClick={() => setStep((s) => s + 1)}>
            {onOverview ? 'Capabilities'
              : onCapabilities && hasPlan ? 'Review plan'
              : (questions.length ? 'Review questions' : 'Continue')} <ArrowRight size={15} />
          </Button>
        )}
      </div>
    </div>
  )
}

interface GuidedProps {
  phases: GrillPhase[] | null; memoryHits: number; loading: boolean; error: string | null
  isThorough: boolean; run: () => void; clear: () => void
}

function OverviewStep({ loop, goalType, setGoalType, rigor, subGoals, setSubGoals, verifyCommand, setVerifyCommand, strategyId, multiAgent, unclassified, guided }: {
  loop: GoalLoop; goalType: GoalType; setGoalType: (t: GoalType) => void; rigor: string
  subGoals: string[]; setSubGoals: (v: string[]) => void
  verifyCommand: string; setVerifyCommand: (v: string) => void
  strategyId?: string; multiAgent: boolean
  unclassified?: boolean
  guided: GuidedProps
}) {
  return (
    <div className="flex flex-col gap-l">
      {/* The classifier couldn't analyze the goal (LLM unavailable/garbled) and
          fell back to open-ended defaults — tell the user to confirm the type so
          a verifiable goal isn't silently run as a never-verifying open one. */}
      {unclassified && (
        <div role="alert" className="rounded-lg px-4 py-3 text-[0.8125rem] flex items-start gap-2"
          style={{ background: 'color-mix(in srgb, var(--color-warning) 10%, transparent)', color: 'var(--color-warning)' }}>
          <AlertTriangle size={15} className="shrink-0 mt-0.5" />
          <span>I couldn’t analyze this goal automatically, so these are safe defaults (open-ended). Please confirm the goal type and sub-goals below before launching.</span>
        </div>
      )}
      <div className="flex flex-col gap-s">
        <div className="flex flex-wrap items-center gap-s">
          <span className="text-on-surface-low text-[0.875rem]">I read this as a</span>
          <Segmented ariaLabel="Goal type" value={goalType} onChange={(v) => setGoalType(v as GoalType)}
            options={GOAL_TYPES.map((t) => ({ key: t.id, label: t.label }))} />
          <span className="text-on-surface-low text-[0.875rem]">goal · {rigor} depth</span>
        </div>
        <p className="text-on-surface-var text-[0.9375rem]">{stopBehavior(loop, goalType)}</p>
      </div>

      <GuidedDecomposition guided={guided} />

      {/* Verifiable goals need a deterministic check the supervisor runs to
          decide done-ness. Surface it as an editable field (was display-only),
          so toggling to Verifiable here lets the user actually provide the
          command instead of launching a loop that can never self-complete. */}
      {goalType === 'verifiable' && (
        <Section label="Verify command">
          <input
            value={verifyCommand}
            onChange={(e) => setVerifyCommand(e.target.value)}
            placeholder="e.g. make ci · npm test · 0 lint warnings"
            spellCheck={false}
            className="w-full h-9 rounded-lg bg-surface-container px-m font-mono text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          <p className="mt-1.5 text-on-surface-low text-[0.75rem]">
            {verifyCommand.trim()
              ? 'The supervisor runs this each cycle; exit code 0 means done.'
              : 'No check yet — without one, the loop runs to its cycle budget instead of self-completing.'}
          </p>
        </Section>
      )}

      <Section label="Sub-goals (become Tasks)" action={<SuggestMoreSubGoals goal={loop.goal} value={subGoals} onChange={setSubGoals} />}>
        <SubGoalsEdit value={subGoals} onChange={setSubGoals} />
      </Section>

      {multiAgent && (loop.roster?.length ?? 0) > 0 && (
        <Section label="Execution — multi-agent">
          <p className="text-on-surface-low text-[0.8125rem] mb-2">
            Orchestration: <span className="text-on-surface-var">{strategyId ?? 'orchestrator'}</span>
          </p>
          <div className="flex flex-col gap-1.5">
            {loop.roster!.map((m, i) => (
              <div key={i} className="flex flex-col gap-0.5 rounded-lg bg-surface-container px-m py-2.5">
                <span className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 550' }}>{m.role}</span>
                {m.persona && <span className="text-on-surface-var text-[0.8125rem]">{m.persona}</span>}
                {m.role_hint && <span className="text-on-surface-low text-[0.75rem] mt-0.5">↳ {m.role_hint}</span>}
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}

/** Guided decomposition (#16) — the overview affordance for grill's memory-checked
 *  question-tree. Idle: an opt-in "Guide me" button (auto-run for thorough goals).
 *  Loading: a spinner. Loaded: a preview of the phases + a note when memory shaped
 *  them, so the user knows the upcoming walk is phased. The walk itself renders the
 *  questions (this is just the entry/preview on the overview step). */
function GuidedDecomposition({ guided }: { guided: GuidedProps }) {
  const { phases, memoryHits, loading, error, isThorough, run, clear } = guided
  const total = phases?.reduce((n, p) => n + p.steps.length, 0) ?? 0
  return (
    <Section label="Guided decomposition"
      action={phases && phases.length
        ? <div className="flex items-center gap-s">
            <button type="button" onClick={run} className="text-on-surface-low text-[0.75rem] hover:text-on-surface">Rebuild</button>
            <button type="button" onClick={clear} className="text-on-surface-low text-[0.75rem] hover:text-on-surface">Use flat questions</button>
          </div>
        : loading ? null
        : <Button variant="ghost" size="sm" onClick={run}>
            <Sparkles size={14} /> Guide me{isThorough ? ' · recommended' : ''}
          </Button>}>
      {loading ? (
        <div className="flex items-center gap-2 rounded-lg bg-surface-container px-m py-3 text-on-surface-low text-[0.8125rem]">
          <Loader2 size={15} className="animate-spin text-primary" /> Scoping the goal into phases — checking memory for what’s already settled…
        </div>
      ) : phases && phases.length ? (
        <div className="flex flex-col gap-2">
          {memoryHits > 0 && (
            <div className="flex items-center gap-1.5 text-[0.75rem]" style={{ color: 'var(--color-primary)' }}>
              <Check size={12} /> Memory-checked — skipped questions you’ve already answered before.
            </div>
          )}
          <p className="text-on-surface-low text-[0.8125rem]">
            {phases.length} phase{phases.length > 1 ? 's' : ''} · {total} scoped question{total > 1 ? 's' : ''} — you’ll walk them next, phase by phase.
          </p>
          <div className="flex flex-col gap-1.5">
            {phases.map((ph, i) => (
              <div key={i} className="rounded-lg bg-surface-container px-m py-2.5">
                <div className="flex items-baseline gap-2">
                  <span className="shrink-0 text-[0.7rem] text-on-surface-low tabular-nums">Phase {i + 1}</span>
                  <span className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 550' }}>{ph.title}</span>
                </div>
                {ph.description && <p className="mt-0.5 text-on-surface-low text-[0.75rem]">{ph.description}</p>}
                <div className="mt-1 text-on-surface-var text-[0.75rem]">{ph.steps.length} question{ph.steps.length > 1 ? 's' : ''}</div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <p className="text-on-surface-low text-[0.8125rem]">
            Instead of a flat question list, I’ll ask a few <span className="text-on-surface-var">phases</span> of scoped
            questions that build on each other — memory-checked so I don’t re-ask what I already know. Best for fuzzy goals.
          </p>
          {error && <p className="text-[0.75rem]" style={{ color: 'var(--color-warning)' }}>{error}</p>}
        </div>
      )}
    </Section>
  )
}

/** A selectable capability row (skill or workflow) with a checkbox + suggested chip. */
function CapRow({ id, name, description, checked, suggested, onToggle, onPeek, icon }: {
  id: string; name: string; description?: string; checked: boolean; suggested: boolean
  onToggle: () => void; onPeek?: () => void; icon: React.ReactNode
}) {
  return (
    <div key={id}
      className={`group flex w-full items-start gap-s rounded-lg px-m py-2.5 transition-colors ${checked ? 'bg-surface-high ring-1 ring-primary/40' : 'bg-surface-container hover:bg-surface-high'}`}>
      {/* the row body toggles selection; the peek button is separate (stopPropagation) */}
      <button type="button" onClick={onToggle} className="flex flex-1 min-w-0 items-start gap-s text-left">
        <span className="mt-0.5 shrink-0 inline-flex size-4 items-center justify-center rounded-sm border" style={{ borderColor: checked ? 'var(--color-primary)' : 'var(--color-outline-variant)', background: checked ? 'var(--color-primary)' : 'transparent' }}>
          {checked && <Check size={11} className="text-on-primary" />}
        </span>
        <span className="shrink-0 mt-0.5 text-on-surface-low">{icon}</span>
        <span className="flex-1 min-w-0">
          <span className="flex items-center gap-1.5">
            <span className="text-on-surface text-[0.875rem] truncate" style={{ fontVariationSettings: '"wght" 550' }}>{name}</span>
            {suggested && <span className="shrink-0 rounded-pill px-1.5 h-4 inline-flex items-center text-[0.6rem] uppercase tracking-wide" style={{ background: 'color-mix(in srgb, var(--color-primary) 18%, transparent)', color: 'var(--color-primary)' }}>suggested</span>}
          </span>
          {description && <span className="block text-on-surface-low text-[0.75rem] truncate">{description}</span>}
        </span>
      </button>
      {onPeek && (
        <button type="button" onClick={(e) => { e.stopPropagation(); onPeek() }} title="Preview — read the full skill/workflow"
          aria-label={`Preview ${name}`}
          className="shrink-0 mt-0.5 rounded-md p-1 text-on-surface-low opacity-0 transition-opacity hover:bg-surface-highest hover:text-on-surface group-hover:opacity-100 focus:opacity-100">
          <Eye size={14} />
        </button>
      )}
    </div>
  )
}

function CapabilitiesStep({ skills, workflows, skillIds, workflowIds, onToggleSkill, onToggleWorkflow, suggestedSkillIds, suggestedWorkflowIds, marketplace, installed, installing, onInstall }: {
  skills: SkillItem[]; workflows: WorkflowItem[]
  skillIds: Set<string>; workflowIds: Set<string>
  onToggleSkill: (id: string) => void; onToggleWorkflow: (id: string) => void
  suggestedSkillIds: string[]; suggestedWorkflowIds: string[]
  marketplace: SkillSearchResult[]; installed: Set<string>; installing: Record<string, boolean>
  onInstall: (s: SkillSearchResult) => void
}) {
  // Suggested-first ordering so the planner's picks rise to the top.
  const orderedSkills = [...skills].sort((a, b) => Number(suggestedSkillIds.includes(b.key)) - Number(suggestedSkillIds.includes(a.key)))
  const orderedWorkflows = [...workflows].sort((a, b) => Number(suggestedWorkflowIds.includes(b.id)) - Number(suggestedWorkflowIds.includes(a.id)))
  const selectedCount = skillIds.size + workflowIds.size
  // Hide marketplace suggestions that are ALREADY installed (this run or a prior
  // one). The planner suggests by marketplace id/name and doesn't know what's on
  // disk; installed skills carry a local `key` (often ≠ marketplace id) + name, so
  // match on a normalized name/key/id set. (Otherwise an installed skill keeps
  // re-appearing under "install" across every goal-loop run.)
  const norm = (x: string) => x.toLowerCase().replace(/[^a-z0-9]+/g, '')
  const installedKeys = new Set<string>()
  for (const sk of skills) { installedKeys.add(norm(sk.key)); if (sk.name) installedKeys.add(norm(sk.name)) }
  const marketplaceToShow = marketplace.filter((m) => !(installedKeys.has(norm(m.id)) || installedKeys.has(norm(m.name)) || installed.has(m.id)))
  // Peek: which capability the user is previewing (skill content fetched on open;
  // workflow steps render from the in-hand item). Lets them study a suggestion
  // before committing it to the loop.
  const [peek, setPeek] = useState<{ kind: 'skill' | 'workflow'; skill?: SkillItem; workflow?: WorkflowItem } | null>(null)
  return (
    <div className="flex flex-col gap-l max-w-[680px] mx-auto py-l">
      <div className="flex flex-col gap-1">
        <h2 data-type="headline-s" className="text-on-surface">Capabilities for this goal</h2>
        <p className="text-on-surface-var text-[0.875rem]">
          Pick the skills and workflows the loop should load <span className="text-on-surface">actively every cycle</span>. The planner pre-selected what looks relevant — adjust freely. {selectedCount > 0 ? `${selectedCount} selected.` : 'None selected — the agent will still trigger-match skills as it goes.'}
        </p>
      </div>

      <Section label={`Skills · ${skills.length} installed`}>
        {orderedSkills.length === 0 ? (
          <p className="text-on-surface-low text-[0.8125rem]">No skills installed.</p>
        ) : (
          <div className="flex flex-col gap-1.5">
            {orderedSkills.map((s) => (
              <CapRow key={s.key} id={s.key} name={s.name} description={s.description}
                checked={skillIds.has(s.key)} suggested={suggestedSkillIds.includes(s.key)}
                onToggle={() => onToggleSkill(s.key)} onPeek={() => setPeek({ kind: 'skill', skill: s })} icon={<Sparkle size={14} />} />
            ))}
          </div>
        )}
      </Section>

      {orderedWorkflows.length > 0 && (
        <Section label={`Workflows · ${workflows.length} installed`}>
          <div className="flex flex-col gap-1.5">
            {orderedWorkflows.map((w) => (
              <CapRow key={w.id} id={w.id} name={w.name} description={w.description}
                checked={workflowIds.has(w.id)} suggested={suggestedWorkflowIds.includes(w.id)}
                onToggle={() => onToggleWorkflow(w.id)} onPeek={() => setPeek({ kind: 'workflow', workflow: w })} icon={<Workflow size={14} />} />
            ))}
          </div>
        </Section>
      )}

      {marketplaceToShow.length > 0 && (
        <Section label="Suggested to install — from the marketplace">
          <div className="flex flex-col gap-1.5">
            {marketplaceToShow.map((s) => {
              const done = installed.has(s.id)
              return (
                <div key={s.id} className="flex items-start gap-s rounded-lg bg-surface-container px-m py-2.5">
                  <span className="shrink-0 mt-0.5 text-on-surface-low"><Sparkle size={14} /></span>
                  <span className="flex-1 min-w-0">
                    <span className="flex items-center gap-1.5">
                      <span className="text-on-surface text-[0.875rem] truncate" style={{ fontVariationSettings: '"wght" 550' }}>{s.name}</span>
                      {typeof s.installs === 'number' && s.installs > 0 && <span className="shrink-0 text-on-surface-low text-[0.7rem]">{s.installs.toLocaleString()} installs</span>}
                    </span>
                    {s.description && <span className="block text-on-surface-low text-[0.75rem] line-clamp-2">{s.description}</span>}
                  </span>
                  <button type="button" disabled={done || !!installing[s.id]} onClick={() => onInstall(s)}
                    className="shrink-0 inline-flex items-center gap-1 rounded-pill border border-outline-variant/50 px-m h-7 text-[0.75rem] text-primary hover:bg-surface-high transition-colors disabled:opacity-50">
                    {done ? <><Check size={13} /> Installed</> : installing[s.id] ? 'Installing…' : <><Download size={13} /> Install</>}
                  </button>
                </div>
              )
            })}
          </div>
        </Section>
      )}

      {peek && <CapabilityPeekModal peek={peek} onClose={() => setPeek(null)} />}
    </div>
  )
}

/** Preview the full content of a suggested skill or workflow, so the user can
 *  study it before committing it to the loop. Skills fetch their SKILL.md body;
 *  workflows render their steps from the in-hand item. */
function CapabilityPeekModal({ peek, onClose }: {
  peek: { kind: 'skill' | 'workflow'; skill?: SkillItem; workflow?: WorkflowItem }
  onClose: () => void
}) {
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(peek.kind === 'skill')
  useEffect(() => {
    if (peek.kind !== 'skill' || !peek.skill) return
    let alive = true
    setLoading(true)
    api.skillContent(peek.skill.key)
      .then((c) => { if (alive) setContent(c) })
      .catch(() => { if (alive) setContent('') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [peek])

  const title = peek.kind === 'skill' ? peek.skill?.name : peek.workflow?.name
  const icon = peek.kind === 'skill' ? <Sparkle size={18} className="text-primary" /> : <Workflow size={18} className="text-primary" />
  return (
    <Modal title={title || 'Preview'} icon={icon} onClose={onClose}>
      <div className="max-h-[60vh] overflow-y-auto">
        {peek.kind === 'skill' ? (
          loading ? (
            <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem] py-4"><Loader2 size={14} className="animate-spin" /> Loading skill…</div>
          ) : content ? (
            <Markdown>{content}</Markdown>
          ) : (
            <p className="text-on-surface-low text-[0.8125rem]">{peek.skill?.description || 'No content available.'}</p>
          )
        ) : peek.workflow ? (
          <div className="flex flex-col gap-3">
            {peek.workflow.description && <p className="text-on-surface-var text-[0.875rem]">{peek.workflow.description}</p>}
            <div className="flex flex-wrap gap-1.5 text-[0.7rem]">
              {peek.workflow.scope && <span className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low">scope: {peek.workflow.scope}</span>}
              {(peek.workflow.tags ?? []).map((t) => <span key={t} className="rounded-pill bg-surface-high px-2 h-5 inline-flex items-center text-on-surface-low">{t}</span>)}
            </div>
            <div className="flex flex-col gap-2">
              <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">{peek.workflow.steps.length} step{peek.workflow.steps.length === 1 ? '' : 's'}</span>
              {peek.workflow.steps.map((st, i) => (
                <div key={st.id || i} className="rounded-lg bg-surface-container px-m py-2.5">
                  <div className="flex items-center gap-2">
                    <span className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.7rem] tabular-nums">{i + 1}</span>
                    <span className="text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>{st.title}</span>
                  </div>
                  {st.instruction && <p className="mt-1 pl-7 text-on-surface-var text-[0.8125rem] whitespace-pre-wrap">{st.instruction}</p>}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}

/** A compact multi-select dropdown of capability ids for a single phase. */
function PhaseCapPicker({ label, options, selected, onChange }: {
  label: string; options: { id: string; name: string }[]; selected: string[]; onChange: (ids: string[]) => void
}) {
  if (options.length === 0) return null
  const toggle = (id: string) => onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])
  return (
    <div className="flex flex-col gap-1">
      <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">{label}</span>
      <div className="flex flex-wrap gap-1">
        {options.map((o) => {
          const on = selected.includes(o.id)
          return (
            <button key={o.id} type="button" onClick={() => toggle(o.id)} title={o.id}
              className={`inline-flex items-center gap-1 rounded-pill px-2 h-6 text-[0.7rem] transition-colors ${on ? 'text-on-primary' : 'text-on-surface-low hover:text-on-surface'}`}
              style={{ background: on ? 'var(--color-primary)' : 'var(--color-surface-container)' }}>
              {on && <Check size={10} />}{o.name}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** One editable phase card in the execution plan. */
function PhaseCard({ phase, index, total, skills, workflows, agentNames, onChange, onRemove, onMoveUp, onMoveDown }: {
  phase: PlanPhase; index: number; total: number
  skills: SkillItem[]; workflows: WorkflowItem[]; agentNames: string[]
  onChange: (p: PlanPhase) => void; onRemove: () => void
  onMoveUp: () => void; onMoveDown: () => void
}) {
  const set = (patch: Partial<PlanPhase>) => onChange({ ...phase, ...patch })
  return (
    <div className="flex flex-col gap-s rounded-lg bg-surface-container px-m py-3">
      <div className="flex items-center gap-s">
        <span className="shrink-0 inline-flex size-6 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.75rem] tabular-nums">{index + 1}</span>
        {/* Reorder — phase sequence is what the orchestrator runs in order, so a
            user must be able to fix ordering without delete-and-recreate. */}
        {total > 1 && (
          <div className="shrink-0 flex flex-col -my-1">
            <button type="button" onClick={onMoveUp} disabled={index === 0} title="Move phase up"
              className="text-on-surface-low hover:text-on-surface disabled:opacity-25 disabled:pointer-events-none"><ChevronUp size={14} /></button>
            <button type="button" onClick={onMoveDown} disabled={index === total - 1} title="Move phase down"
              className="text-on-surface-low hover:text-on-surface disabled:opacity-25 disabled:pointer-events-none"><ChevronDown size={14} /></button>
          </div>
        )}
        <input value={phase.role} onChange={(e) => set({ role: e.target.value })} placeholder="role (e.g. researcher)"
          className="flex-1 min-w-0 h-8 rounded-md bg-surface-high px-2 text-on-surface text-[0.875rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" style={{ fontVariationSettings: '"wght" 550' }} />
        {/* Agent definition for this phase — a dropdown of saved agents (the
            planner's pick is pre-selected); "default worker" = empty = the loop
            worker does it inline. A pre-selected agent no longer installed is
            still shown so the choice isn't silently lost. */}
        <select value={phase.agent_name} onChange={(e) => set({ agent_name: e.target.value })}
          className="w-40 h-8 rounded-md bg-surface-high px-2 text-on-surface-var text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50">
          <option value="">default worker</option>
          {phase.agent_name && !agentNames.includes(phase.agent_name) && <option value={phase.agent_name}>{phase.agent_name} (not installed)</option>}
          {agentNames.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <label className="shrink-0 inline-flex items-center gap-1 text-on-surface-low text-[0.7rem]">
          <span>min</span>
          <input type="number" min={1} value={phase.min_cycles} onChange={(e) => set({ min_cycles: Math.max(1, Number(e.target.value) || 1) })}
            className="w-12 h-8 rounded-md bg-surface-high px-1.5 text-on-surface text-[0.8125rem] text-center outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        </label>
        {total > 1 && <button type="button" onClick={onRemove} className="shrink-0 text-on-surface-low hover:text-danger" title="Remove phase"><X size={15} /></button>}
      </div>
      <textarea value={phase.target} onChange={(e) => set({ target: e.target.value })} rows={2} placeholder="what this phase aims to accomplish"
        className="rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] placeholder:text-on-surface-low outline-none resize-y focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <input value={phase.phase_exit} onChange={(e) => set({ phase_exit: e.target.value })} placeholder="advance when… (exit signal)"
        className="h-8 rounded-md bg-surface-high px-2 text-on-surface-var text-[0.8125rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <PhaseCapPicker label="Skills this phase" options={skills.map((s) => ({ id: s.key, name: s.name }))}
        selected={phase.skill_ids} onChange={(ids) => set({ skill_ids: ids })} />
      <PhaseCapPicker label="Workflows this phase" options={workflows.map((w) => ({ id: w.id, name: w.name }))}
        selected={phase.workflow_ids} onChange={(ids) => set({ workflow_ids: ids })} />
    </div>
  )
}

function PlanStep({ phases, setPhases, skills, workflows, agentNames }: {
  phases: PlanPhase[]; setPhases: (p: PlanPhase[]) => void
  skills: SkillItem[]; workflows: WorkflowItem[]; agentNames: string[]
}) {
  const update = (i: number, p: PlanPhase) => setPhases(phases.map((x, j) => (j === i ? p : x)))
  const remove = (i: number) => setPhases(phases.filter((_, j) => j !== i))
  const add = () => setPhases([...phases, { role: '', agent_name: '', target: '', min_cycles: 1, phase_exit: '', skill_ids: [], workflow_ids: [] }])
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= phases.length) return
    const next = [...phases]
    ;[next[i], next[j]] = [next[j], next[i]]
    setPhases(next)
  }
  return (
    <div className="flex flex-col gap-l max-w-[720px] mx-auto py-l">
      <div className="flex flex-col gap-1">
        <h2 data-type="headline-s" className="text-on-surface">Execution plan</h2>
        <p className="text-on-surface-var text-[0.875rem]">
          The planner split this goal into phases. Each runs for at least its min cycles, then advances on its exit signal. Capabilities you set here load <span className="text-on-surface">only during that phase</span> — on top of the baseline you picked.
        </p>
      </div>
      <div className="flex flex-col gap-s">
        {phases.map((p, i) => (
          <PhaseCard key={i} phase={p} index={i} total={phases.length}
            skills={skills} workflows={workflows} agentNames={agentNames}
            onChange={(np) => update(i, np)} onRemove={() => remove(i)}
            onMoveUp={() => move(i, -1)} onMoveDown={() => move(i, 1)} />
        ))}
      </div>
      <button type="button" onClick={add}
        className="self-start inline-flex items-center gap-1.5 rounded-pill border border-outline-variant/50 px-m h-8 text-[0.8125rem] text-on-surface-var hover:bg-surface-high transition-colors">
        <Plus size={14} /> Add phase
      </button>
    </div>
  )
}

function QuestionStep({ q, index, total, value, onChange, onAdvance }: {
  q: WalkQuestion; index: number; total: number; value: string
  onChange: (v: string) => void; onAdvance: () => void
}) {
  const phased = q.phase != null && q.phaseIndex != null && q.phaseCount != null
  return (
    <div className="flex flex-col gap-l max-w-[640px] mx-auto py-l">
      <div className="flex flex-col gap-1.5">
        {/* Guided decomposition (#16): a phase ribbon so the user sees where this
            question sits in the memory-checked question-tree. Flat walks omit it. */}
        {phased && (
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 rounded-pill px-2.5 h-6 text-[0.7rem]"
              style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)', color: 'var(--color-primary)' }}>
              <Sparkles size={12} /> Phase {q.phaseIndex! + 1} of {q.phaseCount} · {q.phase}
            </span>
          </div>
        )}
        <span className="text-on-surface-low text-[0.75rem]">Question {index} of {total}</span>
      </div>
      <div className="flex items-start gap-s">
        <HelpCircle size={20} className="text-info shrink-0 mt-0.5" />
        <h2 data-type="headline-s" className="text-on-surface">{q.prompt}</h2>
      </div>
      <textarea autoFocus rows={5} value={value} onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onAdvance() } }}
        placeholder="Your answer — or skip it and I'll investigate/assume during the run."
        className="rounded-lg bg-surface-container px-l py-m text-on-surface text-[0.9375rem] placeholder:text-on-surface-low outline-none resize-y focus:ring-2 focus:ring-inset focus:ring-primary/50" />
      <span className="text-on-surface-low text-[0.7rem]">⌘↵ for the next question</span>
    </div>
  )
}

function LaunchStep({ loop, title, goalType, subGoals, verifyCommand, skillIds, workflowIds, installedSkills, installedWorkflows, phases, answered, totalQ }: {
  loop: GoalLoop; title: string; goalType: GoalType; subGoals: string[]
  verifyCommand: string; skillIds: string[]; workflowIds: string[]
  installedSkills: SkillItem[]; installedWorkflows: WorkflowItem[]
  phases: PlanPhase[]; answered: number; totalQ: number
}) {
  const typeLabel = GOAL_TYPES.find((t) => t.id === goalType)?.label ?? goalType
  const granularityLabel = loop.granularity.charAt(0).toUpperCase() + loop.granularity.slice(1)
  // Resolve capability ids → display names (fall back to the id when not found).
  const skillName = (id: string) => installedSkills.find((s) => s.key === id)?.name ?? id
  const workflowName = (id: string) => installedWorkflows.find((w) => w.id === id)?.name ?? id
  const baselineSkills = skillIds.map(skillName)
  const baselineWorkflows = workflowIds.map(workflowName)
  return (
    <div className="flex flex-col gap-l max-w-[640px] mx-auto py-l">
      <h2 data-type="headline-s" className="text-on-surface">Ready to launch</h2>
      <p className="text-on-surface-var text-[0.9375rem]">{stopBehavior(loop, goalType)}</p>

      <div className="flex flex-col gap-1.5 text-[0.875rem] text-on-surface-low">
        <div>Title: <span className="text-on-surface-var">{title || loop.name}</span></div>
        <div>Type: <span className="text-on-surface-var">{typeLabel}</span> · Mode: <span className="text-on-surface-var">{loop.attended ? 'Attended' : 'Unattended'}</span> · Granularity: <span className="text-on-surface-var">{granularityLabel}</span></div>
        {goalType === 'verifiable' && verifyCommand.trim() && (
          <div>Verify: <code className="text-on-surface-var font-mono text-[0.8125rem]">{verifyCommand.trim()}</code></div>
        )}
        {totalQ > 0 && <div>Questions answered: <span className="text-on-surface-var">{answered}/{totalQ}</span> <span className="opacity-70">(the rest I’ll investigate)</span></div>}
      </div>

      {/* The goal as it'll run. */}
      <Section label="Goal">
        <p className="text-on-surface text-[0.9375rem]">{loop.goal}</p>
        {loop.success_criteria && <p className="mt-1 text-on-surface-low text-[0.8125rem]"><span className="text-on-surface-var">Done when:</span> {loop.success_criteria}</p>}
      </Section>

      {subGoals.length > 0 && (
        <Section label={`Sub-goals · ${subGoals.length} (become Tasks)`}>
          <ul className="flex flex-col gap-1.5">
            {subGoals.map((s, i) => (
              <li key={i} className="flex items-start gap-s text-on-surface text-[0.875rem]">
                <span className="mt-2 size-1 shrink-0 rounded-pill bg-primary" />{s}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {phases.length > 0 && (
        <Section label={`Execution plan · ${phases.length} phases`}>
          <div className="flex flex-col gap-1.5">
            {phases.map((p, i) => (
              <div key={i} className="rounded-lg bg-surface-container px-m py-2 flex flex-col gap-0.5">
                <div className="flex items-center gap-s">
                  <span className="shrink-0 inline-flex size-5 items-center justify-center rounded-pill bg-surface-high text-on-surface-low text-[0.65rem] tabular-nums">{i + 1}</span>
                  <span className="text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>{p.role || `Phase ${i + 1}`}</span>
                  <span className="text-on-surface-low text-[0.7rem]">{p.agent_name || 'default worker'} · ≥{Math.max(1, p.min_cycles)} cycle{Math.max(1, p.min_cycles) !== 1 ? 's' : ''}</span>
                </div>
                {p.target && <span className="pl-7 text-on-surface-var text-[0.8125rem]">{p.target}</span>}
                {(p.skill_ids.length > 0 || p.workflow_ids.length > 0) && (
                  <div className="pl-7 flex flex-wrap items-center gap-1 mt-0.5 text-[0.65rem]">
                    {p.skill_ids.map((s) => <span key={s} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low">{skillName(s)}</span>)}
                    {p.workflow_ids.map((w) => <span key={w} className="inline-flex items-center rounded-pill px-1.5 h-5 bg-surface-high text-on-surface-low">{workflowName(w)}</span>)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {(baselineSkills.length > 0 || baselineWorkflows.length > 0) && (
        <Section label={phases.length > 0 ? 'Always-on capabilities' : 'Capabilities'}>
          <div className="flex flex-wrap items-center gap-1 text-[0.75rem]">
            {baselineSkills.map((n, i) => <span key={`s${i}`} className="inline-flex items-center rounded-pill px-2 h-5 bg-surface-container text-on-surface-var">{n}</span>)}
            {baselineWorkflows.map((n, i) => <span key={`w${i}`} className="inline-flex items-center rounded-pill px-2 h-5 bg-surface-container text-on-surface-var">{n}</span>)}
          </div>
        </Section>
      )}
    </div>
  )
}

function Section({ label, children, action }: { label: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-s">
      <div className="flex items-center justify-between">
        <div className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">{label}</div>
        {action}
      </div>
      {children}
    </div>
  )
}

/** "Suggest more" — re-probes the planner for additional sub-goals and appends
 *  the ones not already in the list (case-insensitive dedup). Lets the user grow
 *  the decomposition on the Plan Review without leaving the step. */
function SuggestMoreSubGoals({ goal, value, onChange }: { goal: string; value: string[]; onChange: (v: string[]) => void }) {
  const [busy, setBusy] = useState(false)
  async function suggest() {
    if (busy || goal.trim().length < 20) return
    setBusy(true)
    try {
      // Re-probe via the unified goal classifier (the legacy /suggest route is gone);
      // its kind_config.sub_goals is the decomposition. Append the case-insensitive-new.
      const r = await api.classifyULoop('goal', goal.trim())
      const suggested = (((r.kind_config as Record<string, unknown> | undefined)?.sub_goals) as string[] | undefined)
        ?? (r.plan ?? []).map((p) => String(p.title ?? '')).filter(Boolean)
      const have = new Set(value.map((s) => s.trim().toLowerCase()))
      const fresh = suggested.filter((s) => s.trim() && !have.has(s.trim().toLowerCase()))
      if (fresh.length) onChange([...value, ...fresh])
    } catch { /* best-effort; leave the list as-is */ }
    finally { setBusy(false) }
  }
  return (
    <button type="button" onClick={suggest} disabled={busy || goal.trim().length < 20}
      className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem] text-primary hover:bg-surface-high transition-colors disabled:opacity-40">
      {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />} Suggest more
    </button>
  )
}

function SubGoalsEdit({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  return (
    <div className="flex flex-col gap-1.5">
      {value.map((s, i) => (
        <div key={i} className="flex items-center gap-s rounded-lg bg-surface-container px-m py-2">
          <span className="mt-0.5 size-1 shrink-0 rounded-pill bg-primary" />
          <span className="flex-1 min-w-0 text-on-surface text-[0.875rem]">{s}</span>
          <button type="button" onClick={() => onChange(value.filter((_, j) => j !== i))} className="text-on-surface-low hover:text-on-surface"><X size={14} /></button>
        </div>
      ))}
      <div className="flex items-center gap-s">
        <input value={draft} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && draft.trim()) { onChange([...value, draft.trim()]); setDraft('') } }}
          placeholder="Add a sub-goal…"
          className="flex-1 h-9 rounded-lg bg-surface-container px-m text-on-surface text-[0.875rem] placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
        <IconButton icon={Plus} label="Add sub-goal" size={34} onClick={() => { if (draft.trim()) { onChange([...value, draft.trim()]); setDraft('') } }} />
      </div>
    </div>
  )
}
