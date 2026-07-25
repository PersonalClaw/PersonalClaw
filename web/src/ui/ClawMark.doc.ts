import type { UiDoc } from './uiDoc'

// Doc object for ClawMark — the brand silhouette painted with the active scheme's
// gradient. The "reads --grad-1..4 so it tracks the scheme automatically", the blob
// "liquid/alive thinking" halo, and reduced-motion respect were source comments.
const doc: UiDoc = {
  name: 'ClawMark',
  keywords: ['claw', 'logo', 'brand', 'mark', 'gradient', 'scheme', 'thinking', 'blob'],
  description:
    'The PersonalClaw brand mark — the claw silhouette painted with the ACTIVE scheme\'s gradient (reads --grad-1..4, re-tinted per scheme by the appearance store), so the logo tracks Coral/Jade/Lavender/etc. and any custom fork automatically. An optional `blob` halo wraps it in a soft scheme-tinted shape that slowly morphs its border-radius through organic keyframes for the "liquid / alive" thinking feel.',
  props: [
    { name: 'animated', description: 'Adds a slow ambient rotate wobble to the mark; suppressed under prefers-reduced-motion.' },
    { name: 'blob', description: 'Wraps the mark in a soft, scheme-tinted radial halo that morphs its border-radius through organic keyframes — for the large thinking indicator, not tiny inline lockups.' },
    { name: 'idGradient', description: "SVG gradient element id (default 'claw-grad'). Give co-existing instances distinct ids so their linearGradient defs never collide." },
    { name: 'size', description: 'Pixel width/height of the square mark (default 24); the blob padding scales with it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Let the mark read the --grad tokens — never hardcode a color family, so the logo re-tints with the active scheme and any custom fork automatically.' },
    { guidance: true, description: 'Pass a distinct idGradient when rendering multiple ClawMarks on one page, so their SVG gradient definitions do not collide by id.' },
    { guidance: true, description: 'Use blob only on the large thinking indicator (the living surround), not on small inline lockups where the halo would overwhelm the glyph.' },
    { guidance: false, description: 'Do not force the animation on — the morph and rotate go static under prefers-reduced-motion, and this is intentional.' },
  ],
  anatomy: ['motion.svg (claw path, scheme linearGradient def)', 'optional blob halo (scheme-tinted radial, morphing border-radius) wrapping the svg'],
}

export default doc
