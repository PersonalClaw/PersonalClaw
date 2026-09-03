import type { UiDoc } from './uiDoc'

// Doc object for StatusPill — the canonical tinted status pill. Encodes the
// tint+ink pairing rule (tokens.css AA audit note) and Tone-Not-Line as
// machine-readable Do/Don't so an app-building agent reaches for the
// primitive, not the ~90-site inline color-mix drift it replaces (audit AB-2).
const doc: UiDoc = {
  name: 'StatusPill',
  keywords: ['status', 'pill', 'chip', 'badge', 'tone', 'tint', 'verdict', 'state', 'ok', 'warn', 'danger', 'info'],
  description:
    'The one canonical tinted status pill — a rounded label whose ground is its own ink at a 16% color-mix tint (`background: color-mix(in srgb, <tone> 16%, transparent)` beside `color: <tone>`). Pages hand-rolled this exact pair ~90 times; the primitive carries it once, inside the 18% ink-contrast budget tokens.css documents and statusChipContrast.test.ts rails.',
  props: [
    { name: 'tone', description: "Semantic tone from the closed set: 'ok' | 'warn' | 'danger' | 'info' | 'primary' | 'neutral'. Picks BOTH the 16% tint ground and the ink, so the pair can never disagree — never recolor via className or style." },
    { name: 'pad', description: 'Emit the seed padding (px-1.5). Default true; set false when the pill genuinely needs other metrics and bring your own — cx is a plain joiner, so two padding utilities would race.' },
    { name: 'sized', description: 'Emit the seed type size (text-[0.75rem]). Default true; set false when the pill genuinely reads at another size and bring your own text utility — cx is a plain joiner, so two text-size utilities would race.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for StatusPill for every tinted verdict/state label; it carries the sanctioned tint strength and the closed tone vocabulary in one place.' },
    { guidance: true, description: "Give a pill whose visible text is not its full meaning an accessible name via the spread HTML attributes (role=\"img\" aria-label={fullSentence} title={fullSentence}) — the seed FitChip's shape." },
    { guidance: false, description: 'Do not hand-roll the color-mix tint inline; the statusTint ratchet holds the inline count down, and a hand-rolled percent can leave the audited 18% contrast budget.' },
    { guidance: false, description: 'Do not add borders or side stripes to carry the tone — the tint + ink pair IS the tone (Tone-Not-Line, sideStripeDoctrine).' },
  ],
  anatomy: ['span.inline-flex.shrink-0.items-center.rounded-pill.px-1.5', 'sized text-[0.75rem] (default) or consumer type utility', 'style: 16% color-mix tint ground + tone ink from the closed map', 'children (short label; full meaning via aria-label/title when longer)'],
}

export default doc
