import type { UiDoc } from './uiDoc'

// Doc object for DotGlow — the 3D halftone wave surface behind the composer. The
// "measures the composer live each frame" and "the light never meets an edge"
// contracts, plus reduced-motion respect, were source comments.
const doc: UiDoc = {
  name: 'DotGlow',
  keywords: ['glow', 'dots', 'wave', 'canvas', 'composer', 'ambient', 'halftone', 'decorative', 'background', 'halo'],
  description:
    'The "dot glow" — an animated 3D halftone wave surface on a canvas that reads as light cast by the composer onto a rippling ground plane behind it. Decorative background chrome: it measures the composer element LIVE each frame so the illumination tracks it in perfect sync, reads its tint/shape/density params from the appearance runtime, and respects prefers-reduced-motion (static frame). The box it is mounted in is its stage: the halo fades to nothing before every edge of that box, over exactly the room between the composer and the edge, so it is never cut into a line.',
  props: [
    { name: 'className', description: 'Extra classes on the absolute-inset overlay container (tokens only).' },
    { name: 'composerRef', description: 'Ref to the composer element; the glow measures it live each frame so the light tracks the composer exactly as it spring-animates, with no separate easing. This light always stays lit.' },
    { name: 'intensity', description: 'Target glow strength (1 = rest, >1 = composer focused/lifted); lerped smoothly rather than snapped.' },
    { name: 'focused', description: 'The composer has the caret: the halo gathers a ~40px coral aura close around it (cross-fading over 200ms). It is painted here, inside the edge-faded layer, rather than as the composer\'s own shadow, because the page gutter the composer sits in would clip it into a line.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass composerRef so the glow measures the composer live each frame and tracks it in sync — do not feed it a separately-eased position.' },
    { guidance: true, description: 'Mount it in the box the light belongs to — the column the composer sits in — not an outer page wrapper that a side panel or the header shares: the halo fades out before its stage edges, so the stage must be the region it is allowed to light.' },
    { guidance: false, description: 'Do not add a second light source that is not a visible surface (an anchor in scrolling content, a point behind text): a glow with no emitter reads as a smudge behind the content, and on a light canvas a tinted glow darkens it into a shadow.' },
    { guidance: false, description: 'Do not rely on DotGlow for any interactive or informational role — it is pointer-events-none, aria-hidden decoration, and goes static under prefers-reduced-motion.' },
    { guidance: false, description: 'Do not hardcode the glow colors or geometry — tint, dot shape/size, density, angle and speed are read live from the appearance runtime bridge.' },
  ],
  anatomy: ['pointer-events-none aria-hidden overlay (absolute inset-0, edge-faded mask)', 'soft CSS bloom hugging the composer rect', 'focus aura bloom (same rect, shown while focused)', 'canvas (3D dot-lattice wave field)'],
}

export default doc
