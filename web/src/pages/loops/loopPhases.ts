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

/** Does a loop have a name of its OWN, or is its name just the top of its goal?
 *
 *  A loop is auto-named from its goal, hard-truncated — measured on this dev home,
 *  `name` came back as a 60-character mid-word cut of `task` ("…for our bus-r"). The
 *  list row shows the name as its title and the goal beneath it, so for such a loop
 *  BOTH LINES ARE THE SAME SENTENCE: the second one is filler that costs a line of
 *  scanning and says nothing. 1 of the 3 non-code loops here was in that state; the
 *  other two ("RAIDZ2 vs dRAID homelab report", "Morning bus tier options analysis")
 *  are named properly, and for those the goal genuinely adds information.
 *
 *  Compared case-insensitively on the collapsed prefix, and any trailing ellipsis is
 *  dropped first so a name the backend truncated with "…" still matches its goal.
 */
export function hasDistinctName(name: string, goal: string): boolean {
  const n = (name ?? '').replace(/[…\s]+$/u, '').trim().toLowerCase()
  const g = (goal ?? '').trim().toLowerCase()
  if (!n) return false
  return !g.startsWith(n)
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
