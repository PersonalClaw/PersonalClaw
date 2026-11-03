/** The Settings overview's grid, as two pure functions: how many columns a width holds, and the
 *  span each card takes so every row of a group ends at the grid's right edge (#5897).
 *
 *  The overview used to pack whole groups into columns, each group one block in the shortest
 *  column. A group could never split, so its tallest one (twenty cards) was a column of its own and
 *  the three columns ended over a thousand pixels apart. It is a grid of row bands now: a group's
 *  heading, then its cards in source order, one or two columns each.
 *
 *  Source order is the order on screen. No `grid-auto-flow: dense`, which backfills a hole with a
 *  later card and so moves it ahead of the ones before it, out of the order the keyboard and a
 *  screen reader follow (the CSS Grid spec calls reordering logical order that way
 *  non-conforming). Rows end even because the spans are chosen to fill them, not because cards are
 *  moved.
 *
 *  Pure: no DOM, no imports. `settingsRowsEndEven.test.ts` pins both. */

/** The column count for a grid `width` px wide: 1 under 640, 2 under 960, 3 from there. Read from the
 *  space the grid has, so a collapsed sidebar or a narrow window changes it as a phone does. */
export function columnsFor(width: number): number {
  if (width < 640) return 1
  if (width < 960) return 2
  return 3
}

/** The span each card of one group takes at `columns`, in order.
 *
 *  Each card asks for a span (1 or 2) and gets at most `columns`. When a card does not fit what is
 *  left of its row, the card before it widens to the row's end and this one opens the next row; the
 *  group's last card widens the same way. So every row sums to `columns`, at every width and for
 *  every set of cards a search leaves on screen. A layout chosen with this in mind widens nothing at
 *  the widths it was chosen for: the widening is what keeps a filtered group even. */
export function packRows(spans: readonly number[], columns: number): number[] {
  const out = spans.map((s) => Math.max(1, Math.min(s, columns)))
  let used = 0
  for (let i = 0; i < out.length; i++) {
    if (used + out[i] > columns) {
      out[i - 1] += columns - used
      used = 0
    }
    used += out[i]
    if (used === columns) used = 0
  }
  if (used > 0) out[out.length - 1] += columns - used
  return out
}
