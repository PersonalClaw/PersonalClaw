import type { UiDoc } from './uiDoc'

// Doc object for DotGlow — the 3D halftone wave surface behind the composer. The
// "measures the composer live each frame" and "focusRef travels the glow with a
// smooth glide" contracts, plus reduced-motion respect, were source comments.
const doc: UiDoc = {
  name: 'DotGlow',
  keywords: ['glow', 'dots', 'wave', 'canvas', 'composer', 'ambient', 'halftone', 'decorative', 'background'],
  description:
    'The "dot glow" — an animated 3D halftone wave surface on a canvas that reads as light cast by the composer onto a rippling ground plane behind it. Decorative background chrome: it measures the composer element LIVE each frame so the illumination tracks it in perfect sync, reads its tint/shape/density params from the appearance runtime, and respects prefers-reduced-motion (static frame).',
  props: [
    { name: 'className', description: 'Extra classes on the absolute-inset overlay container (tokens only).' },
    { name: 'composerRef', description: 'Ref to the composer element; the glow measures it live each frame so the primary light tracks the composer exactly as it spring-animates, with no separate easing. This light always stays lit.' },
    { name: 'focusRef', description: "Optional DYNAMIC focus target (chat glow-travel). When its `.current` is non-null a subtler light SPLITS OFF from the composer and glides toward this element's rect (a held rect lerps each frame), fading in on send and out on done. Absent → behavior is composer-only, unchanged." },
    { name: 'intensity', description: 'Target glow strength (1 = rest, >1 = composer focused/lifted); lerped smoothly rather than snapped.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Pass composerRef so the glow measures the composer live each frame and tracks it in sync — do not feed it a separately-eased position.' },
    { guidance: true, description: 'Use focusRef only for the chat glow-travel (a second light rides the active turn); leave it absent on other surfaces so behavior stays composer-only.' },
    { guidance: false, description: 'Do not rely on DotGlow for any interactive or informational role — it is pointer-events-none, aria-hidden decoration, and goes static under prefers-reduced-motion.' },
    { guidance: false, description: 'Do not hardcode the glow colors or geometry — tint, dot shape/size, density, angle and speed are read live from the appearance runtime bridge.' },
  ],
  anatomy: ['pointer-events-none aria-hidden overlay (absolute inset-0)', 'soft CSS bloom hugging the composer rect', 'second bloom riding the split-off traveling light', 'canvas (3D dot-lattice wave field)'],
}

export default doc
