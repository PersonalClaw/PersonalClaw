// Derivations for the loaded-models / memory-pressure surface, Settings › Models'
// "On this machine" section: ordering, tone and wording, kept apart from the panel so a
// second reader would share them rather than re-derive them.
import type { LoadedModel, MemoryPressure } from './api'

/** The fill token for a pressure level, against the SERVER's configured threshold.
 *
 *  The threshold is a config value (`local_models.pressure_warn_pct`), so the comparison
 *  belongs with the data, not hardcoded in a component. `source: 'unavailable'` is a
 *  distinct third state: an unreadable host gets the neutral tone, never a warning colour
 *  on numbers nobody measured.
 */
export function pressureTone(p: MemoryPressure): string {
  if (p.source === 'unavailable') return 'var(--color-outline-variant)'
  if (p.warn) return 'var(--color-danger)'
  if (p.used_pct >= p.warn_pct * 0.85) return 'var(--color-warning)'
  // Normal use is information, not the agent's voice: coral is kept for the agent, the primary
  // action, focus and selection.
  return 'var(--color-info)'
}

/** Human caption under the bar — or an honest "unknown" when nothing could be measured.
 *
 *  Every `_mb` on this wire is MiB (the host probe reads kB and divides by 1024; a sidecar's RSS
 *  is bytes over 1024²), so the captions say GiB and MiB: the unit the model sizes and the
 *  memory reserve beside them are stated in. */
export function pressureDetail(p: MemoryPressure): string {
  if (p.source === 'unavailable' || p.total_mb <= 0) return 'System memory unavailable on this host'
  const gib = (mb: number) => `${(mb / 1024).toFixed(1)} GiB`
  return `${gib(p.used_mb)} of ${gib(p.total_mb)} in use · ${p.used_pct}%`
}

/** Resident models, reclaimable ones FIRST, then heaviest.
 *
 *  A model still in RAM after its binding moved is the row the user can act on, so it
 *  leads. Within a group, a known RSS outranks an unknown one — an in-process model has no
 *  attributable RSS at all, so it sorts last rather than pretending to be 0 MB.
 */
export function sortOccupants(rows: LoadedModel[]): LoadedModel[] {
  return [...rows].sort((a, b) => {
    if (a.is_active !== b.is_active) return a.is_active ? 1 : -1
    return (b.rss_mb ?? -1) - (a.rss_mb ?? -1)
  })
}

/** How many resident models are no longer bound to anything (the reclaimable count). */
export function reclaimableCount(rows: LoadedModel[]): number {
  return rows.filter((r) => !r.is_active).length
}

/** One row's secondary line: process kind, RSS when the child reported one, attribution. */
export function occupantDetail(row: LoadedModel): string {
  const parts: string[] = [row.kind]
  if (row.rss_mb != null && row.rss_mb > 0) parts.push(`${Math.round(row.rss_mb)} MiB`)
  if (row.generation != null && row.generation > 0) parts.push(`gen ${row.generation}`)
  if (!row.is_active) parts.push('not bound')
  return parts.join(' · ')
}
