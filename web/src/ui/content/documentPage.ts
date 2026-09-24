/** Page geometry for the document editor's layout controls and its preview (DFE-6).
 *
 *  **This is a mirror of `documents/model.py`'s `PAGE_SIZE_IN`, not a second source of
 *  truth.** The browser needs page dimensions to draw the preview and cannot import
 *  Python, so the table is duplicated — and `tests/test_document_layout.py::
 *  test_the_frontends_page_size_table_matches_the_models` reds if the two drift, because a
 *  drift is either a preview drawing the wrong paper or a size the editor offers and the
 *  server refuses with a 400.
 *
 *  **Points are the wire unit**, matching `PageSetup`'s `margin_*_pt` and
 *  `ParagraphStyle`'s `*_pt`. Inches appear only in the size table (which is what paper is
 *  specified in) and centimetres only in the controls, where a metric user types 2.
 */

/** The page sizes the model accepts. `''` means "the writer's template decides". */
export type PageSizeName = '' | 'letter' | 'a4' | 'legal' | 'tabloid'

/** PORTRAIT (width, height) in inches. Landscape is the same pair swapped. */
export const PAGE_SIZE_IN: Record<Exclude<PageSizeName, ''>, [number, number]> = {
  letter: [8.5, 11],
  a4: [210 / 25.4, 297 / 25.4],
  legal: [8.5, 14],
  tabloid: [11, 17],
}

export const PAGE_SIZE_LABEL: Record<PageSizeName, string> = {
  '': 'Template default',
  letter: 'Letter',
  a4: 'A4',
  legal: 'Legal',
  tabloid: 'Tabloid',
}

export const ORIENTATION_LABEL: Record<string, string> = {
  '': 'Template default',
  portrait: 'Portrait',
  landscape: 'Landscape',
}

export const ALIGN_LABEL: Record<string, string> = {
  '': 'Template default',
  left: 'Left',
  center: 'Center',
  right: 'Right',
  justify: 'Justify',
}

export const POINTS_PER_CM = 72 / 2.54

/** Points → centimetres, rounded to two places for display in a numeric input.
 *
 *  Rounded for DISPLAY only: the value round-trips through the model in points, so a
 *  control showing 2.00 does not rewrite the document's 56.6929pt unless a user edits it.
 */
export function ptToCm(points: number): number {
  return Math.round((points / POINTS_PER_CM) * 100) / 100
}

export function cmToPt(cm: number): number {
  return cm * POINTS_PER_CM
}

/** The page's (width, height) in inches with orientation applied, or `null` when no size
 *  is named — the mirror of `PageSetup.size_in()`'s `(0.0, 0.0)`.
 *
 *  `null` rather than a Letter fallback: the preview must be able to say "the template
 *  decides" instead of drawing a page the document never asked for.
 */
export function pageSizeIn(
  size: PageSizeName,
  orientation: string,
): { width: number; height: number } | null {
  if (!size) return null
  const [portraitW, portraitH] = PAGE_SIZE_IN[size]
  const landscape = orientation === 'landscape'
  return {
    width: landscape ? portraitH : portraitW,
    height: landscape ? portraitW : portraitH,
  }
}

/** The preview's box model, in percentages of the page, ready for CSS.
 *
 *  Percentages rather than pixels so the caller owns the rendered scale — the preview is a
 *  labelled approximation of proportions, and committing to pixels here would make it look
 *  like a measurement.
 */
export interface PreviewGeometry {
  /** width / height of the page itself, for an aspect ratio. */
  aspect: number
  /** Each margin as a percentage of the corresponding page dimension. */
  inset: { top: number; bottom: number; left: number; right: number }
}

export function previewGeometry(
  size: PageSizeName,
  orientation: string,
  margins: { top: number; bottom: number; left: number; right: number },
): PreviewGeometry | null {
  const page = pageSizeIn(size, orientation)
  if (!page) return null
  const heightPt = page.height * 72
  const widthPt = page.width * 72
  const share = (points: number, total: number) =>
    // Clamped: a margin wider than the page would invert the box and draw a preview that
    // is not merely approximate but wrong. 45% a side leaves the text area visible.
    Math.max(0, Math.min(45, (points / total) * 100))
  return {
    aspect: page.width / page.height,
    inset: {
      top: share(margins.top, heightPt),
      bottom: share(margins.bottom, heightPt),
      left: share(margins.left, widthPt),
      right: share(margins.right, widthPt),
    },
  }
}
