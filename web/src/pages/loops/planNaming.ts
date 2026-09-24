/** Plan naming with deterministic fallbacks (UNIVERSAL-PLANNING UP-R7, WF2UNI-10).
 *
 *  The planner runs ONE small-model naming call (the `background` use-case, server-side) that
 *  returns `{title, description, per-step labels}` and streams them alongside the plan. Two
 *  things must hold, and both are pure logic the view should not carry:
 *
 *  1. **The model output is authoritative when present.** A streamed title/label wins over the
 *     fallback — the whole reason for the call is a name a human reads better than a slug.
 *  2. **No model is a supported state, not an error.** When the naming call is unavailable
 *     (offline, degraded, or simply not yet arrived on the stream), a deterministic name is
 *     derived from the plan's own structure so the review NEVER shows an unnamed plan or a step
 *     labeled by a raw id. This is the provider-agnostic floor: usable names with zero model.
 *
 *  Kept pure + unit-tested (`planNaming.test.ts`) precisely so the no-model floor is asserted
 *  without a model in the loop. Per-FIELD merge (not all-or-nothing): a model that named the
 *  plan but not step 3 still gets a deterministic label for step 3. */

import type { PlanDraft, PlanStep } from './planStream'

/** What the small-model naming call streams. Every field optional — a partial arrival (title in,
 *  labels still coming) is normal, and each missing field falls back independently. */
export interface PlanNames {
  title?: string
  description?: string
  /** step id → label. */
  labels?: Record<string, string>
}

export interface NamedPlan {
  title: string
  description: string
  /** step id → resolved label (model value or deterministic fallback). */
  labels: Record<string, string>
}

/** Resolve the final names for a plan: model output where present, deterministic fallback where
 *  not. `goal` is the loop's own goal text — the best deterministic title source when the plan
 *  itself carries none. */
export function resolvePlanNames(
  draft: PlanDraft,
  names: PlanNames | null | undefined,
  goal = '',
): NamedPlan {
  const model = names ?? {}
  const title = firstNonEmpty(model.title, draft.title, fallbackTitle(draft, goal))
  const description = firstNonEmpty(
    model.description,
    draft.description,
    fallbackDescription(draft),
  )
  const labels: Record<string, string> = {}
  for (const step of draft.steps) {
    labels[step.id] = firstNonEmpty(model.labels?.[step.id], step.label, fallbackLabel(step))
  }
  return { title, description, labels }
}

/** A usable plan title with no model: the goal's first line (trimmed to a headline length), else
 *  a phrase built from the step count. Never empty. */
export function fallbackTitle(draft: PlanDraft, goal = ''): string {
  const firstLine = goal.split('\n').map((l) => l.trim()).find(Boolean)
  if (firstLine) return truncate(stripTrailingPunct(firstLine), 60)
  const n = draft.steps.length
  return n ? `Plan · ${n} step${n === 1 ? '' : 's'}` : 'Plan'
}

/** A usable description with no model: a sentence naming the ordered step labels — enough for a
 *  reviewer to see the shape of the plan without the model's prose. */
export function fallbackDescription(draft: PlanDraft): string {
  const names = draft.steps.map(fallbackLabel).filter(Boolean)
  if (!names.length) return 'No steps yet.'
  if (names.length === 1) return `A single step: ${names[0]}.`
  const head = names.slice(0, -1).join(', ')
  return `${names.length} steps: ${head}, then ${names[names.length - 1]}.`
}

/** A usable per-step label with no model: the step's own label/role/kind/target, else a
 *  humanized form of its id (so a raw `step_2` never surfaces). */
export function fallbackLabel(step: PlanStep): string {
  const direct = firstNonEmpty(step.label, step.role, step.target, humanizeKind(step.kind))
  if (direct) return truncate(direct, 60)
  return humanizeId(step.id)
}

function humanizeKind(kind?: string): string {
  return kind ? kind.replace(/[_-]+/g, ' ').trim() : ''
}

function humanizeId(id: string): string {
  const words = id.replace(/[_-]+/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : 'Step'
}

function firstNonEmpty(...vals: (string | undefined)[]): string {
  for (const v of vals) if (v && v.trim()) return v.trim()
  return ''
}

function stripTrailingPunct(s: string): string {
  return s.replace(/[.!?,;:]+$/, '')
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s
}
