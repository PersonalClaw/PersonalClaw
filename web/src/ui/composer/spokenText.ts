/** Where a dictated transcript lands in the draft: set apart from the text around it.
 *
 *  `before` is what comes before the caret (or the selection), up to its last eight characters, and
 *  `after` what comes after it, up to its first eight; each is `''` at an end of the draft. A side
 *  gets a space when its neighbour is not whitespace, except:
 *  - inside an opening bracket, and before closing punctuation, so `(spoken` and `spoken.` stay
 *    tight;
 *  - inside a run of Markdown markers that opens (whitespace or the draft's start before it), and
 *    before a run that closes (whitespace or the draft's end after it), so `**spoken**` stays tight,
 *    while a dictation after `**bold**` or before `_this_` is set apart, as after a word;
 *  - after a straight quote that opens a quotation (whitespace or the draft's start before it),
 *    and before one that closes it (no whitespace before the caret).
 *  So two dictations in a row read as two sentences, and a dictation into the middle of a sentence
 *  keeps one space on each side. The transcript's own leading and trailing whitespace is dropped
 *  first. `caret` is the offset just after the transcript, inside `insert`. */
export function spacedInsertion(before: string, after: string, text: string): { insert: string; caret: number } {
  const spoken = text.trim()
  if (!spoken) return { insert: '', caret: 0 }
  const prev = before.slice(-1)
  const prevPrev = before.slice(-2, -1)
  const next = after.slice(0, 1)
  const blank = (c: string) => !c || /\s/.test(c)
  const opensQuote = QUOTES.has(prev) && blank(prevPrev)
  const closesQuote = QUOTES.has(next) && !blank(prev)
  // A run of Markdown markers is judged by its far side, as a quote is: the dictation goes inside
  // a run that opens and up against one that closes, and is set apart past a closed run, as after
  // a word.
  const runBefore = /[*_~`]+$/.exec(before)?.[0] ?? ''
  const runAfter = /^[*_~`]+/.exec(after)?.[0] ?? ''
  const opensBefore = !!runBefore && blank(before.slice(0, before.length - runBefore.length).slice(-1))
  const closesAfter = !!runAfter && blank(after.charAt(runAfter.length))
  const lead = !blank(prev) && !OPENING.has(prev) && !opensQuote && !opensBefore ? ' ' : ''
  const trail = !blank(next) && !CLOSING.has(next) && !closesQuote && !closesAfter ? ' ' : ''
  return { insert: lead + spoken + trail, caret: lead.length + spoken.length }
}

const QUOTES = new Set(['"', "'"])
const OPENING = new Set(['(', '[', '{', '“', '‘'])
const CLOSING = new Set(['.', ',', ';', ':', '!', '?', ')', ']', '}', '”', '’'])
