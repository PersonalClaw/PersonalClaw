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

/** Tiny muted badge marking a field the backend can't persist yet. */
export function SoonTag() {
  return <span className="rounded-pill px-1.5 py-0.5 text-[0.625rem] uppercase tracking-wide bg-surface-high text-on-surface-low" title="Designed ahead of the backend — not saved yet">soon</span>
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

/** Due-date relative label + urgency tone (overdue→danger, soon→warn). */
export function dueMeta(due?: string): { label: string; tone: string } | null {
  if (!due) return null
  const t = Date.parse(due)
  if (Number.isNaN(t)) return { label: due, tone: 'var(--color-on-surface-low)' }
  const days = Math.ceil((t - Date.now()) / 86400000)
  if (days < 0) return { label: `${-days}d overdue`, tone: 'var(--color-danger)' }
  if (days === 0) return { label: 'Due today', tone: 'var(--color-warn)' }
  if (days === 1) return { label: 'Due tomorrow', tone: 'var(--color-warn)' }
  if (days <= 7) return { label: `Due in ${days}d`, tone: 'var(--color-on-surface-var)' }
  return { label: new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }), tone: 'var(--color-on-surface-low)' }
}
