import type { UiDoc } from './uiDoc'

// Doc object for WavyProgress — the animated sine-wave progress indicator that
// replaces the flat M2 bar. The determinate/indeterminate split (value present vs
// absent) and its use by the model-download manager were source comments.
const doc: UiDoc = {
  name: 'WavyProgress',
  keywords: ['progress', 'wave', 'wavy', 'bar', 'loading', 'download', 'indeterminate', 'determinate'],
  description:
    'The wavy progress indicator — an animated sine wave in the accent gradient that replaces the flat M2 progress bar. Indeterminate by default (the wave crest travels); pass `value` (0–1) for a determinate bar drawn as a faint full-width track with the filled portion overlaid. Used by the bundled-model download manager, where the byte total may be known or absent.',
  props: [
    { name: 'color', description: "Stroke color of the wave (default 'var(--color-primary)') — pass a design token, not a raw hex." },
    { name: 'value', description: 'Progress 0–1 for a DETERMINATE bar (clamped); omit it (undefined) for the INDETERMINATE traveling-crest animation.' },
    { name: 'width', description: 'Pixel width of the SVG (default 120); the wave path scales to it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass value (0–1) when the total is known for a determinate fill; omit it entirely for indeterminate — the two modes render different markup (determinate adds the progressbar ARIA + faint track).' },
    { guidance: true, description: 'Reach for WavyProgress instead of a flat bar for loading/download progress — it is the accent-gradient replacement for the old M2 progress bar.' },
    { guidance: false, description: 'Do not pass a raw hex to color — use a design token (e.g. var(--color-primary)) so the wave tracks the theme.' },
  ],
  anatomy: ['svg', 'indeterminate: single motion.path (traveling crest)', 'determinate: faint full-width track path + overlaid motion.path filled to value'],
}

export default doc
