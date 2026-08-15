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
    { name: 'label', description: 'Accessible name for a DETERMINATE bar — required whenever `value` is passed, and forbidden when it is not. The determinate path renders role="progressbar" with aria-valuenow, and without a name assistive tech announces a bare percentage with no subject ("progressbar, 42%"); in a list of models that does not say WHICH is downloading. The indeterminate path takes no label because it is aria-hidden on purpose — the caller\'s own text already reports the state.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Name a determinate bar for the THING it is measuring, not for the act ("Downloading llama3", not "Progress") — the percentage is announced separately, so the label supplies the subject.' },
    { guidance: false, description: 'Do not give the indeterminate mode a label or a role. It is aria-hidden deliberately: the caller renders its own status text beside it, and a valueless progressbar would announce nothing useful twice.' },
    { guidance: true, description: 'Pass value (0–1) when the total is known for a determinate fill; omit it entirely for indeterminate — the two modes render different markup (determinate adds the progressbar ARIA + faint track).' },
    { guidance: true, description: 'Reach for WavyProgress instead of a flat bar for loading/download progress — it is the accent-gradient replacement for the old M2 progress bar.' },
    { guidance: false, description: 'Do not pass a raw hex to color — use a design token (e.g. var(--color-primary)) so the wave tracks the theme.' },
  ],
  anatomy: ['svg', 'indeterminate: single motion.path (traveling crest)', 'determinate: faint full-width track path + overlaid motion.path filled to value'],
}

export default doc
