/** Document outline for the reading view (KL-16).
 *
 *  The item body is markdown — the reader renders it with `<Markdown className="reading">`
 *  — so the headings are parseable straight from the SOURCE, and each one is keyed by the
 *  character index of its first `#`.
 *
 *  ── 🔑 THIS IS NOT A CONTRADICTION OF `readingAnchors`, WHICH REJECTED SOURCE OFFSETS ──
 *
 *  That module's docstring is right, and stays right: *"a character offset into the item's
 *  SOURCE is useless: the transform moves every index"*. It is right about the job it was
 *  doing. A highlight has to FIND ITS PASSAGE IN THE RENDERED DOM after a reload, and a
 *  source index cannot address a DOM position — hence quote + occurrence there.
 *
 *  Here the offset does the other job entirely:
 *
 *      readingAnchors    offset as a DOM COORDINATE   useless — the transform moves it
 *      readingOutline    offset as an IDENTITY        exactly right, and collision-free by
 *                                                     construction: two headings cannot
 *                                                     begin at the same character
 *
 *  So an {@link OutlineEntry} never tries to locate its own heading element, and nothing in
 *  this module touches the DOM. Matching entries to rendered nodes is the caller's job, and
 *  the only thing both sides can agree on without either re-deriving the other's text is
 *  DOCUMENT ORDER: the nth entry here is the nth `h1`–`h6` in the article. The offset stays
 *  the key so that two headings with the same words remain two different rows.
 *
 *  ── 🔑 WHY NOT A SLUG OF THE RENDERED TEXT, which is what a table of contents normally
 *  uses. Two failures, both cheap to hit in a real saved article:
 *
 *    DUPLICATE TITLES  a body with two `## Setup` sections mints ONE id twice, so the
 *                      outline's second row scrolls to the first section forever.
 *    INLINE MARKUP     `## The `config` file` renders as three children (`The`,
 *                      `<code>config</code>`, ` file`), so a text-derived key depends on how
 *                      the renderer splits nodes — and drifts the day that changes.
 *
 *  An offset is immune to both because it is not derived from the text at all. That is also
 *  what frees {@link OutlineEntry.text} to be cleaned up for DISPLAY (inline markers
 *  stripped, whitespace collapsed) without any of it reaching identity.
 *
 *  ── WHAT IT PARSES, AND WHAT IT DELIBERATELY DOES NOT ──
 *
 *  ✓ ATX headings `#`…`######`, 0–3 spaces of indent, marker followed by a space/tab or the
 *    end of the line, closing `#`s stripped when they are spaced off the text (so `## C#`
 *    keeps its sharp). This is the same shape the repo's other markdown-heading consumer
 *    recognizes — `documents/from_markup.py`'s `_HEADING` — widened only to CommonMark's
 *    indent and closing-sequence rules.
 *  ✓ FENCED CODE IS SKIPPED. ``` and ~~~, any fence length ≥ 3, closed by the same character
 *    at ≥ the opening length with no info string, or by the end of the document. A
 *    `# install deps` line inside a shell block is the single most common false heading in a
 *    parser like this one, and it is not a heading.
 *  ✓ 4+ SPACES OF INDENT IS SKIPPED. CommonMark makes that an indented code block, or — after
 *    a paragraph line — a lazy continuation of it. Never a heading, either way.
 *  ✗ SETEXT headings (`===` / `---` underlines). Deliberate, for three reasons: the ingest
 *    path emits ATX (`web/extract.py` — trafilatura, falling back to html2text); `---` after
 *    a paragraph is genuinely ambiguous with a thematic break in real-world markdown, and a
 *    phantom heading is worse than a missing one; and a setext heading has no `#` to point
 *    `offset` at, so its key would mean something different from every other entry's.
 *  ✗ Headings nested in a blockquote (`> ## x`) or a list item, and raw HTML `<h2>` (which
 *    `ui/Markdown` passes through via rehype-raw).
 *
 *  ⚠️ THE CONSEQUENCE FOR ORDER MATCHING. Those last two DO render as heading elements, so on
 *  a body that uses them the article has MORE headings than this returns and the nth-entry ⇄
 *  nth-node mapping silently slips by one. A caller doing that mapping must compare
 *  `entries.length` against the number of heading nodes it found and decline the match when
 *  they disagree — an off-by-one outline is worse than no scroll spy.
 */

