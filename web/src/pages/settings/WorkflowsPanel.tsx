import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, RowGroup, ToggleRow, NumberRow, SegRow, SelectRow, TextRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'

// The editable `workflows.*` fields, mirroring the backend `_EDITABLE_CONFIG` allowlist
// (`config/loader.py` WorkflowsConfig). Each control PATCHes ONE allowlisted path through
// /api/config/personalclaw; nothing here spreads the fetched config object into a write.
//
// 🔴 ONE ALLOWLISTED `workflows.*` PATH IS DELIBERATELY ABSENT, and that absence is the point:
//
//   · `workflows.max_active_runs` — zero readers outside the plumbing. `watchdog.py`'s adopt loop
//                                   iterates `store.active_runs()` with no cap check, so the
//                                   unbounded stacking its help text names is what actually
//                                   happens.
//
// It is an INERT path (issue #465), which is a DIFFERENT defect from a path with no control: an
// inert knob needs its reader wired or its allowlist row dropped, and giving it a Settings control
// would only make a promise the code still ignores more convincing. It stays uncontrolled until
// #465 decides which way it goes.
//
// `workflows.max_concurrent_nodes` used to sit beside it here. The #465 half of this same change
// took the second option — it is DELETED from `WorkflowsConfig` and off the allowlist, because
// `lane_caps()` returns the two per-lane fields plus a hardcoded `compute: 64` and never consulted
// the total it claimed to partition. The two `Lane cap` rows below ARE that partition, so there is
// nothing left to control.
type WorkflowsCfg = Record<string, unknown>

// The model use cases a tier may resolve to — the chat family from `ModelsPanel`'s USE_CASE_META,
// which is the same vocabulary `providers/use_cases.py` resolves. 6 options (>4) → a Select.
const TIER_USE_CASES = [
  { value: 'chat', label: 'Chat' },
  { value: 'code_tools', label: 'Code & tools' },
  { value: 'reasoning', label: 'Reasoning' },
  { value: 'background', label: 'Background' },
  { value: 'orchestration', label: 'Orchestration' },
  { value: 'loops', label: 'Loops' },
] as const

/** Workflows — the engine's runtime knobs.
 *
 *  Every field here was PATCH-editable with no control anywhere in `web/` (issue #2801 measured
 *  `workflows` as the largest of nine such sections, 21 keys), so the only way to retune the engine
 *  was a raw API call or a hand edit of `config.json`. The DEFINITIONS, runs and triggers live on
 *  `#/workflows`; this panel is only the engine settings those surfaces run under. */
