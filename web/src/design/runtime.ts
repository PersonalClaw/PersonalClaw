// Mutable runtime bridge for animated params the <canvas> reads each frame.
// The appearance store writes here when tokens change; DotGlow reads from it
// (cheap object reads — no per-frame getComputedStyle). Defaults match the
// resting design.

/** The dot glyph the halftone surface paints (`--dot-shape`). Named because more
 *  than one declaration site needs the union — the select token's value list, this
 *  bridge, and a personality's dial preset — and three hand-copied unions would
 *  drift the moment a shape is added. */
export type DotShape = 'circle' | 'square' | 'diamond' | 'star' | 'sparkle' | 'burst' | 'claude'
/** The lattice arrangement the dots sit on (`--dot-pattern`). */
export type DotPattern = 'grid' | 'diamond' | 'hex' | 'brick'

export const runtime = {
  glow: 1,            // glow intensity multiplier (--glow)
  animSpeed: 1,       // wave time multiplier (--anim-speed)
  waveAmount: 1,      // wave amplitude multiplier (--wave-amount)
  surfaceAngle: 45,   // 3D surface camera PITCH in degrees (--surface-angle): 0=edge-on/steep … 90=top-down flat
  surfaceDistance: 1, // 3D surface camera distance (--surface-distance): higher = farther/wider POV
  dotSize: 1,         // dot size multiplier (--dot-size)
  dotDensity: 1,      // dot density (--dot-density): higher = more dots / less spacing
  dotShape: 'claude' as DotShape, // dot shape (PClaw sunburst, off Gemini's sparkle)
  dotPattern: 'hex' as DotPattern, // lattice arrangement
  glowA: [255, 107, 91] as [number, number, number],  // --glow-a (coral)
  glowB: [255, 154, 122] as [number, number, number],  // surface accent (coral grad-3)
  // Motion personality (--bounciness): 0 = calm/no overshoot … 1 = playful.
  // Scales the spring overshoot + morph amount on the bounce tiers. Default 1
  // (playful) per the brand decision; users dial it in Appearance → Motion.
  bounciness: 1,
  // Expressiveness (--expressiveness): the PRIMARY intensity dial for the whole
  // motion/morph language — 0 = refined/tasteful (heavy effects fade toward
  // subtle: gentler morph, smaller lift, sheen off), 1 = bold/showpiece. Default
  // 0.8 (bold-leaning) per the v2 brand decision. Every expressive treatment
  // (hover-lift, press depth, morph delta, container-transform, sheen gate)
  // multiplies through `expr()`/`exprHeavy()` in design/motion.ts so ONE dial
  // governs the system. Reduced-motion still overrides everything to near-static
  // regardless of this value.
  expressiveness: 0.8,
  // ── Gesture physics (--drag-elastic / --swipe-dismiss-velocity / -distance) ──
  // Read by dragElastic()/swipeDismiss() in design/motion.ts. Kept here, not in
  // CSS-land, because a gesture threshold is compared against a Framer velocity in
  // JS — getComputedStyle on every drag end would be absurd. `dragElastic` is how
  // far a dragged element stretches past its constraints (0 = rigid, 1 = loose);
  // the two dismiss thresholds are OR'd, so a fast flick and a slow deliberate haul
  // both dismiss.
  dragElastic: 0.9,
  swipeVelocity: 500, // px/s flick speed that dismisses
  swipeDistance: 80,  // px dragged that dismisses regardless of speed
}
