import { Ban, CircleDashed, Clock, Eye, Layers, Sparkles, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { WorkflowSurfacingFinding, WorkflowSurfacingRow } from '../../lib/api'

/** Presentation for the surfacing state of a template (TASKS-SOPS §7 R15/R8/R18/R19).
 *
 *  Centralized for the same reason `workflowMeta` is: the templates list, a composer chip and a
 *  def detail view must render the SAME tone and label for a given state. Three components each
 *  choosing their own colour is how an "overdue" template looks urgent in one place and calm in
 *  another.
 *
 *  All pure. The backend already decided the state (`GET /api/workflows/surfacing` computes
 *  freshness, overdue and the doctor findings from `sort_key`/`freshness`/`doctor`) — recomputing
 *  any of it here would be a second implementation of a rule that already has an owner, and the
 *  two would disagree the first time a threshold moved. */

export interface SurfacingLook { label: string; icon: LucideIcon; tone: string; hint: string }

const FRESHNESS_LOOK: Record<WorkflowSurfacingRow['freshness'], SurfacingLook> = {
  // `never_run` is its own band, not "infinitely overdue": a checklist authored yesterday has not
  // failed to run, and showing it as maximally stale on day one trains a user to ignore the column.
  never_run: { label: 'Never run', icon: CircleDashed, tone: 'text-on-surface-low', hint: 'No completed run yet' },
  fresh: { label: 'Fresh', icon: Clock, tone: 'text-success', hint: 'Within its cadence' },
  due_soon: { label: 'Due soon', icon: Clock, tone: 'text-on-surface', hint: 'Approaching its cadence' },
  overdue: { label: 'Overdue', icon: TriangleAlert, tone: 'text-warning', hint: 'Past its cadence' },
  // Two bands rather than one, because a def three weeks past a weekly cadence is a different
  // conversation from one a day late.
  stale: { label: 'Stale', icon: TriangleAlert, tone: 'text-danger', hint: 'Far past its cadence' },
}

export function freshnessLook(freshness: string): SurfacingLook {
  return (
    FRESHNESS_LOOK[freshness as WorkflowSurfacingRow['freshness']] ?? {
      label: freshness || 'Unknown',
      icon: CircleDashed,
      tone: 'text-on-surface-low',
      hint: '',
    }
  )
}

const MODE_LOOK: Record<WorkflowSurfacingRow['surface_mode'], SurfacingLook> = {
  off: { label: 'Off', icon: Ban, tone: 'text-on-surface-low', hint: 'Never surfaces on its own — start it explicitly' },
  passive: { label: 'Guidance', icon: Eye, tone: 'text-on-surface', hint: 'Injects its guidance; proposes nothing' },
  suggest: { label: 'Suggests', icon: Sparkles, tone: 'text-on-surface', hint: 'May propose running itself' },
}

export function modeLook(mode: string): SurfacingLook {
  return MODE_LOOK[mode as WorkflowSurfacingRow['surface_mode']] ?? MODE_LOOK.off
}

/** Whether a row should show a cadence column at all.
 *
 *  `cadence_days: 0` means the author did not ask to be nagged — the same reading `ttl: 0` gets in
 *  the confirmation record. Rendering "Fresh" for a def with no cadence would imply a schedule it
 *  does not have. */
export function tracksCadence(row: Pick<WorkflowSurfacingRow, 'cadence_days'>): boolean {
  return row.cadence_days > 0
}

/** The cadence line for a row, or '' when it has none. */
export function cadenceLabel(row: Pick<WorkflowSurfacingRow, 'cadence_days' | 'escalation'>): string {
  if (row.cadence_days <= 0) return ''
  const every = row.cadence_days === 1 ? 'Every day' : `Every ${row.cadence_days} days`
  // Auto escalation puts a task on the user's board, which is a materially different promise from
  // "appears higher in this list" — so it is named, not implied by the cadence alone.
  return row.escalation === 'auto' ? `${every} · files a task when overdue` : every
}

/** Which chip a def gets in the composer, or null when it must not appear.
 *
 *  An `off` def has no chip: the chip's entire purpose is to show the user WHAT the matcher
 *  injected and let them switch it off, and a def that injects nothing has nothing to show. A chip
 *  for it would be an affordance with no referent. */
export function composerChip(
  row: Pick<WorkflowSurfacingRow, 'name' | 'surface_mode' | 'summary'>,
): { label: string; runnable: boolean; preview: string } | null {
  if (row.surface_mode === 'off') return null
  return {
    label: `SOP: ${row.name}`,
    // Only `suggest` gets a run affordance. A passive def surfaces guidance and proposes running
    // nothing, which is the whole reason the two modes are separate.
    runnable: row.surface_mode === 'suggest',
    preview: row.summary || '',
  }
}

/** Group doctor findings by def name, for rendering beside the row they belong to.
 *
 *  Findings arrive as a flat list because one def can have several (unreachable AND shadowed AND
 *  requirements-unmet are three different fixes). A list rendered separately from the rows would
 *  make the reader match names by eye. */
export function findingsByDef(findings: WorkflowSurfacingFinding[]): Record<string, WorkflowSurfacingFinding[]> {
  const out: Record<string, WorkflowSurfacingFinding[]> = {}
  for (const finding of findings ?? []) {
    const key = finding?.name ?? ''
    if (!key) continue
    ;(out[key] ??= []).push(finding)
  }
  return out
}

/** The pack chips for a row. A def in no pack shows none — an empty "Packs:" label reads as a
 *  missing value rather than an absent concept. */
export function packChips(row: Pick<WorkflowSurfacingRow, 'packs'>): string[] {
  return (row.packs ?? []).filter(Boolean)
}

export const PACK_ICON: LucideIcon = Layers

/** Whether this row needs the user's attention in the list.
 *
 *  Reads the backend's `overdue` flag rather than recomputing it from `cadence_days` and
 *  `last_completed_at`: the thresholds (`DUE_SOON_AT`, `STALE_MULTIPLE`) live in one place, and a
 *  second comparison here would drift the first time one of them moved. */
export function needsAttention(row: Pick<WorkflowSurfacingRow, 'overdue'>, findingCount: number): boolean {
  return row.overdue || findingCount > 0
}
