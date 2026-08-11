import type { UiDoc } from './uiDoc'

// Doc object for ProgressRing — the circular cycle-progress arc extracted from two
// byte-identical page-local copies (dashboard ActiveWork + loops LoopsListPage). The
// "one animated, one jumped" divergence and the initial={false} mount contract were
// source comments; they are the reason this primitive exists.
const doc: UiDoc = {
  name: 'ProgressRing',
  keywords: ['progress', 'ring', 'arc', 'circular', 'cycle', 'loop', 'svg', 'donut', 'gauge'],
  description:
    'A circular progress arc for loop cycle progress — a 28px-by-default SVG ring with a surface-high track and a status-toned arc that grows clockwise from 12 o\'clock. The arc SPRING-ANIMATES between values (so a completing cycle reads as progress advancing rather than a value being swapped) but is silent on mount, and snaps instead of easing when the user has asked for reduced motion.',
  props: [
    { name: 'pct', description: 'Progress as a FRACTION in 0..1 — not 0..100. Callers clamp with Math.min(1, …) so a loop that overran its cycle budget reads full instead of winding a second time round.' },
    { name: 'tone', description: 'Stroke color for the filled arc — pass the status tone (a CSS var or color string) so the ring agrees with the status label beside it. The track is always surface-high.' },
    { name: 'size', description: 'Outer diameter in px (default 28). The 2.5 stroke width is fixed, so much smaller sizes read as proportionally heavier.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass a 0..1 fraction and clamp it at the call site; the ring does not clamp for you, and a value above 1 winds the arc past full.' },
    { guidance: true, description: 'Pass the same tone the adjacent status label uses, so the ring and the words agree about what state the loop is in.' },
    { guidance: true, description: 'Use it for a value that CHANGES while visible (cycle progress). The spring is what communicates advancement; for a static ratio a bar or plain text is calmer.' },
    { guidance: false, description: 'Do not reimplement this ring locally to change the animation or the geometry — two page-local copies already diverged that way, one animating and one jumping, and that is why this primitive exists.' },
    { guidance: false, description: 'Do not use it as a busy/indeterminate spinner. It always shows a definite fraction; a loop with no cycle budget should render a StatusDot instead.' },
  ],
  anatomy: ['svg (size × size, shrink-0)', 'track circle (surface-high, 2.5 stroke)', 'animated arc circle (tone, round cap, rotated -90°)'],
}

export default doc
