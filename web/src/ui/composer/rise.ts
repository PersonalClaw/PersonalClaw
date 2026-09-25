/** How far the composer may GROW, per side, when it wakes — in layout px.
 *
 *  The composer sits in the page gutter (`px-l`, 16px) on every surface that docks it or runs it at
 *  the shipped `full` width, and the page box clips whatever crosses that gutter. Its focus and
 *  drag-over springs scaled by a FRACTION of its own width, so the growth was proportional to how
 *  wide the composer happened to be: at `full` width (1212px on a 1440px window) focus grew it
 *  ~8.5px a side and drag-over ~15px — the whole gutter — which put its own shadow, and at drag-over
 *  the composer itself, across the rail edge, where the page clip cut it into a straight line.
 *
 *  Bounding the growth in px keeps the "rises to meet you" spring identical on a composer narrow
 *  enough never to reach the cap, and stops a wide one from lurching into its gutter. The budget
 *  that is left in the gutter belongs to `--shadow-composer-focus` (tokens.css), which is sized to it.
 */
export const RISE_GROW_PX = 4
export const DROP_GROW_PX = 6

/** The awake scale for a composer `width` px wide: `1 + fraction`, capped so neither side grows by
 *  more than `maxGrowPx`. An unmeasured composer (`width` 0 — first paint, or no ResizeObserver)
 *  keeps the plain fraction rather than guessing a width. */
export function riseScale(fraction: number, maxGrowPx: number, width: number): number {
  if (!(width > 0)) return 1 + fraction
  return 1 + Math.min(fraction, (2 * maxGrowPx) / width)
}
