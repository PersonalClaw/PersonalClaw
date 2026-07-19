/** Role-phased execution-plan helpers — the FE mirror of `loop.py`'s
 *  active_phase_index. Shared by the cockpit (full phase display) and the goals
 *  list peek (compact summary) so both compute the active phase identically. */

export type Phase = Record<string, unknown>

/** Index of the phase the upcoming cycle belongs to — cumulative min_cycles
 *  windows; stays on the last phase past the end. -1 when there's no plan. */
export function activePhaseIndex(totalCycles: number, plan: Phase[]): number {
  if (!plan.length) return -1
  let elapsed = 0
  for (let i = 0; i < plan.length; i++) {
    elapsed += Math.max(1, Number(plan[i].min_cycles) || 1)
    if (totalCycles < elapsed) return i
  }
  return plan.length - 1
}

/** Min cycles for a phase (floored at 1, like the backend). */
export function phaseMinCycles(p: Phase): number {
  return Math.max(1, Number(p.min_cycles) || 1)
}

/** Which phase a given 1-based cycle number belongs to (cumulative min_cycles
 *  windows; the LAST phase absorbs any overflow beyond the planned minimums, so
 *  a loop running past its plan keeps its later cycles under the final phase).
 *  -1 when there's no plan. */
export function phaseForCycle(cycle: number, plan: Phase[]): number {
  if (!plan.length) return -1
  let end = 0
  for (let i = 0; i < plan.length; i++) {
    end += phaseMinCycles(plan[i])
    if (cycle <= end) return i
  }
  return plan.length - 1
}
