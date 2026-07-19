// Mutable runtime bridge for animated params the <canvas> reads each frame.
// The appearance store writes here when tokens change; DotGlow reads from it
// (cheap object reads — no per-frame getComputedStyle). Defaults match the
// resting design.
export const runtime = {
  glow: 1,            // glow intensity multiplier (--glow)
  animSpeed: 1,       // wave time multiplier (--anim-speed)
  waveAmount: 1,      // wave amplitude multiplier (--wave-amount)
  surfaceAngle: 45,   // 3D surface camera PITCH in degrees (--surface-angle): 0=edge-on/steep … 90=top-down flat
  surfaceDistance: 1, // 3D surface camera distance (--surface-distance): higher = farther/wider POV
  dotSize: 1,         // dot size multiplier (--dot-size)
  dotDensity: 1,      // dot density (--dot-density): higher = more dots / less spacing
  dotShape: 'claude' as 'circle' | 'square' | 'diamond' | 'star' | 'sparkle' | 'burst' | 'claude', // dot shape (PClaw sunburst, off Gemini's sparkle)
  dotPattern: 'hex' as 'grid' | 'diamond' | 'hex' | 'brick', // lattice arrangement
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
}
