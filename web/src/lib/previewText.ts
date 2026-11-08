/** One-line PLAIN-TEXT preview of markdown-ish content — for list rows, tooltips, and
 *  accessible names, where markup markers read as garbage (`**bold**` is announced
 *  star-star-bold-star-star, and a heading's `##` renders literally in a truncated row).
 *
 *  This is deliberately NOT a markdown parser: a preview needs the marks GONE, not the
 *  tree honored. The renderer (`ui/Markdown`) stays the one place markdown becomes
 *  markup; this is the one place it becomes plain text. Both existing consumers of raw
 *  bodies (the inbox row preview, the artifact-card excerpt label path) route here, so
 *  the next preview surface inherits the same stripping instead of leaking marks its
 *  own way.
 *
 *  Two entry points, one inline pass (`inlinePlain`): `previewText` for a BODY, which first drops
 *  the line-leading block marks (a heading's `#`, a quote's `>`, a list's bullet, a fence line),
 *  and `previewLine` for a field written as ONE line, which keeps them, as `Markdown`'s `line`
 *  mode draws them. `markdownLineParity.test.tsx` holds the two entry points to the same result on
 *  every inline construct, and both to the text the renderer's `line` mode draws.
 */
export function previewText(md: string | null | undefined, cap?: number): string {
  let s = md ?? ''
  // Fenced code: keep the code, drop the fence lines (a preview of a code artifact
  // should show code, not ```lang).
  s = s.replace(/^```[^\n]*$/gm, '')
  // Headings, blockquotes, list bullets — line-leading structural marks.
  s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '')
  s = s.replace(/^\s{0,3}>\s?/gm, '')
  s = s.replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+/gm, '')
  return oneLine(inlinePlain(s), cap)
}

/** The plain text of a field written as ONE line (a title, a reason, a step), as `MarkdownLine`
 *  draws it: for its tooltip, and for an accessible name built from it. A line starts no block,
 *  so a leading "#", "2024." or ">" is its text and stays. */
export function previewLine(md: string | null | undefined, cap?: number): string {
  return oneLine(inlinePlain(md ?? ''), cap)
}

/** A link's destination may hold one level of parentheses (`javascript:alert(1)`), as the parser
 *  reads it. */
const DESTINATION = String.raw`\((?:[^()\n]|\([^()\n]*\))*\)`
const IMAGE = new RegExp(String.raw`!\[([^\]]*)\]` + DESTINATION, 'g')
const LINK = new RegExp(String.raw`\[([^\]]+)\]` + DESTINATION, 'g')
/** The attribute-free formatting tags the renderer keeps (`ui/Markdown`'s `EMBEDDED_FORMATTING`):
 *  they format, so their words are the text. Any other tag is shown as the text it is. A copy of the
 *  renderer's list, held to it one row per tag by `markdownLineParity.test.tsx`. */
const FORMATTING_TAG = /<\/?(?:b|strong|i|em|u|s|del|ins|mark|small|sub|sup|kbd|code|br|details|summary)\s*\/?>/gi
const AUTOLINK = /<((?:https?|mailto|ftp):[^\s<>]*|[^\s<>@]+@[^\s<>]+\.[^\s<>]+)>/gi
const ESCAPED = /\\([!-/:-@[-`{-~])/g
const ENTITY = /&(?:#(\d{1,7})|#[xX]([0-9a-fA-F]{1,6})|(amp|lt|gt|quot|apos|nbsp));/g
const NAMED: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }

/** Set aside what the later rules must not read, as a private-use pair around its index, and give it back after. */
function holder() {
  const held: string[] = []
  return {
    hold: (text: string) => `\uE010${held.push(text) - 1}\uE011`,
    restore: (s: string) => s.replace(/\uE010(\d+)\uE011/g, (_m, i: string) => held[Number(i)]),
  }
}

/** `s` with every HTML comment removed whole, as the renderer drops a comment. Comment removal is
 *  one linear pass, left to right: a comment still open when a `-->` ends at least 7 characters on
 *  ("<!---->" is the shortest) is cut out, and what is before it is read on from there, so a comment
 *  that removing one leaves behind it ("<!<!-- x -->-- y -->") goes too. */
function withoutComments(s: string): string {
  const out: string[] = []
  let open = -1 // where the comment still open in `out` starts, or -1
  for (const ch of s) {
    out.push(ch)
    const n = out.length
    if (open < 0) {
      if (n >= 4 && out[n - 4] === '<' && out[n - 3] === '!' && out[n - 2] === '-' && out[n - 1] === '-') open = n - 4
    } else if (n - open >= 7 && out[n - 3] === '-' && out[n - 2] === '-' && out[n - 1] === '>') {
      out.length = open
      open = -1
    }
  }
  return out.join('')
}

/** The inline pass both entry points share: a code span is its code, an escaped mark the mark, a
 *  link and an image their words, a formatting tag nothing, an autolink its address, emphasis,
 *  strikethrough and inline math their text, an entity its character. A code span's text is code,
 *  so it is set aside first and no other rule reads it (`__init__.py`). */
function inlinePlain(s: string): string {
  const { hold, restore } = holder()
  const linked = s
    .replace(/`([^`]+)`/g, (_m, code: string) => hold(code))
    .replace(ESCAPED, (_m, mark: string) => hold(mark))
    .replace(IMAGE, '$1')
    .replace(LINK, '$1')
    .replace(AUTOLINK, '$1')
  // A line break is the space between its words, written as an opener or a closer, as the HTML
  // parser reads both.
  const out = withoutComments(linked)
    .replace(/<\/?br\s*\/?>/gi, ' ')
    .replace(FORMATTING_TAG, '')
    // Order matters: ** before *.
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    // Inside a word (`mcp__github__create_issue`) a double underscore is no emphasis, as a single one
    // is not: both need a non-word on each side.
    .replace(/(^|\W)__([^_]+)__(?=\W|$)/g, '$1$2')
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/(^|\W)_([^_\n]+)_(?=\W|$)/g, '$1$2')
    .replace(/~~([^~\n]+)~~/g, '$1')
    .replace(/(^|[^~])~([^~\s](?:[^~\n]*[^~\s])?)~(?!~)/g, '$1$2')
    .replace(/\$\$([^$\n]+)\$\$/g, '$1')
    .replace(ENTITY, (m, dec?: string, hex?: string, name?: string) => {
      if (name) return NAMED[name]
      const code = dec ? Number(dec) : parseInt(hex ?? '', 16)
      return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : m
    })
  return restore(out)
}

/** One line: newlines and runs of space collapse, exactly like the visual truncate renders
 *  them, then the cap with an ellipsis. The cap never splits a character: a cut that would end on
 *  the first half of a surrogate pair ends before it. */
function oneLine(s: string, cap?: number): string {
  const line = s.replace(/\s+/g, ' ').trim()
  if (!cap || line.length <= cap) return line
  let end = cap - 1
  if (/[\uD800-\uDBFF]/.test(line[end - 1] ?? '')) end -= 1
  return `${line.slice(0, end)}…`
}
