import { type CodeProject, type CodeStage, type Loop } from '../../../lib/api'

// ── The cockpit's view of a code loop ──────────────────────────────────────────────────────────
//
// The unified Loop a code project is, in the shape the cockpit's regions draw.

// The Code cockpit is a code-shaped view-model over the unified Loop (kind=code).
// The wire format is the unified Loop (entry_stage/project_kind/verify_command/
// test_command/queued_task_ids under kind_config; the stage list as `plan`, per-stage
// status as `phase_status`); adapt it to the CodeProject shape the cockpit's many
// sub-components already render, so the migration is one boundary, not 2700 lines.
export function loopToCodeProject(p: Loop): CodeProject {
  const kc = (p.kind_config || {}) as Record<string, unknown>
  return {
    ...(p as unknown as CodeProject),
    entry_stage: (kc.entry_stage as CodeProject['entry_stage']) ?? 'ideation',
    project_kind: (kc.project_kind as CodeProject['project_kind']) ?? 'greenfield',
    verify_command: String(kc.verify_command ?? ''),
    test_command: String(kc.test_command ?? ''),
    queued_task_ids: Array.isArray(kc.queued_task_ids) ? (kc.queued_task_ids as string[]) : [],
    stage_plan: (p.plan ?? []) as unknown as CodeStage[],
    stage_status: (p.phase_status ?? {}) as Record<string, string>,
    command_runnability: p.command_runnability,
  }
}
