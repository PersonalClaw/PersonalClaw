import { Circle, CircleDot, CircleSlash, CheckCircle2, XCircle, ListChecks } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ExitCriterion } from '../../lib/api'

/** Re-export so list/empty states share one task glyph. */
export const ListChecksLike = ListChecks

/** One source of truth for "is this exit criterion complete": prefer the typed
 *  `status`, fall back to the legacy `met` bool when status is absent. Every view
 *  (list/cards/board/detail) must agree, so a criterion set via the API to
 *  status=complete (no `met`) counts the same everywhere. */
export const isExitComplete = (e: ExitCriterion): boolean =>
  e.status === 'complete' || (e.status === undefined && !!e.met)
export const exitDoneCount = (items: ExitCriterion[] = []): number => items.filter(isExitComplete).length

/** Canonical task vocabulary. Status keys match the backend Task dataclass
 *  (open/in_progress/done/cancelled/blocked); labels use the richer
 *  TasksMultiServer phrasing. Priority adds critical/trivial rungs beyond the
 *  backend's low/medium/high (extra rungs may not persist yet). */
export interface StatusMeta { key: string; label: string; icon: LucideIcon; tone: string }

export const STATUSES: StatusMeta[] = [
  { key: 'open', label: 'Not started', icon: Circle, tone: 'var(--color-on-surface-low)' },
  { key: 'in_progress', label: 'In progress', icon: CircleDot, tone: 'var(--color-info)' },
  { key: 'blocked', label: 'Blocked', icon: CircleSlash, tone: 'var(--color-warn)' },
  { key: 'done', label: 'Completed', icon: CheckCircle2, tone: 'var(--color-ok)' },
  { key: 'cancelled', label: 'Cancelled', icon: XCircle, tone: 'var(--color-on-surface-low)' },
]
const STATUS_MAP = Object.fromEntries(STATUSES.map((s) => [s.key, s]))
export const statusMeta = (k?: string): StatusMeta => STATUS_MAP[k ?? ''] ?? { key: k ?? '', label: k ?? 'Unknown', icon: Circle, tone: 'var(--color-on-surface-low)' }
export const TERMINAL = new Set(['done', 'cancelled'])

/** Why a blocked task is blocked, from the backend's `blocked_reason_kind` ("" | "auto" | "manual").
 *
 *  The two kinds are NOT cosmetic variants — they behave differently and imply different next steps:
 *
 *    auto    an unfinished prerequisite the reconciler tracks. It clears ITSELF the moment the
 *            prerequisite reaches a terminal status; the user does nothing.
 *    manual  a person blocked this for a reason outside the graph. `reconcile_blocked_status`
 *            explicitly `continue`s on it ("never auto-touch a manual block"), so it will sit there
 *            until someone unblocks it by hand.
 *
 *  And the surfaces had no way to tell them apart, because `block_reason` is derived purely from
 *  prerequisites: a MANUAL block has none, so it reports `is_blocked: false` with an empty
 *  `message`, and every panel gated on that flag rendered nothing at all. A task read "Blocked" with
 *  no explanation anywhere and no hint that only a human could clear it.
 *
 *  Returns null for a task that is not blocked, or whose kind the backend left empty — the caller
 *  then falls back to `block_reason`, which is right for a legacy payload with no kind stamped. */
export function blockKindMeta(kind?: string): { label: string; hint: string } | null {
  if (kind === 'auto') {
    return {
      label: 'Waiting on a prerequisite',
      hint: 'Unblocks itself when the task it depends on is done or cancelled.',
    }
  }
  if (kind === 'manual') {
    return {
      label: 'Blocked by you',
      hint: 'Not waiting on any tracked task — it stays blocked until you unblock it.',
    }
  }
  return null
}

