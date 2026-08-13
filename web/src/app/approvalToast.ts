import { blastRadiusLine, deriveBlastRadius, type ApprovalRisk } from '../pages/chat/approvalMeta'

/** OU-8 — the COMPACT form of the approval brief.
 *
 *  The out-of-context nudge (`useApprovalToasts`) is not a second approval renderer and must
 *  never become one: it carries no Allow, no Deny, and no scope. What it owes the reader is
 *  the same first question the card answers — what would this call touch — in the one line a
 *  toast has room for, so a user can tell "another chat wants to read a file" from "another
 *  chat wants to run a shell command" without opening it.
 *
 *  It draws its words from `approvalMeta`'s single facet vocabulary, so the chip on the card
 *  and the phrase in the toast cannot drift apart. When nothing is established the clause is
 *  omitted entirely rather than rendered as "nothing established" — see the honesty contract
 *  in `approvalMeta`.
 *
 *  Deliberately NOT advocating and deliberately not alarming: the sentence states who is
 *  asking, what for, what it can touch, and where to answer. It does not tell the reader what
 *  to do, and it does not editorialise on the risk level (the card's risk chip does that in
 *  context, where the arguments are also visible).
 */
export function approvalToastMessage(input: {
  /** "A subagent" / "A background task" / "Another chat session". */
  who: string
  tool: string
  session: string
  risk?: ApprovalRisk
}): string {
  const line = blastRadiusLine(deriveBlastRadius({ tool: input.tool, risk: input.risk }))
  const touches = line ? ` (${line})` : ''
  return `${input.who} needs approval to run ${input.tool}${touches} — open ${input.session} to respond.`
}
