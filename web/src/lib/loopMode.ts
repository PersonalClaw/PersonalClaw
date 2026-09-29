/** What a loop's Mode means — the one wording the composer, the plan review and the cockpit use.
 *
 *  It is what the loop's posture enforces (`loop/manager._arm_posture`): an Attended loop's workers
 *  ask you for their tool calls as a chat does, on the loop's page and wherever your approvals go;
 *  an Unattended loop's workers run on a standing grant that ends with the loop's trust window
 *  (`loops.trust_ttl_secs`), after which the loop waits for you to resume it. */
export function loopModeLabel(attended: boolean): string {
  return attended ? 'Attended' : 'Unattended'
}

export function loopModeMeaning(attended: boolean): string {
  return attended
    ? 'Attended: you are watching. Its workers’ tool calls ask for your approval the way a chat’s do, on the loop’s page, the bell and your approval channel, and a worker may stop to ask you a question.'
    : 'Unattended: it runs on its own. Its tool calls run without asking, inside your safety rules, until its trust window ends; then it waits for you to resume it.'
}
