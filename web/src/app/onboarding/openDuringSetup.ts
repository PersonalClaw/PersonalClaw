/** The pages first-run setup never stands in front of.
 *
 *  A home whose setup is not done is pulled into the flow on every route (`App.tsx`'s guard),
 *  because those pages run on setup's answers: a name to greet and attribute by, a model to chat
 *  with. These pages run on none of them. Each one cuts something off — access to this gateway, or
 *  the agent's unattended work — and a sign-in link that leaked, or a run that misbehaves, does not
 *  wait for setup to end. Held behind it, the terminal (`personalclaw logout`) was the only way to
 *  sign a device out of a fresh home, and nothing reachable could suspend unattended work.
 *
 *  Settings subpage ids:
 *
 *   · `devices` — sign out one device, every other one, or an integration token;
 *   · `security` — replace the sign-in key, which signs every device out at once;
 *   · `guardrails` — the incident switch, which suspends all unattended work;
 *   · `external-access` — the switch for every inbound surface, and each client's revoke;
 *   · `sender-trust` — who may reach the agent through a messaging channel, each with a revoke.
 *
 *  🔑 MEMBERSHIP ASKS "DOES THIS PAGE CUT SOMETHING OFF?", never "is this page harmless before
 *  setup?". Nearly every page is harmless before setup, so a list grown on that test would quietly
 *  become the whole app, with setup reduced to a screen nobody is shown. */
export const OPEN_DURING_SETUP: readonly string[] = ['devices', 'security', 'guardrails', 'external-access', 'sender-trust']

/** Whether `#/<route>/<sub>` is one of them. Exact on the subpage: a path below one is not a page
 *  Settings renders, so it is not one of these either. */
export function opensDuringSetup(route: string, sub: string): boolean {
  return route === 'settings' && OPEN_DURING_SETUP.includes(sub)
}
