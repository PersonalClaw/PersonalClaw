import { api } from './api'
import { launchChat } from '../app/appSdk'

/** Investigate Anywhere (plan 60): open a chat pre-loaded with one entity's
 *  context. POSTs /api/investigate (the envelope is composed SERVER-side from
 *  the owning store — a client can't forge a snapshot), then navigates to the
 *  staged session via the existing launch-chat path. The session opens in `ask`
 *  mode (read-only investigation); the user escalates the mode themselves. */
export async function investigate(kind: string, id: string, opts?: { backLink?: string }): Promise<void> {
  const res = await api.investigate({ kind, id, back_link: opts?.backLink })
  // The opening prompt pre-fills the composer (editable — the user always fires
  // the first turn); the context itself is injected server-side at that turn.
  launchChat({ session: res.session_key, prompt: res.context.opening_prompt || undefined })
}

/** SDK hook — apps get the same primitive (Tier-S export via installAppSdk). */
export function useInvestigate(): typeof investigate {
  return investigate
}
