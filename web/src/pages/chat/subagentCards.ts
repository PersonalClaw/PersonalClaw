import type { SubagentCard } from './chatTypes'

/** A helper's live event folded into the chat's Subagents list: `subagent_spawn` adds its card,
 *  `subagent_tool` names its latest call, and `subagent_done` ends it with how it went.
 *
 *  A helper that never ran sent no `subagent_spawn`: her Deny of its start, a start nobody allowed
 *  in time, or a stop while it waited to start. Its `subagent_done` says so (`never_ran`, and
 *  `declined` for her Deny) and adds its card, so the list shows it beside the helpers that ran.
 *  Its report starts no turn in the chat, so this card is where the chat shows how it ended.
 *  Pure, so the list reads the same however the frames arrive. */
export function foldSubagentEvent(cards: SubagentCard[], type: string, d: Record<string, unknown>): SubagentCard[] {
  const id = String(d.id ?? '')
  if (!id) return cards
  if (type === 'subagent_spawn') {
    if (cards.some((s) => s.id === id)) return cards
    // A task of a batch this chat started arrives with its batch's run and its step's name.
    const batchTask = d.run ? { run: String(d.run), title: String(d.title ?? '') } : {}
    return [...cards, { id, task: String(d.task ?? ''), agent: String(d.agent ?? ''), done: false, ...batchTask }]
  }
  if (type === 'subagent_tool') {
    return cards.map((s) => s.id === id ? { ...s, lastTool: String(d.tool ?? '') } : s)
  }
  if (type !== 'subagent_done') return cards
  const ended: Partial<SubagentCard> = {
    done: true,
    error: (d.error as string | null) ?? null,
    elapsed: typeof d.elapsed === 'number' ? d.elapsed : undefined,
    result: String(d.result ?? ''),
    costUsd: typeof d.cost_usd === 'number' ? d.cost_usd : undefined,
    tokens: typeof d.tokens === 'number' ? d.tokens : undefined,
    ...(d.never_ran === true ? { neverRan: true, declined: d.declined === true } : {}),
  }
  if (cards.some((s) => s.id === id)) return cards.map((s) => s.id === id ? { ...s, ...ended } : s)
  if (d.never_ran !== true) return cards
  return [...cards, { id, task: String(d.task ?? ''), agent: String(d.agent ?? ''), ...ended, done: true }]
}
