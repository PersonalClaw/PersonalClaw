// Shared loop-status presentation meta — label + token-driven tone per unified
// loop status. Single source of truth for every surface that shows a loop's
// status (the Loops list, the dashboard Active Work widget, …), so the color
// language stays consistent app-wide.
export const LOOP_STATUS: Record<string, { label: string; tone: string }> = {
  intake: { label: 'Intake', tone: 'var(--color-on-surface-low)' },
  planning: { label: 'Planning', tone: 'var(--color-on-surface-low)' },
  review: { label: 'Review', tone: 'var(--color-info)' },
  ready: { label: 'Ready', tone: 'var(--color-on-surface-low)' },
  running: { label: 'Running', tone: 'var(--color-ok)' },
  paused: { label: 'Paused', tone: 'var(--color-warn)' },
  stagnant: { label: 'Stagnant', tone: 'var(--color-warn)' },
  needs_input: { label: 'Needs input', tone: 'var(--color-info)' },
  complete: { label: 'Completed', tone: 'var(--color-primary)' },
  failed: { label: 'Failed', tone: 'var(--color-danger)' },
  stopped: { label: 'Stopped', tone: 'var(--color-on-surface-low)' },
  // Synthetic: a 'complete' loop carrying an error_message finished non-genuinely
  // (cycle budget ran out before its Definition of Done) — show the honest warn-toned
  // "Ended early" rather than a celebratory "Completed". Matches the Code surfaces.
  ended_early: { label: 'Ended early', tone: 'var(--color-warn)' },
}

/** Fallback-safe lookup for an unknown status string. */
export function loopStatusMeta(status: string): { label: string; tone: string } {
  return LOOP_STATUS[status] ?? { label: status, tone: 'var(--color-on-surface-low)' }
}
