import type { InboxItem, SkillProposal, WaitingItem, WaitingList } from './api'
import {
  firstLine, inboxRaisedBy, isOpenStatus, isoSeconds, mirroredProposalId, named, proposalSummary,
  proposalTitle, sentenceStart,
} from './attentionLanes'

// ── Home's Needs You: the waiting list, then the Inbox's own asks ─────────────────────────────────
//
// Needs You is `GET /api/waiting` (#3888) in the order it is served: by consequence, then age, the
// one order Home, the buddy, the tray and the phone keep. Nothing here sorts those items again.
// After them come the two asks no work waits on, which the list leaves to the Inbox: a skill
// proposal waiting on a yes or no, and a note you wrote yourself. They follow every waiting item
// because answering them lets nothing run, oldest first, as the Inbox's own asks always were.
//
// A source the gateway could not read is carried as `unreadable`, so the view says it could not
// read it instead of drawing an empty group: an empty Needs You is this page's "all clear".

/** A skill proposal as Needs You reads it. */
export type ProposalInput = Pick<SkillProposal, 'id' | 'slug' | 'description' | 'created_at'>

/** An Inbox row as Needs You reads it. */
export type NoteInput = Pick<
  InboxItem,
  'id' | 'item_kind' | 'status' | 'message' | 'context_summary' | 'created_at' | 'refs' | 'sender_name' | 'channel_name'
>

interface RowBase {
  /** Unique across the three origins: `${origin}:${id}`. */
  key: string
  title: string
  /** What raised it, in words: "Workflow · release", "Loop · Fix the README", "Skills". */
  raisedBy: string
  subtitle: string
}

/** One row of Needs You, carrying the one source object its answer acts on. */
export type NeedsYouRow =
  | (RowBase & { origin: 'waiting'; item: WaitingItem })
  | (RowBase & { origin: 'proposal'; proposal: ProposalInput; at: number | null })
  | (RowBase & { origin: 'note'; note: NoteInput; at: number | null })

function text(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function record(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {}
}

/** The first question an agent's question card asks. */
function firstQuestion(brief: Record<string, unknown>): string {
  const qs = Array.isArray(brief.questions) ? brief.questions : []
  const q = record(qs[0])
  return firstLine(q.question) || firstLine(q.header)
}

/** A waiting item's title, read off its own brief, in its source's words. */
export function waitingTitle(item: WaitingItem): string {
  const b = record(item.brief)
  switch (item.kind) {
    case 'approval': return firstLine(b.tool) || 'a tool'
    case 'question': return firstQuestion(b) || 'A question for you'
    case 'loop_question':
      if ('merge' in b) return 'Merge what the loop made?'
      if ('conflict' in b) return 'The loop’s work conflicts: redo it or drop it?'
      return firstLine(b.question) || 'The loop asks you something'
    case 'gate': return firstLine(record(b.ask).prompt) || 'The run waits on you'
    case 'park': return firstLine(b.question) || firstLine(record(b.card).title) || 'The trigger waits on you'
    case 'browse_grant': return firstLine(b.task) || 'A browser task asks to go on'
    case 'plan_review': {
      const step = record(b.step)
      const what = firstLine(step.title) || firstLine(b.task) || firstLine(b.name)
      return what ? `Review the plan: ${what}` : 'Review the plan'
    }
    case 'bridge_confirmation': return firstLine(b.action) || 'An action asks to run'
    default: return firstLine(b.title) || 'Something waits on you'
  }
}

/** What raised a waiting item, in words. */
export function waitingRaisedBy(item: WaitingItem): string {
  const b = record(item.brief)
  switch (item.kind) {
    case 'approval': return sentenceStart(text(b.source_label))
    case 'question': return firstLine(b.asked_by)
    case 'loop_question': return named('Loop', b.name)
    case 'gate': return named('Workflow', b.workflow)
    case 'park': return named('Trigger', b.trigger_name)
    case 'browse_grant': return 'Browser'
    case 'plan_review': return item.work.startsWith('chat:') ? 'Chat' : named('Loop', b.name)
    case 'bridge_confirmation': return firstLine(b.asked_by) || 'Control bridge'
    default: return ''
  }
}

/** A waiting item's second line: what an approval is for, why a loop asks, what a grant reaches. */
export function waitingSubtitle(item: WaitingItem): string {
  const b = record(item.brief)
  switch (item.kind) {
    case 'approval': return firstLine(b.tool_purpose)
    case 'loop_question': return firstLine(b.why)
    case 'browse_grant': return firstLine(b.scope)
    case 'plan_review': return firstLine(record(b.step).objective)
    default: return ''
  }
}

/** Every row of Needs You: the waiting items in the list's order, then the skill proposals and your
 *  notes, oldest first. Defended as if each argument came off a failed read: a non-array or a
 *  non-object element is skipped, never thrown on, since this runs on a render path. */
export function needsYou(
  list: WaitingList | null | undefined,
  items: NoteInput[] = [],
  proposals: ProposalInput[] = [],
): NeedsYouRow[] {
  const rows: NeedsYouRow[] = []
  for (const item of Array.isArray(list?.waiting) ? list.waiting : []) {
    if (item === null || typeof item !== 'object' || typeof item.id !== 'string' || item.id === '') continue
    rows.push({
      key: `waiting:${item.id}`,
      origin: 'waiting',
      title: waitingTitle(item),
      raisedBy: waitingRaisedBy(item),
      subtitle: waitingSubtitle(item),
      item,
    })
  }
  const asks: NeedsYouRow[] = []
  const proposalIds = new Set<string>()
  for (const p of Array.isArray(proposals) ? proposals : []) {
    if (p === null || typeof p !== 'object' || typeof p.id !== 'string' || p.id === '') continue
    if (typeof p.slug !== 'string' || p.slug === '') continue
    proposalIds.add(p.id)
    asks.push({
      key: `proposal:${p.id}`,
      origin: 'proposal',
      title: proposalTitle(p),
      raisedBy: 'Skills',
      subtitle: proposalSummary(p),
      at: isoSeconds(p.created_at),
      proposal: p,
    })
  }
  for (const note of Array.isArray(items) ? items : []) {
    if (note === null || typeof note !== 'object' || typeof note.id !== 'string' || note.id === '') continue
    if (!isOpenStatus(note.status)) continue
    // A proposal's own Inbox row while the proposal is listed: the proposal is the card.
    if (note.item_kind === 'proposal' && proposalIds.has(mirroredProposalId(note))) continue
    if (note.item_kind !== 'user_note' && note.item_kind !== 'proposal') continue
    asks.push({
      key: `note:${note.id}`,
      origin: 'note',
      title: firstLine(note.message) || firstLine(note.context_summary) || '(no message)',
      raisedBy: note.item_kind === 'user_note' ? 'Your note' : inboxRaisedBy(note),
      subtitle: '',
      at: typeof note.created_at === 'number' && Number.isFinite(note.created_at) ? note.created_at : null,
      note,
    })
  }
  asks.sort((a, b) => {
    const at = (r: NeedsYouRow) => (r.origin === 'waiting' ? null : r.at)
    const x = at(a)
    const y = at(b)
    if (x === null && y === null) return a.key < b.key ? -1 : a.key > b.key ? 1 : 0
    if (x === null) return 1
    if (y === null) return -1
    return x !== y ? x - y : a.key < b.key ? -1 : a.key > b.key ? 1 : 0
  })
  return [...rows, ...asks]
}
