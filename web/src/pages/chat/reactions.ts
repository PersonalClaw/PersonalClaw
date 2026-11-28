// ── A reaction to an agent's answer ────────────────────────────────────────────────────────────
//
// Every finished answer can take one of five reactions, and the agent is told, in the gateway's
// words (`dashboard/chat_reactions.py`): "The user reacted 👍 (thumbs up) to your message from
// 2:31 PM: …". The page sends a pick as a chat message with no words of its own, only
// `meta.reaction` (`{ to, pick }`: the answer's row time, the pick or null to take it back); the
// gateway writes the sentence and the row, and the row keeps `{ to, pick, previous }`. An answer's
// reaction is the last such row for it, so a reload, a branch and an export all read it from the
// rows. A reaction is the owner's input to the agent, never feedback: it is not `FeedbackThumbs`.

export type ReactionPick = 'thumbs_up' | 'thumbs_down' | 'heart' | 'celebrate' | 'thinking'

/** The five reactions, in the order the picker offers them, each with the name the agent is told. */
export const REACTIONS: readonly { pick: ReactionPick; emoji: string; name: string }[] = [
  { pick: 'thumbs_up', emoji: '👍', name: 'thumbs up' },
  { pick: 'thumbs_down', emoji: '👎', name: 'thumbs down' },
  { pick: 'heart', emoji: '❤️', name: 'heart' },
  { pick: 'celebrate', emoji: '🎉', name: 'celebrate' },
  { pick: 'thinking', emoji: '🤔', name: 'thinking' },
]

/** How long a pick waits before it is sent, so a quick change sends one message. */
export const REACTION_WAIT_MS = 2000

/** What a reaction's row keeps (`meta.reaction`). */
export interface ReactionRecord { to: string; pick: ReactionPick | null; previous: ReactionPick | null }

export const reactionOf = (pick: ReactionPick | null | undefined) => REACTIONS.find((r) => r.pick === pick)

const isPick = (v: unknown): v is ReactionPick => REACTIONS.some((r) => r.pick === v)

/** A row's reaction record, as the gateway keeps it; `null` for any other row. */
export function reactionRecordOf(raw: unknown): ReactionRecord | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  if (typeof r.to !== 'string' || !r.to) return null
  return { to: r.to, pick: isPick(r.pick) ? r.pick : null, previous: isPick(r.previous) ? r.previous : null }
}

/** Each answer's reaction now, by its row's time: the last reaction row for it, in row order. */
export function reactionsOf(rows: readonly { role: string; meta?: unknown }[]): Map<string, ReactionPick> {
  const picks = new Map<string, ReactionPick>()
  for (const row of rows) {
    if (row.role !== 'user') continue
    const rec = reactionRecordOf((row.meta as Record<string, unknown> | undefined)?.reaction)
    if (!rec) continue
    if (rec.pick) picks.set(rec.to, rec.pick)
    else picks.delete(rec.to)
  }
  return picks
}

/** The first words of an answer, as a reaction's line quotes it. */
export function quoteOf(text: string): string {
  const one = text.replace(/\s+/g, ' ').trim()
  return one.length <= 60 ? one : `${one.slice(0, 59).trimEnd()}…`
}

/** The line a reaction's row is drawn as, where it was sent: "You reacted 👍 to “…”". */
export function reactionLine(r: Pick<ReactionRecord, 'pick' | 'previous'>, quote: string): string {
  const now = reactionOf(r.pick)
  const was = reactionOf(r.previous)
  const to = quote ? `“${quote}”` : 'an answer'
  if (now && was) return `You changed your reaction to ${to} from ${was.emoji} to ${now.emoji}`
  if (now) return `You reacted ${now.emoji} to ${to}`
  return `You removed your ${was?.emoji ?? 'reaction'} from ${to}`
}
