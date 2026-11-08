/** Cutting markdown that will be RENDERED, where a helper has to cut it to a budget.
 *
 *  A visible sink gets the whole text and lets CSS (`truncate`, `line-clamp`) do the cut. Where a
 *  string itself has to be shortened (a 400-character preview, a field the server already cut), a
 *  plain `slice` can stop inside a construct: half a fence turns everything after it into code,
 *  and half a code span or a `**` shows its marks. These two cut where the text still renders:
 *
 *   · `cutMarkdown(md, max)` cuts a body at a paragraph boundary, and never inside a fence;
 *   · `cutMarkdownLine(md, max)` cuts one line and closes what it left open (a code span, an
 *     emphasis, a strikethrough), drops a link it cut in half to its words, and repairs a string a
 *     writer already cut and ended with "…".
 *
 *  Each marks its cut with "…". Plain text (an attribute, a tooltip) is cut by `previewText`. */

const ELLIPSIS = '…'
const FENCE = /^ {0,3}(`{3,}|~{3,})/

/** A fence's opening run, or null when `line` opens none. */
const fenceOpen = (line: string): string | null => FENCE.exec(line)?.[1] ?? null

/** Does `line` close the fence `open` opened (the same character, at least as long, nothing after)? */
function closesFence(line: string, open: string): boolean {
  const m = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(line)
  return !!m && m[1][0] === open[0] && m[1].length >= open.length
}

/** `md` cut to about `max` characters at the last block boundary that fits (a blank line, the
 *  line before a fence, the end of a fence), never inside a fence. When even the first block does
 *  not fit, a paragraph is cut as a line (`cutMarkdownLine`) and a fence keeps the lines that fit,
 *  closed. */
export function cutMarkdown(md: string, max: number): string {
  if (md.length <= max) return md
  const lines = md.split('\n')
  let fence: string | null = null
  let best = -1
  let pos = 0
  for (const line of lines) {
    if (pos > max) break
    const end = pos + line.length
    if (fence) {
      if (closesFence(line, fence)) {
        fence = null
        if (end <= max) best = end
      }
    } else if (fenceOpen(line)) {
      fence = fenceOpen(line)
      if (pos > 0) best = pos - 1
    } else if (!line.trim() && pos > 0) {
      best = pos
    }
    pos = end + 1
  }
  const kept = best > 0 ? md.slice(0, best).trimEnd() : ''
  if (kept) return `${kept}\n\n${ELLIPSIS}`
  // No boundary fits: cut the first block on its own.
  const first = Math.max(0, lines.findIndex((l) => l.trim()))
  const open = fenceOpen(lines[first])
  if (open) {
    const body: string[] = [lines[first]]
    let used = lines[first].length
    for (const line of lines.slice(first + 1)) {
      if (closesFence(line, open) || used + 1 + line.length > max) break
      body.push(line)
      used += 1 + line.length
    }
    return `${body.join('\n')}\n${open}\n\n${ELLIPSIS}`
  }
  const block: string[] = []
  for (const line of lines.slice(first)) {
    if (!line.trim() || fenceOpen(line)) break
    block.push(line)
  }
  return cutMarkdownLine(block.join('\n'), max)
}

/** The emphasis and strikethrough runs a cut can leave open. */
type Mark = '**' | '*' | '__' | '_' | '~~' | '~'

/** May the `_` run at `i` (of length `n`) be emphasis? Inside a word (`model_calls`) it never is. */
function underscoreCounts(s: string, i: number, n: number): boolean {
  const before = s[i - 1] ?? ' '
  const after = s[i + n] ?? ' '
  return !(/[\p{L}\p{N}]/u.test(before) && /[\p{L}\p{N}]/u.test(after))
}

/** An emphasis or strikethrough opener a scan found: its mark, where its run starts, the run's length. */
type Opener = { mark: Mark; at: number; n: number }

/** Read `s` as a parse reads its marks: the code span (or fence) still open at its end, the
 *  emphasis and strikethrough openers still open, and where each opener a later run closed starts.
 *  A run closes the mark on top when it follows a word, and opens one when a word follows it: a `*`
 *  between spaces ("5 * 3") is neither, and is left alone. An emphasis cannot run past a blank line,
 *  so a paragraph's end forgets the openers its paragraph left. */
function scanMarks(s: string): { open: Opener[]; closed: Set<number>; code: number; fence: boolean } {
  let code = 0 // the open code span's backtick count, or 0
  let fence = false // the open code run starts a line, so it is a fence in a block parse
  const stack: Opener[] = []
  const closed = new Set<number>()
  const toggle = (m: Mark, at: number, n: number) => {
    const before = s[at - 1] ?? ' '
    const after = s[at + n] ?? ' '
    const top = stack[stack.length - 1]
    if (top?.mark === m && !/\s/.test(before)) { stack.pop(); closed.add(top.at) }
    else if (!/\s/.test(after)) stack.push({ mark: m, at, n })
  }
  for (let i = 0; i < s.length;) {
    const c = s[i]
    if (c === '\n' && !code && /^\n[ \t]*(?:\n|$)/.test(s.slice(i))) { stack.length = 0; i++; continue }
    if (c === '\\' && !code) { i += 2; continue }
    if (c === '`') {
      let n = 1
      while (s[i + n] === '`') n++
      if (!code) { code = n; fence = n >= 3 && /(?:^|\n) {0,3}$/.test(s.slice(0, i)) }
      else if (n === code) code = 0
      i += n
      continue
    }
    if (code || (c !== '*' && c !== '_' && c !== '~')) { i++; continue }
    let n = 1
    while (s[i + n] === c) n++
    if (c !== '_' || underscoreCounts(s, i, n)) {
      if (c === '~') toggle(n >= 2 ? '~~' : '~', i, n)
      else {
        const double = (c + c) as Mark
        const single = c as Mark
        // A `***` run is both: it closes the inner one first when both are open.
        if (n >= 3 && stack[stack.length - 1]?.mark === single) { toggle(single, i, n); toggle(double, i, n) }
        else if (n >= 3) { toggle(double, i, n); toggle(single, i, n) }
        else toggle(n === 2 ? double : single, i, n)
      }
    }
    i += n
  }
  return { open: stack, closed, code, fence }
}

