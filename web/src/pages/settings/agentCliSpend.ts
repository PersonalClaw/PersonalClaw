/** What the places that show spend say of an agent CLI's turns.
 *
 *  An agent CLI makes its model calls in its own process, so a turn on one counts only by what the
 *  CLI says it cost (the gateway's `acp.turn_spend`): Claude Code says it, and a CLI that says
 *  nothing bills the turn on its own account. Said where spend is summed (the Usage page) and
 *  where its caps are set and shown (Guardrails, the Usage page's daily cap), so a total never
 *  reads as including turns it left out and a cap never reads as covering them. */

/** Beside a spend total. */
export const AGENT_CLI_USAGE =
  "An agent CLI's turn (Claude Code, Codex, Gemini CLI, Kiro CLI) counts here only by what the CLI says it cost. A turn it says nothing of is billed on the CLI's own account and isn't counted here."

/** Beside a spend cap. */
export const AGENT_CLI_CAP =
  "The cap counts an agent CLI's turn (Claude Code, Codex, Gemini CLI, Kiro CLI) only by what the CLI says it cost. A turn it says nothing of is billed on the CLI's own account, and the cap leaves its dollars out."
