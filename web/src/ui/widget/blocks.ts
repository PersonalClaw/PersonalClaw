/** Split a markdown string into a sequence of markdown segments and `<widget>`
 *  segments. Agents emit rich HTML inline via `<widget title="…" slug="…">HTML
 *  </widget>` (a sandboxed-iframe contract — see WidgetFrame). Everything else is
 *  ordinary markdown. Streaming-aware: an unclosed `<widget>` during streaming
 *  yields a provisional segment so the iframe can render progressively.
 *
 *  A tag inside a code region — a fenced block or an inline span — is content, not a
 *  program, and stays in its markdown segment untouched. See the code-region rule
 *  below for what that covers and what it deliberately does not. */

export interface MdSegment { type: 'md'; content: string }
export interface WidgetSegment { type: 'widget'; title: string; slug?: string; html: string; complete: boolean; kind?: string }
export type ContentSegment = MdSegment | WidgetSegment

// The opening tag, attr blob in group 1 (any order of title=/slug=/kind=). Matched
// STICKY so the scanner below can ask "does a tag start exactly HERE" rather than
// "is there one somewhere ahead" — the second question is the whole of #3526.
const OPEN_TAG = /<widget((?:\s+\w+="[^"]*")*)\s*>/y
const CLOSE_TAG = '</widget>'

function attr(attrs: string | undefined, name: string): string | undefined {
  if (!attrs) return undefined
  const m = new RegExp(`\\b${name}="([^"]*)"`).exec(attrs)
  return m ? m[1] : undefined
}
function widgetSeg(attrs: string | undefined, html: string, complete: boolean): WidgetSegment {
  // `kind="react"` selects the React+Babel renderer; default (absent) is the
  // plain HTML widget iframe. The inner body is JSX source for a react widget.
  return { type: 'widget', title: attr(attrs, 'title') || 'Widget', slug: attr(attrs, 'slug'), html: html.trim(), complete, kind: attr(attrs, 'kind') }
}

/* ── The code-region rule (#3526) ───────────────────────────────────────────────
 *
 *  A widget is EXECUTED: its body becomes the document of a sandboxed iframe in the
 *  app's own origin. A fenced code block is the one construct in markdown whose
 *  entire meaning is "do not interpret this", so a widget written inside a fence is
 *  content DESCRIBING a widget — and extracting it inverts that guarantee twice
 *  over. The example runs, and the fence that was meant to show it is left with the
 *  tag cut out from under it (#3526; the orphan fence that produced is what
 *  surfaced #3525). Anything that reaches a transcript can carry one: model output,
 *  tool results, a fetched page, an MCP response, or this repo's own
 *  `visual-output/SKILL.md`, whose ```html example the Skills inspector renders
 *  through `Markdown`.
 *
 *  The rule lives HERE rather than in `Markdown`, because this function feeds three
 *  surfaces — chat, a workflow gate's prompt and a dashboard tile's body (the last
 *  two through `findGenUiBlock`) — and a content-to-execution boundary that holds on
 *  one of three is not a boundary. None of the three wants a widget inside a fence:
 *  chat wants the code shown, a gate prompt paints as plain text in a `<p>`, and a
 *  tile body is machine-authored by `visualize._wrap_widget`, which builds the
 *  envelope itself and never fences it.
 *
 *  What counts as a code region, and what deliberately does NOT:
 *
 *  * FENCED blocks, ``` and ~~~ alike, on CommonMark's rules: up to 3 spaces of
 *    indent, 3+ delimiters, a closer of the same character at least as long with
 *    nothing after it, and an unclosed fence running to the end of the input. The
 *    opening and closing lines are inert too, so an info string that is itself
 *    ```widget cannot smuggle a tag.
 *  * INLINE code spans, within one line: a backtick run closes at the next run of
 *    exactly that length. `<widget title="Title">HTML</widget>` written that way is
 *    how `prompt_snippets/widget-instructions.md` documents the tag to the model.
 *  * INDENTED (4-space) blocks are NOT a code region, deliberately. Indentation is
 *    structure, not a marker: a tile body is pretty-printed HTML where four spaces
 *    mean nesting, and a widget under a list item sits at its content column — so an
 *    indentation rule would silently stop rendering REAL widgets, including on the
 *    two consumers whose input is not markdown at all. Recognizing one correctly
 *    needs the block structure (list content columns, paragraph continuation) that
 *    only a full markdown parser has, and this is a string splitter that also runs
 *    over HTML and over plain text. The residual gap is narrow and stated rather
 *    than papered over: a widget shown by 4-space indentation alone still executes.
 *
 *  A widget's own body is opaque to the rule — the scanner jumps the whole
 *  `<widget>…</widget>` span. Without that, a widget whose HTML contains a line of
 *  three backticks would open a phantom fence and silence every widget after it.
 */

interface Fence { char: string; len: number }

/** The fence a line OPENS, or null. A backtick fence's info string may not contain
 *  a backtick (CommonMark 6.3) — that is what keeps `` ```code`` `` inline from
 *  reading as a block opener. */
function fenceOpenedBy(line: string): Fence | null {
  const m = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line)
  if (!m) return null
  if (m[1][0] === '`' && m[2].includes('`')) return null
  return { char: m[1][0], len: m[1].length }
}

/** Does `line` close `open`? Same character, at least as long, nothing but spaces
 *  after it. A fence that is never closed runs to the end of the input. */
