/** The PROSE MEASURE — one line-length token for every continuous-reading surface.
 *
 *  "Measure" is the typographic term for line length, and it is a READABILITY
 *  constant, not a layout preference: past roughly 90 characters a reader loses the
 *  line on the return sweep and re-reads or skips one.
 *
 *  WHY 35rem AND NOT 72 `ch`. The document preview and the HTML export both capped at
 *  72 `ch` units, which sounds like 72 characters and is not: `ch` is the advance
 *  width of "0", and in this font that is 0.66em — so the cap resolved to 758px and a
 *  MEASURED 101 characters on a full line, well past the 45-90 return-sweep band.
 *  35rem measures ~75. Stated in rem so the number means what it says.
 *
 *  The knowledge reader records the same measurement at its own container; this token
 *  is what it, the document preview and the HTML export converge on, so the app has ONE
 *  reading measure rather than a per-surface guess.
 *
 *  The retired utility is deliberately NOT spelled out anywhere in source: Tailwind's
 *  scanner reads source TEXT, comments included, so naming the old class — even in
 *  prose — puts its now-dead rule back into the shipped stylesheet. It did, until this
 *  was reworded.
 *
 *  WHY A TS MODULE AND NOT A CSS VAR IN tokens.css. `--content-width` is a var
 *  because the appearance store REWRITES it at runtime (the width-preset pill), so
 *  only CSS can hold it. This one is fixed, and one of its two consumers —
 *  `ui/content/exporters.ts` — emits a self-contained HTML file that never loads
 *  this app's stylesheet, so it needs the value in JavaScript to inline. A CSS var
 *  could not serve that consumer, and defining the number in BOTH places would
 *  recreate the divergence this token exists to retire.
 */

/** The measure as a raw CSS length — for a stylesheet STRING (the standalone HTML
 *  export) or an inline `style` value. */
export const PROSE_MEASURE = '35rem'

/** The measure as a Tailwind utility — for a `className`.
 *
 *  Written as a LITERAL rather than composed as `` `max-w-[${PROSE_MEASURE}]` ``:
 *  Tailwind v4 generates a utility only for candidate strings it finds in the
 *  scanned source, and a template-composed class never appears there — the rule
 *  would not be emitted and the max-width would be silently absent (the
 *  inert-utility failure mode `design/inertUtilities.test.ts` exists to catch).
 *  `ui/content/proseMeasure.test.tsx` asserts the two forms stay equal, so the
 *  literal cannot drift from the length above. */
export const PROSE_MEASURE_CLASS = 'max-w-[35rem]'
