import type { CSSProperties } from 'react'

// ── Variable-font weight helper (design-system consistency, S2/S3) ──────────
// The app's variable font is driven by `font-variation-settings: "wght" <n>`.
// The S1 audit found this set INLINE ~180 times across pages (75× wght 500,
// 48× 600, 42× 550, 11× 470, …) — a genuine consistency pattern with no shared
// home, so every call site hand-writes the same string.
//
// `fvs(n)` returns the exact same style object those call sites already use —
// a BYTE-IDENTICAL drop-in (zero visual change) that gives the pattern one home
// and makes weights greppable/typo-proof. Prefer a type-role (`data-type=…`,
// see tokens.css) when the element maps to one (it sets size+weight together);
// use `fvs()` for the many cases that only need a weight nudge on existing text.
//
// There are also `.fw-<n>` utility CLASSES in tokens.css (same weights) for
// className-based usage — pick whichever fits the call site. Both emit the
// identical `font-variation-settings`.

/** The canonical variable-font weights the app uses. */
export type FontWeight = 400 | 470 | 500 | 550 | 600 | 650

/** Inline style for a variable-font weight — identical to the hand-written
 *  `{ fontVariationSettings: '"wght" <n>' }` it replaces. */
export function fvs(weight: FontWeight): CSSProperties {
  return { fontVariationSettings: `"wght" ${weight}` }
}

/** Merge a weight onto an existing style object (keeps other props). */
export function withWeight(style: CSSProperties | undefined, weight: FontWeight): CSSProperties {
  return { ...style, fontVariationSettings: `"wght" ${weight}` }
}