function fenceClosedBy(line: string, open: Fence): boolean {
  const m = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line)
  return !!m && m[1][0] === open.char && m[1].length >= open.len
}

/** Index of a run of EXACTLY `n` backticks in `[from, end)`, or -1. A longer run
 *  does not close a shorter span, so runs are measured rather than counted. */
function backtickRun(raw: string, from: number, end: number, n: number): number {
  let i = from
  while (i < end) {
    if (raw.charCodeAt(i) !== 96) { i++; continue }
    let len = 1
    while (i + len < end && raw.charCodeAt(i + len) === 96) len++
    if (len === n) return i
    i += len
  }
  return -1
}

/** The first EXECUTABLE opening tag in `[from, lineEnd)`, skipping inline code
 *  spans. The tag itself may run past `lineEnd` (attributes are `\s`-separated, so
 *  a tag may be wrapped) — only its START has to be live. */
function liveOpenTag(raw: string, from: number, lineEnd: number): { index: number; end: number; attrs: string } | null {
  let i = from
  while (i < lineEnd) {
    const c = raw.charCodeAt(i)
    if (c === 96) {
      // A backtick run opens a span that closes at the next run of the same length
      // ON THIS LINE. An unmatched run is literal text, so it must not silence the
      // rest of the document — that direction breaks working widgets.
      let n = 1
      while (i + n < lineEnd && raw.charCodeAt(i + n) === 96) n++
      const close = backtickRun(raw, i + n, lineEnd, n)
      i = close === -1 ? i + n : close + n
      continue
    }
    if (c === 60) {
      OPEN_TAG.lastIndex = i
      const m = OPEN_TAG.exec(raw)
      if (m) return { index: i, end: i + m[0].length, attrs: m[1] }
    }
    i++
  }
  return null
}

/** Parse `raw` into ordered md / widget segments. When `streaming`, an unclosed
 *  trailing `<widget>` becomes a provisional (complete:false) segment. */
export function parseWidgetBlocks(raw: string, streaming = false): ContentSegment[] {
  if (!raw.includes('<widget')) return raw.trim() ? [{ type: 'md', content: raw }] : []

  const out: ContentSegment[] = []
  let mdStart = 0
  let pos = 0
  // Fence state is only ever read at a line start, so the cursor tracks whether it
  // is at one: after a widget it resumes mid-line, where no fence can open.
  let atLineStart = true
  let fence: Fence | null = null

  while (pos < raw.length) {
    const nl = raw.indexOf('\n', pos)
    const lineEnd = nl === -1 ? raw.length : nl
    const nextLine = nl === -1 ? raw.length : nl + 1

    if (atLineStart) {
      const line = raw.slice(pos, lineEnd).replace(/\r$/, '')
      if (fence) {
        if (fenceClosedBy(line, fence)) fence = null
        pos = nextLine
        continue
      }
      const opened = fenceOpenedBy(line)
      if (opened) { fence = opened; pos = nextLine; continue }
    }

    const hit = liveOpenTag(raw, pos, lineEnd)
    if (!hit) { pos = nextLine; atLineStart = true; continue }

    const closeAt = raw.indexOf(CLOSE_TAG, hit.end)
    if (closeAt === -1) {
      // A trailing, not-yet-closed <widget> while streaming → provisional segment so
      // the iframe paints progressively. Not streaming, there is no close tag ahead
      // at all, so no later tag can complete either: the rest is markdown.
      if (streaming) {
        pushMd(out, raw.slice(mdStart, hit.index))
        out.push(widgetSeg(hit.attrs, raw.slice(hit.end), false))
        return out
      }
      break
    }
    pushMd(out, raw.slice(mdStart, hit.index))
    out.push(widgetSeg(hit.attrs, raw.slice(hit.end, closeAt), true))
    mdStart = closeAt + CLOSE_TAG.length
    pos = mdStart
    atLineStart = false
  }

  pushMd(out, raw.slice(mdStart))
  return out
}

function pushMd(out: ContentSegment[], content: string): void {
  if (content.trim()) out.push({ type: 'md', content })
}

/** The first COMPLETE `<widget kind="genui">` block in `raw`, or null.
 *
 *  The shared detector for every non-chat genui HOST (a workflow gate's prompt, a
 *  dashboard tile's rendered body — AMBIENT-SURFACES §5.4). Those surfaces are not
 *  markdown-rendered, so they cannot pick the block up through `Markdown`'s embed
 *  dispatch; they ask HERE instead of each re-deriving "is this a genui payload",
 *  which is how one surface ends up recognizing a block another one renders as text.
 *
 *  Incomplete (still-streaming) blocks are skipped deliberately: a gate prompt and a
 *  tile body are FINISHED artifacts by the time a host paints them, so a half-parsed
 *  tree there means malformed input, not progress. */
export function findGenUiBlock(raw: string): WidgetSegment | null {
  if (!raw) return null
  for (const seg of parseWidgetBlocks(raw)) {
    if (seg.type === 'widget' && seg.kind === 'genui' && seg.complete) return seg
  }
  return null
}

/** The non-widget text of `raw`, joined — the prose around a genui block. */
export function widgetlessText(raw: string): string {
  if (!raw) return ''
  return parseWidgetBlocks(raw)
    .filter((s): s is MdSegment => s.type === 'md')
    .map((s) => s.content.trim())
    .filter(Boolean)
    .join('\n\n')
}
