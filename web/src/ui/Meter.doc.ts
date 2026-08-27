import type { UiDoc } from './uiDoc'

// Doc object for Meter — the flat linear meter for a measured LEVEL, as opposed to
// WavyProgress's in-flight task. Introduced by the loaded-models / memory-pressure
// surface (LOCAL-MODEL-MANAGER-V2 §7), where three hand-rolled bars already existed and a
// fourth would have been drift.
const doc: UiDoc = {
  name: 'Meter',
  keywords: ['meter', 'bar', 'gauge', 'memory', 'pressure', 'usage', 'level', 'quota', 'disk'],
  description:
    'A flat linear determinate bar for any quantity that is a plain fraction of a known total — memory in use, disk consumed, quota spent, an upload\'s bytes, exit criteria met, workflow nodes done. Use WavyProgress instead when the remaining time is UNKNOWN (a download with no content-length, an index rebuild with no total): that one has an indeterminate mode this deliberately lacks. Always renders role="progressbar" with a required accessible name and aria-valuenow, which is exactly why a page must not re-type the track by hand.',
  props: [
    { name: 'label', description: 'Accessible name — REQUIRED. A bare bar announces "progressbar, 63%" with no subject, which says nothing in a list of several meters. Name the thing being measured ("System memory in use"), not the act.' },
    { name: 'pct', description: 'The level as 0–100 (clamped). Rounded for aria-valuenow so assistive tech reads a whole percentage.' },
    { name: 'detail', description: 'Optional caption under the bar for the raw numbers ("12.4 GB of 16 GB"). Tabular-nums, so a live-updating value does not jitter horizontally.' },
    { name: 'tone', description: "Fill color as a design token (default 'var(--color-primary)'). The caller owns the threshold: whether a level is alarming depends on a configured warning percentage, which does not belong hardcoded in a primitive." },
    { name: 'size', description: "Track height: 'default' is the 6px bar this shipped with; 'thin' is 4px, for a bar riding inside a dense one-line row (an upload row, a task-card footer, a system tile) where 6px would crowd the text beside it. Nothing else changes between them — same radius, same track tone, same fill." },
    { name: 'className', description: 'Layout classes for the OUTER box only — how the meter sits in its parent (`flex-1`, `min-w-0`, `w-32`, `mb-2`). Deliberately does not reach the track, so a caller cannot restyle the bar itself and drift the primitive; height goes through `size`, colour through `tone`.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Name the meter for the quantity it measures — the percentage is announced separately, so the label supplies the subject.' },
    { guidance: true, description: 'Pass `tone` from your own threshold logic (e.g. the configured warn percentage) so one place decides what "high" means.' },
    { guidance: true, description: 'Use `size="thin"` for a bar sharing a one-line row with text, and keep the default 6px for a bar that is the row.' },
    { guidance: false, description: 'Do not use Meter for work with no known total — reach for WavyProgress, whose indeterminate mode this deliberately lacks. A bar that invents a fill it cannot compute is lying about progress.' },
    { guidance: false, description: 'Do not pass a raw hex to tone — use a design token so the fill tracks the theme.' },
    { guidance: false, description: 'Do not hand-roll the track (`h-1 overflow-hidden rounded-pill bg-surface-high` + an `h-full` fill). It ships with no role and no aria-valuenow, so axe cannot even see that a progressbar is unnamed; `design/meterAdoption.test.ts` fails on a new one.' },
  ],
  anatomy: ['div.outer (className, flex column)', 'div.track (role=progressbar, aria-label, aria-valuenow; h-1 when size=thin, else h-1.5)', 'div.fill (width = pct%)', 'optional detail caption'],
}

export default doc
