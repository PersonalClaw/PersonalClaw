import type { LucideIcon } from 'lucide-react'
import {
  AppWindow, Archive, Bot, BookOpen, Brain, Cable, ClipboardCheck, FileText, FolderLock, FolderOpen,
  GitMerge, GitPullRequestClosed, Globe, Hand, HelpCircle, Layers, Lightbulb, ListChecks, MessageSquareQuote,
  NotebookPen, PauseCircle, Repeat, RotateCcw, ShieldCheck, Sparkles, StickyNote, TrendingUp, Workflow, Zap,
} from 'lucide-react'
import type { NeedsYouRow } from './needsYou'

// ── Needs you's kinds: each card's tag, its noun and its icon ────────────────────────────────────
//
// One registry, so a card, Learning's chips, Skills and the Proposals view name a kind one way. The
// words are pinned in the fixture the server's and the web's card tests share
// (`web/src/test/fixtures/needsYouKinds.json`, `tags`), which `needsYouKinds.test.ts` holds this
// file to: a kind with no tag would draw an empty tag row, and two names for one kind would read
// as two kinds.

export interface KindWords {
  /** The noun its tag says, in sentence case ("New template", "Permission needed"). */
  tag: string
  /** Its 12 px icon, beside the noun, `aria-hidden`. */
  icon: LucideIcon
}

const k = (tag: string, icon: LucideIcon): KindWords => ({ tag, icon })

/** A learning proposal's tag, by its card's variant (`learning.cards.variant_of`). */
export const LEARNING_KINDS: Record<string, KindWords> = {
  'template.named': k('New template', Workflow),
  'template.plan_shape': k('New template', Workflow),
  'template.recurring_path': k('New template', Workflow),
  'template.card': k('New template', Workflow),
  'prompt.card': k('New prompt', MessageSquareQuote),
  'agent.card': k('New agent', Bot),
  prompt: k('New prompt', MessageSquareQuote),
  agent: k('New agent', Bot),
  lesson: k('Lesson', Lightbulb),
  'lesson.failed_run': k('Lesson', Lightbulb),
  'lesson.outcome': k('Lesson', Lightbulb),
  'lesson.working_habit': k('Working habit', Brain),
  'lesson.conflict': k('Lesson conflict', Hand),
  'lesson.captures': k('Captures', NotebookPen),
  'skill.promoted': k('New skill', Sparkles),
  template_diff: k('Template change', Workflow),
  retirement: k('Retirement', Archive),
  'retirement.text_not_typed': k('Retirement', Archive),
  tier: k('Tier change', Layers),
  'tier.wider_scope': k('Wider scope', TrendingUp),
  project_instruction: k('Project instruction', FileText),
  project_file: k('Project file', FileText),
  project_skill: k('Project skill', Sparkles),
  knowledge: k('Knowledge draft', BookOpen),
}

/** A skill proposal's tag, by its kind. */
export const SKILL_KINDS: Record<string, KindWords> = {
  refine: k('Skill change', Sparkles),
  new: k('New skill', Sparkles),
}

/** A waiting item's tag, by its kind (`GET /api/waiting`), the Inbox-only asks included. */
export const WAITING_KINDS: Record<string, KindWords> = {
  approval: k('Permission needed', ShieldCheck),
  question: k('Question', HelpCircle),
  loop_question: k('Loop', Repeat),
  gate: k('Workflow', Workflow),
  park: k('Automation', Zap),
  browse_grant: k('Browser', Globe),
  plan_review: k('Plan', ListChecks),
  bridge_confirmation: k('Control bridge', Cable),
  folder_trust: k('Folder trust', FolderLock),
  held_action: k('Held action', PauseCircle),
  declined_wait: k('Workflow', Workflow),
  escalated_run: k('Workflow', Workflow),
}

/** The kinds a loop's question is, by what it asks, and the Inbox's own asks. */
export const OTHER_KINDS: Record<string, KindWords> = {
  merge: k('Ready to merge', GitMerge),
  conflict: k('Conflict', GitPullRequestClosed),
  note: k('Note', StickyNote),
  app_proposal: k('App proposal', AppWindow),
  chat_to_file: k('Chat to file', FolderOpen),
  autonomy: k('Autonomy', ClipboardCheck),
  proposal: k('Proposal', RotateCcw),
}

function record(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {}
}

/** A learning proposal's card on its Inbox row (`refs.card`), when the row was filed with one. */
export function learningCardOf(row: NeedsYouRow): Record<string, unknown> | null {
  if (row.origin !== 'note') return null
  const refs = record(row.note.refs)
  return refs.learning_proposal && refs.card && typeof refs.card === 'object' ? record(refs.card) : null
}

/** What kind of thing a Needs you row is, as its tag says it. */
export function kindOf(row: NeedsYouRow): KindWords {
  if (row.origin === 'waiting') {
    const b = record(row.item.brief)
    if (row.item.kind === 'loop_question' && 'merge' in b) return OTHER_KINDS.merge
    if (row.item.kind === 'loop_question' && 'conflict' in b) return OTHER_KINDS.conflict
    return WAITING_KINDS[row.item.kind] ?? OTHER_KINDS.proposal
  }
  if (row.origin === 'proposal') {
    const kind = (row.proposal as { kind?: string }).kind
    return SKILL_KINDS[kind === 'refine' ? 'refine' : 'new']
  }
  if (row.note.item_kind === 'user_note') return OTHER_KINDS.note
  const refs = record(row.note.refs)
  const card = learningCardOf(row)
  if (card) return LEARNING_KINDS[String(card.variant ?? '')] ?? OTHER_KINDS.proposal
  if (refs.session_organize) return OTHER_KINDS.chat_to_file
  if (refs.action_type && refs.rung) return OTHER_KINDS.autonomy
  if (refs.app && refs.proposal) return OTHER_KINDS.app_proposal
  return OTHER_KINDS.proposal
}