export interface OutlineEntry {
  /** The character index of the heading's first `#` in the markdown source. THE KEY:
   *  stable, collision-free, and independent of how the text renders. Not a DOM position. */
  offset: number
  /** Nesting level, 0-based and RELATIVE TO THE SHALLOWEST HEADING IN THIS DOCUMENT — a body
   *  whose top level is `##` indents from flat, because "one level in" is meaningless without
   *  a level to be in from. */
  depth: number
  /** The heading's own words, for display: markers stripped, inline markdown flattened,
   *  whitespace collapsed. Free to change without breaking anything, because it is never
   *  the key. */
  text: string
}

/** CommonMark ATX: up to 3 spaces of indent, 1–6 `#`, then a space/tab before the text (or
 *  nothing at all, which is a legal empty heading). `#hashtag` and `####### seven` match
 *  neither, exactly as the renderer treats them. */
const ATX = /^( {0,3})(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/

/** A fence opener or closer: ≥3 backticks or tildes at ≤3 spaces of indent. Group 2 is the
 *  info string, which only an OPENER may carry. */
const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/

/** The heading's source text reduced to the words a reader recognizes.
 *
 *  Display only. The inline forms are the ones `documents/from_markup.py`'s `_strip_inline`
 *  already strips (code spans, emphasis, links), plus GFM strikethrough and images, since
 *  `ui/Markdown` loads remark-gfm. Leaving the markers in would print literal backticks and
 *  asterisks in the panel; stripping them is safe here in a way it would NOT be for a
 *  slug-keyed outline, which is the whole point of keying on the offset. */
function displayText(raw: string): string {
  return raw
    // A closing `#` sequence, only when it is spaced off the text — `## C#` is a heading
    // about C#, not a heading named "C" with a stray marker.
    .replace(/[ \t]+#+[ \t]*$/, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1') // image → its alt text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1') // link → its label
    .replace(/`([^`]+)`/g, '$1') // code span
    .replace(/(\*\*|__)(.+?)\1/g, '$2') // strong, before emphasis so `**x**` is not eaten
    .replace(/(\*|_)(.+?)\1/g, '$2') // emphasis
    .replace(/~~(.+?)~~/g, '$1') // GFM strikethrough
    .replace(/\s+/g, ' ')
    .trim()
}

/** Every ATX heading in `markdown`, in document order, keyed by source offset and with
 *  `depth` normalised so the shallowest heading present sits at 0. Returns `[]` for an empty
 *  or heading-less body. */
export function parseOutline(markdown: string): OutlineEntry[] {
  const found: Array<{ offset: number; level: number; text: string }> = []
  // An open fence: which character opened it and how long it was. A closer must match both.
  let fence: { char: string; len: number } | null = null
  let offset = 0

  for (const line of (markdown || '').split('\n')) {
    const f = FENCE.exec(line)
    if (fence) {
      if (f && f[1][0] === fence.char && f[1].length >= fence.len && !f[2].trim()) fence = null
    } else if (f) {
      fence = { char: f[1][0], len: f[1].length }
    } else {
      const m = ATX.exec(line)
      // `offset + indent` is the index of the `#` itself, not of the line — an indented
      // heading's key points at the marker, so the key means one thing everywhere.
      if (m) found.push({ offset: offset + m[1].length, level: m[2].length, text: displayText(m[3] ?? '') })
    }
    offset += line.length + 1 // +1 for the '\n' that split() consumed
  }

  if (!found.length) return []
  const shallowest = found.reduce((min, h) => (h.level < min ? h.level : min), 6)
  return found.map((h) => ({ offset: h.offset, depth: h.level - shallowest, text: h.text }))
}
