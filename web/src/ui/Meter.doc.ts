import type { UiDoc } from './uiDoc'

// Doc object for Meter — the flat linear meter for a measured LEVEL, as opposed to
// WavyProgress's in-flight task. Introduced by the loaded-models / memory-pressure
// surface (LOCAL-MODEL-MANAGER-V2 §7), where three hand-rolled bars already existed and a
// fourth would have been drift.
const doc: UiDoc = {
  name: 'Meter',
  keywords: ['meter', 'bar', 'gauge', 'memory', 'pressure', 'usage', 'level', 'quota', 'disk'],
  description:
    'A flat linear meter reporting a MEASURED level that is true right now — memory in use, disk consumed, quota spent. Use WavyProgress instead for a task in flight (a download, an index rebuild): that one animates and can be indeterminate, this one cannot. Always renders role="progressbar" with a required accessible name and aria-valuenow.',
  props: [
    { name: 'label', description: 'Accessible name — REQUIRED. A bare bar announces "progressbar, 63%" with no subject, which says nothing in a list of several meters. Name the thing being measured ("System memory in use"), not the act.' },
    { name: 'pct', description: 'The level as 0–100 (clamped). Rounded for aria-valuenow so assistive tech reads a whole percentage.' },
    { name: 'detail', description: 'Optional caption under the bar for the raw numbers ("12.4 GB of 16 GB"). Tabular-nums, so a live-updating value does not jitter horizontally.' },
    { name: 'tone', description: "Fill color as a design token (default 'var(--color-primary)'). The caller owns the threshold: whether a level is alarming depends on a configured warning percentage, which does not belong hardcoded in a primitive." },
  ],
  bestPractices: [
    { guidance: true, description: 'Name the meter for the quantity it measures — the percentage is announced separately, so the label supplies the subject.' },
    { guidance: true, description: 'Pass `tone` from your own threshold logic (e.g. the configured warn percentage) so one place decides what "high" means.' },
    { guidance: false, description: 'Do not use Meter for a download or any task in progress — reach for WavyProgress, which has an indeterminate mode this deliberately lacks.' },
    { guidance: false, description: 'Do not pass a raw hex to tone — use a design token so the fill tracks the theme.' },
  ],
  anatomy: ['div.track (role=progressbar, aria-label, aria-valuenow)', 'div.fill (width = pct%)', 'optional detail caption'],
}

export default doc
