/** Settings → Agent defaults → Approval mode: each value, and what it does.
 *
 *  Strictest first, the order of its control (`config/editable.py`'s `loosens_toward`), and the
 *  first is what ships, so a value the read lacks reads as the default it is. The words say what
 *  the gateway does with each value, for a chat and for an agent no chat started (a trigger's
 *  Invoke Agent agent, a subagent started outside a chat): `approval_grants.setting_grant` is the
 *  one place "auto" approves such an agent's calls, and `chat_runner._apply_approval_floor` is
 *  where "trust_reads" lets a chat run a read-only shell command. An automation or a loop runs as
 *  its own Allow or Mode says, whatever this is. */
export interface ApprovalMode { key: string; label: string; means: string }

export const APPROVAL_MODES: ApprovalMode[] = [
  {
    key: 'interactive',
    label: 'Ask each time',
    means: 'Every call that needs approval asks you: a chat on its card, and an agent no chat started (a trigger’s Invoke Agent agent, a subagent started outside a chat) in your Inbox.',
  },
  {
    key: 'trust_reads',
    label: 'Trust reads',
    means: 'As Ask each time, and a chat also runs a read-only shell command without asking. An agent no chat started still asks you in your Inbox.',
  },
  {
    key: 'auto',
    label: 'Auto',
    means: 'An agent no chat started (a trigger’s Invoke Agent agent, a subagent started outside a chat) approves every tool call it makes, file changes and shell commands included, without asking you. Chats still ask.',
  },
]

/** The row's hint before the chosen value's own sentence. */
export const APPROVAL_MODE_HINT = 'Who approves a tool call that needs approval. A tool that only reads never asks, and an automation or a loop runs as its own Allow or Mode says.'

/** What a stored value does, including one that is not among the values (a hand edit): every call
 *  that needs approval then asks, since only an exact "auto" approves and only "trust_reads" trusts. */
export function approvalModeMeans(value: string): string {
  return APPROVAL_MODES.find((m) => m.key === value)?.means
    ?? `“${value}” is not one of its values, so every call that needs approval asks you.`
}
