import { Sparkles, BookOpen, Workflow, GitPullRequest, Trash2, ArrowUpDown, FileText, HelpCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { LearningRow, StagingDay } from '../../lib/api'

// ── Proposal kinds ──
// Every kind the backend serves. Labelled here because names like "lesson_batch", "tier_migration"
// and "project_file" are internal ones a reviewer should never have to decode, and an unknown kind
// falls back to its raw id rather than an empty chip — a row whose kind cannot be named is still a
// row that needs deciding. The three project_* kinds are the project-context review's output;
// knowledge_draft is a gap-healing or schema-edit draft awaiting review (KNOWLEDGE-SYNTHESIS §3.4).
const KIND_META: Record<string, { label: string; icon: LucideIcon }> = {
  skill: { label: 'Skill', icon: Sparkles },
  lesson_batch: { label: 'Lessons', icon: BookOpen },
  template: { label: 'Template', icon: Workflow },
  template_diff: { label: 'Template edit', icon: GitPullRequest },
  retirement: { label: 'Retirement', icon: Trash2 },
  tier_migration: { label: 'Tier change', icon: ArrowUpDown },
  project_instruction: { label: 'Project instruction', icon: BookOpen },
  project_file: { label: 'Project file', icon: FileText },
  project_skill: { label: 'Project skill', icon: Sparkles },
  knowledge_draft: { label: 'Knowledge draft', icon: BookOpen },
}

export function kindLabel(kind: string): string {
  return KIND_META[kind]?.label ?? kind
}
export function kindIcon(kind: string): LucideIcon {
  return KIND_META[kind]?.icon ?? HelpCircle
}

// ── Risk tiers ──
// Metadata for ordering and filtering, NEVER an auto-apply lane (§3.1: any "auto" tier is
// guardrail-violating). The tones deliberately escalate: `manual_only` reads as the one to stop at.
export const TIER_TONE: Record<string, string> = {
  low: 'var(--color-on-surface-var)',
  review: 'var(--color-info)',
  manual_only: 'var(--color-warn)',
}
export const TIER_LABEL: Record<string, string> = {
  low: 'Low risk',
  review: 'Review',
  manual_only: 'Manual only',
}
export function tierTone(tier: string): string {
  // An UNSCORED tier gets the warn tone, matching the backend's sort: nobody judged its risk, which
  // is more urgent than a judged destructive edit, not less.
  return TIER_TONE[tier] ?? 'var(--color-warn)'
}
export function tierLabel(tier: string): string {
  return TIER_LABEL[tier] ?? 'Unscored'
}

/** Why a row cannot be accepted from a bulk control, or "" when it can.
 *
 *  Reads the backend's own `bulk_acceptable`/`renderable` flags rather than re-deriving the rule —
 *  two implementations of "safe to bulk-accept" would eventually disagree, and the FE would be the
 *  one shipping the permissive answer. This only EXPLAINS the flag. */
export function bulkBlockedReason(row: LearningRow): string {
  if (row.bulk_acceptable) return ''
  if (!row.renderable) return 'missing provenance — cannot be shown weighably'
  if (row.risk_tier === 'manual_only') return 'destructive edits are never bulk-accepted'
  if (!row.manifest_valid) return 'its change manifest is invalid'
  if (!row.evidence_refs.length) return 'no evidence to check'
  return 'not eligible for bulk accept'
}

// ── Evidence: how many refs, and WHAT KIND ──
// The queue stamps a tier at `enqueue` and the tiers are not interchangeable: an ablation is a
// paired on/off MEASUREMENT (EVALUATION-SUBSTRATE §3.1 files a retirement on one), a correlation is
// a co-occurrence. Rendering only the count made those identical on screen — and "retire this
// component" is exactly the decision where the difference decides the answer.
const EVIDENCE_GRADE: Record<string, string> = {
  ablation: 'measured on/off',
  causal: 'measured (controlled study)',
  correlated: 'correlated',
  anecdotal: 'anecdotal',
}

/** The evidence clause on a proposal row: how much, and of what kind.
 *
 *  An UNGRADED tier — "" from a record filed before the tier existed, or a name this build does not
 *  know — renders as `ungraded` rather than falling back to a grade. Substituting `correlated` would
 *  turn "nobody said" into a claim, the same failure as drawing an unmeasured mean as `0.000`. */
export function evidenceLabel(row: LearningRow): string {
  if (!row.evidence_refs.length) return 'no evidence'
  const grade = EVIDENCE_GRADE[row.evidence_strength] ?? 'ungraded'
  return `${row.evidence_refs.length} evidence ref(s) · ${grade}`
}

// ── The Loop-2 gate: before/after, or an honest "ungated" ──
// EVALUATION-SUBSTRATE amendment E2 (ES-6). The gate re-runs a cheap scenario subset over the home
// as it is and again with the candidate staged, so a reviewer sees whether the change made things
// worse BEFORE accepting it. Three facts have to survive to the screen and they are all separate:
//
//  * a proposal with NO gate run reads `ungated` + the backend's reason — never a blank cell, and
//    never a 0. It stays fully acceptable: a gate that blocked on its own absence would stop a user
//    shipping a change because the GATE broke.
//  * a gated proposal whose arms produced no score reads `not measured`, the same string
//    JudgeBenchPanel / AblationPanel / StudiesPanel already use for a null mean. One vocabulary for
//    "the number does not exist", not a second one per surface.
//  * a measured DROP is called out, because that is the whole point of the columns.

/** Format one gate score. `null` is "not measured" — the house string for an absent number. */
export function gateScore(value: number | null): string {
  return value === null ? 'not measured' : value.toFixed(3)
}

/** The gate clause on a proposal row: before → after, or why there is no pair.
 *
 *  A missing `gate` object (an older cached row) is treated as ungated rather than crashing or
 *  rendering nothing: an absent measurement and an absent FIELD mean the same thing to a reviewer,
 *  and neither is evidence of a passing gate. */
export function gateLabel(row: LearningRow): string {
  const gate = row.gate
  if (!gate || gate.state !== 'gated') {
    const reason = gate?.reason ? ` — ${gate.reason}` : ''
    return `ungated${reason}`
  }
  const pair = `${gateScore(gate.before)} → ${gateScore(gate.after)}`
  const delta = gate.delta === null
    ? ''
    : ` (${gate.delta > 0 ? '+' : ''}${gate.delta.toFixed(3)})`
  const scope = ` over ${gate.scenarios} gate scenario(s)`
  const halted = gate.halted ? ' · stopped early on the eval budget' : ''
  return `gate ${pair}${delta}${scope}${halted}`
}

/** Whether the row should shout. A measured drop only — a tie is not a regression, and an
 *  UNMEASURED pair is not one either: flagging one would be the same dishonesty as drawing an
 *  unmeasured mean as 0, just pointing the other way. */
export function gateRegressed(row: LearningRow): boolean {
  return row.gate?.state === 'gated' && row.gate.delta !== null && row.gate.delta < 0
}

// ── The staging week panel ──
/** How a day should render. `silent` is the alarming one: no passes at all means capture did not run,
 *  and an aggregate view cannot see it — which is the whole reason this panel exists. */
export type DayState = 'silent' | 'error' | 'produced' | 'ok'

export function dayState(day: StagingDay): DayState {
  if (day.passes === 0) return 'silent'
  if (day.errors > 0) return 'error'
  if (day.produced > 0) return 'produced'
  return 'ok'
}

export const DAY_TONE: Record<DayState, string> = {
  silent: 'var(--color-warn)',
  error: 'var(--color-danger)',
  produced: 'var(--color-primary)',
  ok: 'var(--color-on-surface-low)',
}

export const DAY_HINT: Record<DayState, string> = {
  silent: 'No capture pass ran — this is the gap an aggregate view cannot see',
  error: 'A capture pass errored',
  produced: 'Produced proposals',
  ok: 'Ran, produced nothing',
}

/** A short weekday label for a `YYYY-MM-DD` bucket. Parsed as LOCAL time (the backend buckets by
 *  local date), because `new Date('2024-01-01')` is parsed as UTC and would shift the label a day
 *  west of the reader. */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  if (!y || !m || !d) return day
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short' })
}
