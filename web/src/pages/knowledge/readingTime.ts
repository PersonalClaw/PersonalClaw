/** The reading-time estimate — ONE place that turns a word count into "how long is this".
 *
 *  It used to be computed inline in `ReadingView`, so the estimate only ever appeared once the
 *  reader was open. A word count is a decision cue — whether to start now — and the moment a
 *  reader needs it is while scanning the LIST, before opening anything. So the arithmetic lives
 *  here and rides the list row and the item metadata as well as the reader.
 */

/** Words per minute used for the estimate. The common editorial figure for adult prose; it is a
 *  rough orientation cue, not a measurement, and being off by 20% costs a reader nothing while
 *  having no estimate at all costs them the decision of whether to start now. */
const WPM = 220

/** Estimated whole minutes to read `wordCount` words, or 0 when there is no count to estimate
 *  from. Floored at 1 for any non-empty body: "0 min read" reads as "nothing here" for an item
 *  that does have words, and the estimate is a cue, not a stopwatch. */
export function readingMinutes(wordCount?: number | null): number {
  return wordCount ? Math.max(1, Math.round(wordCount / WPM)) : 0
}

/** The standalone "N min read" label for a metadata strip, or '' when there is no estimate —
 *  an empty string so a caller can drop the whole element rather than render "0 min read".
 *
 *  The reader's own progress strip does NOT use this: it already says "% read" one token over,
 *  so it composes the bare "N min" from `readingMinutes` to avoid reading "…read · … min read". */
export function readingTimeLabel(wordCount?: number | null): string {
  const minutes = readingMinutes(wordCount)
  return minutes ? `${minutes} min read` : ''
}
