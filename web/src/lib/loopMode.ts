/** What a loop's Mode means — the one wording the composer, the plan review and the cockpit use.
 *
 *  For a loop of its own (code, goal, research, design) it is what the loop's posture enforces
 *  (`loop/posture`), for every session the loop runs: its planner, its stage worker and each task's
 *  worker, and the merges of their work. An Attended loop's planner and workers ask you for their
 *  tool calls as a chat does, on the loop's page and wherever your approvals go; a finished task's
 *  work goes into your branch only once you approve it; and its planner's and workers' spend is
 *  yours, like a chat's. An Unattended loop's sessions run on a standing grant (the workers' ends
 *  with the loop's trust window, `loops.trust_ttl_secs`, after which the loop waits for you to
 *  resume it); it merges each finished task by itself; and its spend counts against the daily cap
 *  for unattended work. Either way, a loop's commits are made under your own git name and email,
 *  and it asks you for them when git has none. Only a Code loop merges a task's work into a branch
 *  (`MERGING_KINDS`), so only its wording says so.
 *
 *  A general loop is a workflow run (`workflows/service.PORTED_LOOP_KINDS`): Attended, each step
 *  asks before it starts and its tool calls ask as a chat's do (`supervisor_policy.unattended_grant`
 *  is only an explicit Unattended); either way its steps' spend counts as a workflow run's does. */
export function loopModeLabel(attended: boolean): string {
  return attended ? 'Attended' : 'Unattended'
}

/** The loop kinds that run as a workflow run rather than as a loop of their own. */
const RUN_BACKED_KINDS = new Set(['general'])

/** The loop kinds whose tasks' work is merged into your branch (`loop/kinds/sdlc`). */
const MERGING_KINDS = new Set(['code'])

export function loopModeMeaning(attended: boolean, kind = ''): string {
  if (RUN_BACKED_KINDS.has(kind)) {
    return attended
      ? 'Attended: you are watching. Each step asks you before it starts, and its tool calls ask for your approval the way a chat’s do. Its model spend counts against the daily cap, as every workflow run’s does.'
      : 'Unattended: it runs on its own. Its steps start and their tool calls run without asking, inside your safety rules. Its model spend counts against the daily cap for unattended work.'
  }
  const merges = MERGING_KINDS.has(kind)
  return attended
    ? 'Attended: you are watching. Its planner’s and workers’ tool calls ask for your approval the way a chat’s do, on the loop’s page, the bell and your approval channel, and a worker may stop to ask you a question. '
      + (merges ? 'A finished task’s work goes into your branch only when you approve it, with its changes shown, and under your git name. ' : '')
      + 'Its planner’s and workers’ model spend is yours, like a chat’s, so the daily cap for unattended work does not count it.'
    : 'Unattended: it runs on its own. Its planner’s and workers’ tool calls run without asking, inside your safety rules; once it runs, its workers’ grant lasts until its trust window ends, and then it waits for you to resume it. '
      + (merges ? 'It merges each finished task into your branch by itself, under your git name. ' : '')
      + 'Its model spend counts against the daily cap for unattended work.'
}