export interface PriorityMeta { key: string; label: string; tone: string }
export const PRIORITIES: PriorityMeta[] = [
  { key: 'critical', label: 'Critical', tone: 'var(--color-danger)' },
  { key: 'high', label: 'High', tone: 'var(--color-warn)' },
  { key: 'medium', label: 'Medium', tone: 'var(--color-info)' },
  { key: 'low', label: 'Low', tone: 'var(--color-on-surface-low)' },
  { key: 'trivial', label: 'Trivial', tone: 'var(--color-on-surface-low)' },
]
const PRIORITY_MAP = Object.fromEntries(PRIORITIES.map((p) => [p.key, p]))
export const priorityMeta = (k?: string): PriorityMeta => PRIORITY_MAP[k ?? ''] ?? { key: k ?? '', label: k ?? '—', tone: 'var(--color-on-surface-low)' }
// (the backend persists any priority string verbatim — all rungs save, no gating)

/** The priority to SHOW on a browsing surface, or null when it carries no signal.
 *
 *  `medium` is the default: `models.py` declares `priority: TaskPriority = MEDIUM` and normalizes a
 *  missing value with `d.get("priority", "medium")`. So a medium task is **indistinguishable from
 *  one whose priority was never set** — rendering "Medium" asserts an intent that may not exist.
 *  Measured on the validation home: **28 of 30 tasks are medium**, so the chip appeared on 93% of
 *  rows in a semantic colour while telling the user nothing, and the two `high` tasks it exists to
 *  surface did not stand out at all.
 *
 *  This is the rule the file next door already applies to the assignee — "on a single-user install
 *  every task is the owner's, and '@you' on every row is noise" (`TasksListPage`'s `MetaLine`).
 *  Priority simply never got it.
 *
 *  Every EXPLICIT rung still shows, including `low` and `trivial`: deliberately deprioritising
 *  something is a real signal, and so is any unrecognised string the backend kept verbatim. Only
 *  the default is silent.
 *
 *  Detail views should keep using {@link priorityMeta} — a field's current value belongs in the
 *  editor that sets it, where a blank would read as "unset" rather than "medium". */
export const signalPriority = (k?: string): PriorityMeta | null =>
  !k || k === 'medium' ? null : priorityMeta(k)

/** Tiny muted badge marking a field the backend can't persist yet. */
export function SoonTag() {
  return <span className="rounded-pill px-1.5 py-0.5 text-[0.75rem] uppercase tracking-wide bg-surface-high text-on-surface-low" title="Designed ahead of the backend — not saved yet">soon</span>
}

export function relTime(iso?: string): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = (Date.now() - t) / 1000
  if (s < 0) return new Date(t).toLocaleDateString()
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`
  return new Date(t).toLocaleDateString()
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/
/** `due` is a date-only string (`2026-10-26`) — and `Date.parse` reads that shape as UTC
 *  midnight per spec, so west of UTC it renders as the PREVIOUS day. Build local midnight
 *  ourselves; full timestamps (with a time part or trailing Z) are already unambiguous and
 *  pass through to Date.parse. Returns NaN for anything unparseable. */
export function parseDueDate(due: string): number {
  const m = DATE_ONLY.exec(due.trim())
  if (!m) return Date.parse(due)
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const local = new Date(y, mo - 1, d)
  return local.getMonth() === mo - 1 && local.getDate() === d ? local.getTime() : NaN
}

const localMidnight = (t: number) => { const d = new Date(t); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() }
/** Whole calendar days from today to `t` in LOCAL time, so "Due today" holds for the whole
 *  local day instead of flipping at whatever hour UTC midnight lands on. */
const dueDayDelta = (t: number) => Math.round((localMidnight(t) - localMidnight(Date.now())) / 86400000)

/** Due-date relative label + urgency tone (overdue→danger, soon→warn). */
export function dueMeta(due?: string): { label: string; tone: string } | null {
  if (!due) return null
  const t = parseDueDate(due)
  if (Number.isNaN(t)) return { label: due, tone: 'var(--color-on-surface-low)' }
  const days = dueDayDelta(t)
  if (days < 0) return { label: `${-days}d overdue`, tone: 'var(--color-danger)' }
  if (days === 0) return { label: 'Due today', tone: 'var(--color-warn)' }
  if (days === 1) return { label: 'Due tomorrow', tone: 'var(--color-warn)' }
  if (days <= 7) return { label: `Due in ${days}d`, tone: 'var(--color-on-surface-var)' }
  return { label: new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), tone: 'var(--color-on-surface-low)' }
}
