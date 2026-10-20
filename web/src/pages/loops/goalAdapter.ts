import type { GoalLoop, GoalType, Granularity, Loop } from '../../lib/api'

/** Adapt a unified Loop (kind=goal) to the flat GoalLoop shape the goal cockpit +
 *  plan-review view-models read — goal-specific fields live in kind_config on the
 *  unified entity. A read-boundary adapter (the fetches + the SSE snapshot) so the
 *  large views stay GoalLoop-shaped without a field-by-field rewrite. Mirrors the
 *  Code cockpit's loopToCodeProject. */
export function loopToGoalLoop(l: Loop): GoalLoop {
  const kc = (l.kind_config || {}) as Record<string, unknown>
  const arr = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : [])
  return {
    ...(l as unknown as GoalLoop),
    goal: l.task,
    sub_goals: arr(kc.sub_goals),
    deliverables: arr(kc.deliverables),
    scope: arr(kc.scope),
    goal_type: (kc.goal_type as GoalType) ?? 'open_ended',
    granularity: (kc.granularity as Granularity) ?? 'balanced',
    verify_command: String(kc.verify_command ?? ''),
    rubric: arr(kc.rubric),
    ratchet_mode: kc.ratchet_mode ? String(kc.ratchet_mode) : undefined,
    execution_plan: Array.isArray(kc.execution_plan) ? (kc.execution_plan as Record<string, unknown>[]) : [],
  }
}