export function WorkflowsPanel() {
  const [cfg, setCfg] = useState<WorkflowsCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:workflows', () =>
    api.personalclawConfig().then((c) => (c.workflows ?? {}) as WorkflowsCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  // A settings panel must not present FABRICATED values as saved state: a failed read reaches the
  // hook (no `.catch(() => ({}))`), so the form is replaced by the failure instead of rendering
  // every control at its fallback and inviting an edit of values it never loaded.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={3} what="settings" />

  // Optimistic single-field PATCH; a rejected save rolls back and surfaces the error
  // (a swallowed 400 would look exactly like a successful save).
  const patch = (key: string, value: unknown, onSaved?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`workflows.${key}`, value).then(() => onSaved?.()).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Workflows" hint="How the workflow engine runs: what may start, how much runs at once, how long a node gets, and what a newly authored workflow is allowed to do before you opt it in." />

      <Section title="Engine" hint="The master switch and the ceiling on work the agent schedules for itself.">
        <RowGroup>
          <ToggleRow label="Workflow engine" cfg={cfg} field="enabled" patch={patch}
            hint="Master switch. Off stops new runs from starting and leaves stored definitions untouched — nothing is deleted." />
          <NumberRow label="Self-scheduled tasks — max outstanding" cfg={cfg} field="self_schedule_max_outstanding" min={0} max={200} patch={patch}
            hint="How many enabled automations the agent may hold at once via its own scheduling tools. Counted over enabled agent-created automations, so pausing one frees a slot without deleting it." />
        </RowGroup>
      </Section>

      <Section title="Concurrency & timeouts" hint="Typed lanes so a long local-model action cannot block a run's model calls, plus the clocks that stop a wedged node.">
        <RowGroup>
          <NumberRow label="Lane cap — model calls" cfg={cfg} field="max_concurrent_llm_nodes" min={1} max={32} patch={patch}
            hint="How many model-backed nodes may run at once in one workflow." />
          <NumberRow label="Lane cap — actions" cfg={cfg} field="max_concurrent_io_nodes" min={1} max={32} patch={patch}
            hint="How many action nodes may run at once. Kept low on purpose: a fan-out over minutes-long local-model actions would otherwise starve the run's model calls behind it." />
          <NumberRow label="Node timeout — total (seconds)" cfg={cfg} field="default_node_timeout_total_secs" min={0} max={86400} patch={patch}
            hint="Wall-clock cap for one node. 0 disables it." />
          <NumberRow label="Node timeout — stall (seconds)" cfg={cfg} field="default_node_timeout_stall_secs" min={0} max={86400} patch={patch}
            hint="Stop a node after this long with NO progress, even when it is under the total cap. Progress events reset the clock, so a slow-but-working node survives while a wedged one does not. 0 disables it." />
          <NumberRow label="Task claim lifetime (seconds)" cfg={cfg} field="lease_ttl_secs" min={30} max={3600} patch={patch}
            hint="How long a session's exclusive claim on a task lasts before another may take it. Deliberately short: a worker that needs longer renews, which proves it is alive." />
        </RowGroup>
      </Section>

      <Section title="Model tiers" hint="Templates name an intent — reasoning, standard, fast — never a model, so they stay portable. This maps each intent onto one of your model use cases.">
        <RowGroup>
          <SelectRow label="Reasoning tier" cfg={cfg} field="model_tier_reasoning" patch={patch}
            options={[...TIER_USE_CASES]} fallback="reasoning"
            hint="Which model use case a node asking for the reasoning tier resolves to." />
          <SelectRow label="Standard tier" cfg={cfg} field="model_tier_standard" patch={patch}
            options={[...TIER_USE_CASES]} fallback="orchestration"
            hint="Use case for the standard tier. Keep it distinct from Fast: if both point at one use case the three tiers are decorative, and a node asking for a mid-capability model silently gets the cheapest one." />
          <SelectRow label="Fast tier" cfg={cfg} field="model_tier_fast" patch={patch}
            options={[...TIER_USE_CASES]} fallback="background"
            hint="Use case for the fast tier." />
        </RowGroup>
      </Section>

      <Section title="Surfacing & matching" hint="What a workflow may do on its own before you opt it in, and how a tie between two templates is broken.">
        <RowGroup>
          <SegRow label="New workflow surfacing" cfg={cfg} field="surface_mode_default" patch={patch}
            fallback="off"
            options={[{ key: 'off', label: 'Off' }, { key: 'passive', label: 'Passive' }, { key: 'suggest', label: 'Suggest' }]}
            hint="What a NEWLY authored workflow does before you opt it in. Off never surfaces itself (running it explicitly always works); Passive injects its guidance; Suggest may propose running itself. Off is the default — auto-trigger-by-default is the mistake that let pasted content fire workflows." />
          <NumberRow label="Template match threshold" cfg={cfg} field="match_threshold" min={0} max={1} step={0.01} patch={patch}
            hint="How confident the embedding tie-breaker must be to override a keyword tie when two templates score alike. Only consulted on a tie — a keyword match always decides first." />
        </RowGroup>
      </Section>

      <Section title="Fan-out & retention" hint="Bounds so one run cannot bury your task board or grow history without limit.">
        <RowGroup>
          <NumberRow label="Task fan-out cap" cfg={cfg} field="max_materialized_per_foreach" min={1} max={500} patch={patch}
            hint="The most Tasks one foreach node may put on your board. The run still executes every item — only the board rows are capped, and the run reports what it withheld." />
          <NumberRow label="Runs kept per workflow" cfg={cfg} field="retention_per_def" min={1} max={10000} patch={patch}
            hint="Oldest runs beyond this are pruned. Matches the per-job cap schedules use." />
        </RowGroup>
      </Section>

      <Section title="Approvals & scheduling defaults" hint="How long a run waits for you, and the defaults a new automation inherits when it declares none of its own.">
        <RowGroup>
          <NumberRow label="Approval lifetime (seconds)" cfg={cfg} field="confirmation_ttl_secs" min={0} max={2592000} step={3600} patch={patch}
            hint="How long a pending approval stays live. A week by default, because the realistic case is being away. 0 means it never expires. A destructive confirmation auto-rejects on expiry; an ordinary one keeps waiting." />
          <TextRow label="Default quiet hours" cfg={cfg} field="default_quiet_windows" patch={patch} mono
            placeholder="22:00-08:00"
            hint="A quiet window applied to new automations that set none of their own, as HH:MM-HH:MM. A window may wrap midnight. Empty means no default — an automation you created deliberately should run when you told it to. Per-trigger settings always win." />
          <TextRow label="Default duty gate" cfg={cfg} field="duty_gate_default" patch={patch} mono
            placeholder="manual"
            hint="The are-you-on-duty check applied to new automations that name none. Empty means no gate; manual is the built-in on/off toggle, and apps can supply others. The gate always fails open, so a broken calendar app can never silence everything." />
        </RowGroup>
      </Section>

      <Section title="Workspaces" hint="Where a run does its work when its template does not say, and what happens to that directory afterwards.">
        <RowGroup>
          <SegRow label="Default workspace mode" cfg={cfg} field="workspace_default_mode" patch={patch}
            fallback="scratch"
            options={[{ key: 'scratch', label: 'Scratch' }, { key: 'worktree', label: 'Worktree' }, { key: 'in_place', label: 'In place' }, { key: 'container', label: 'Container' }]}
            hint="Scratch is a per-run directory, Worktree a git worktree of the project, In place the real tree with no isolation, Container an isolated image. A template's own declaration always wins. Scratch is the default because being wrong about isolation should cost a copy, not the original." />
          <ToggleRow label="Run teardown before deletion" cfg={cfg} field="workspace_teardown_on_expiry" patch={patch}
            hint="Run a workspace's declared teardown command before its directory is deleted by retention or an explicit delete. On, because teardown's whole job is to stop services and sync work out while the directory still exists." />
        </RowGroup>
      </Section>
    </div>
  )
}