/** Close what a cut left open in `s`: a code span (or a fence, on a line of its own), then the
 *  emphasis and strikethrough runs, in the reverse of the order they opened. `fenced` says the text
 *  now ends with a fence's closing line, which nothing may follow on that line. */
function closeOpenMarks(s: string): { text: string; fenced: boolean } {
  const { open, code, fence } = scanMarks(s)
  if (code && fence) return { text: `${s}\n${'`'.repeat(code)}`, fenced: true }
  let out = s
  if (code) out += '`'.repeat(code)
  for (let k = open.length - 1; k >= 0; k--) out += open[k].mark
  return { text: out, fenced: false }
}

/** `cut`, a prefix of `whole`, with each opener still open at its end that no run in `whole` ever
 *  closes escaped: it was a literal ("Pass *args to the helper"), and closing it would draw an
 *  emphasis the uncut line never had. */
function keepLiteralOpeners(cut: string, whole: string): string {
  const { closed } = scanMarks(whole)
  const runs = new Map<number, number>()
  for (const o of scanMarks(cut).open) if (!closed.has(o.at)) runs.set(o.at, o.n)
  let out = cut
  for (const [at, n] of [...runs].sort((a, b) => b[0] - a[0])) {
    out = out.slice(0, at) + out.slice(at, at + n).replace(/./g, '\\$&') + out.slice(at + n)
  }
  return out
}

/** `cut` without what a cut left half-written at its end: an escape whose mark it cut off, and a
 *  run of marks that is backticks, follows a space, or partly closes an open mark of its own
 *  character. A run that is escaped, sits inside an open code span, or ends a word with no mark of
 *  its character open ("$9.99*") is the text it is, and is kept. */
function dropPartialRun(cut: string): string {
  let t = cut.trimEnd()
  if (/(?:^|[^\\])(?:\\\\)*\\$/.test(t)) t = t.slice(0, -1).trimEnd()
  const m = /([`*_~])\1*$/.exec(t)
  if (!m) return t
  const at = m.index
  const before = t.slice(0, at)
  const drop = () => before.trimEnd()
  if (/\\+$/.test(before) && (/\\+$/.exec(before)![0].length % 2 === 1)) return t
  if (m[1] === '`') return drop()
  const state = scanMarks(before)
  if (state.code) return t
  if (/\s/.test(before[at - 1] ?? ' ')) return drop()
  if (state.open.some((o) => o.mark[0] === m[1])) return drop()
  return t
}

/** `cut` without a last line that holds only a block marker ("- first item\n-"), which reads as a
 *  stray mark after the words. */
function dropBareMarker(cut: string): string {
  const nl = cut.lastIndexOf('\n')
  if (nl < 0 || !/^[ \t]*(?:[-*+>]|#{1,6}|\d+[.)])[ \t]*$/.test(cut.slice(nl + 1))) return cut
  return cut.slice(0, nl).trimEnd()
}

/** `body` cut to fit `max` with its ellipsis, at a word when one is near, never between the two
 *  halves of a surrogate pair. */
function cutAtWord(body: string, max: number): string {
  let end = Math.max(1, max - 1)
  if (/[\uD800-\uDBFF]/.test(body[end - 1] ?? '')) end -= 1
  const hard = body.slice(0, end)
  const space = hard.search(/\s\S*$/)
  return space >= Math.floor(hard.length * 0.6) ? hard.slice(0, space) : hard
}

/** A link the cut left half-written keeps its words: "[the report](https://exa" and "[the rep"
 *  both become their text. */
function dropCutLink(s: string): string {
  return s.replace(/\[([^\]]*)\]\([^)]*$/, '$1').replace(/!?\[([^\]]*)$/, '$1')
}

/** One line (a title, a reason, a snippet) cut to about `max` characters, at a word when one is
 *  near, with what the cut left open closed. An opener the whole line never closes is a literal and
 *  stays one, a literal or escaped mark at the cut is kept, and a character is never split. A line
 *  that a writer already cut and ended with "…" (the gateway's 80- and 240-character previews) is
 *  repaired by closing everything it left open: what came after its cut is unknown. */
export function cutMarkdownLine(md: string, max: number): string {
  const preCut = md.endsWith(ELLIPSIS)
  const body = preCut ? md.slice(0, -ELLIPSIS.length) : md
  if (!body.trim()) return ''
  if (!preCut && body.length <= max) return body
  let cut = body.length > max ? cutAtWord(body, max) : body
  if (preCut) {
    // A partial run of marks at the cut is no closer: drop it, and close what it would have.
    cut = cut.trimEnd().replace(/[`*_~]+$/, '').trimEnd()
  } else {
    cut = keepLiteralOpeners(dropBareMarker(dropPartialRun(cut)), body)
  }
  const { text, fenced } = closeOpenMarks(dropCutLink(cut))
  return fenced ? `${text}\n\n${ELLIPSIS}` : `${text}${ELLIPSIS}`
}
