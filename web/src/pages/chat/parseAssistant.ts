/** Post-processing of assistant text for the chat UI:
 *  - trailing `[OPTIONS: A | B | C]` → stripped from the prose (see below)
 *  - absolute file paths → clickable file references (open in a side panel)
 *  Kept pure + tested-shaped so the render layer stays simple. */

/** The retired `[OPTIONS: …]` marker's EXACT shape — ONE contract, implemented on
 *  both sides of the wire. Kept CHARACTER-IDENTICAL to the backend stripper's pattern
 *  (`src/personalclaw/textfmt.py` `_OPTIONS_PATTERN`); `parseAssistant.test.ts` reads
 *  that file and asserts the two strings match, so the two implementations of one
 *  contract cannot silently drift apart again (#540). Both languages agree on this
 *  source text: `\s*$` absorbs a trailing newline, so Python's `$`-without-MULTILINE
 *  and JS's `$`-without-`m` accept exactly the same inputs.
 *
 *  Deliberately NARROW — it matches only what the retired emitter actually produced:
 *   • literal uppercase `OPTIONS`, PLURAL. The old `/\[\s*OPTIONS?\s*:…\]\s*$/i` was
 *     three widenings past that (`/i`, `OPTIONS?`, loose inner spacing) and ate
 *     ordinary prose: "Configure it via [options: verbose | quiet]" rendered as
 *     "Configure it via", and "See the CLI flags [OPTION: --json]" lost its clause.
 *   • labels on ONE line — `[ \t]*` after the colon (not `\s*`) and `[^\]\n]+` for the
 *     labels — so a bracketed phrase that WRAPS ("See [OPTIONS:\nnot a marker]") is not
 *     swallowed. The retired emitter always put the marker on one line.
 *   • anchored at the end of the INPUT, so text is only ever removed from the tail
 *     and nothing following a match can be deleted. */
export const OPTIONS_PATTERN = '\\[OPTIONS:[ \\t]*([^\\]\\n]+)\\]\\s*$'

/** Pull the trailing `[OPTIONS: a | b | c]` marker(s) off the text. Returns the
 *  cleaned body + the option labels (empty if none).
 *
 *  This is a STRIPPER, not a suggestion source. The legacy `[OPTIONS: …]`
 *  mechanism is retired — nothing instructs the model to emit the marker and the
 *  chat UI never renders `options` as buttons (follow-up chips, driven by the
 *  `chat_followups` event, are the single suggestion surface). Historical
 *  messages persisted before the retirement still carry the marker, so the
 *  render path keeps calling this to keep a raw `[OPTIONS: …]` string out of the
 *  prose. `options` remains in the return shape for that parse.
 *
 *  STACKED trailing markers are all stripped, so the function is idempotent and a
 *  raw marker can never leak into the UI — the one thing the docstring above says it
 *  prevents. A single anchored match left the earlier marker of
 *  `"Answer.\n[OPTIONS: A | B]\n[OPTIONS: C | D]"` in the rendered body. `options`
 *  reports the LAST (operative) marker's labels. */
export function parseOptions(text: string): { body: string; options: string[] } {
  const re = new RegExp(OPTIONS_PATTERN)
  let body = text
  let options: string[] = []
  for (;;) {
    const m = body.match(re)
    if (!m || m.index === undefined) break
    if (options.length === 0) options = m[1].split('|').map((s) => s.trim()).filter(Boolean)
    // Each pass strictly shortens `body` (the match is non-empty), so this terminates.
    body = body.slice(0, m.index).trimEnd()
  }
  return { body, options }
}

/** Pull a trailing `[SWITCH_TO_AGENT: <continuation>]` marker off the text.
 *  The model emits this from a restricted mode (Ask/Plan/Build) to OFFER a
 *  one-click escalation: the UI renders a primary button that flips the session
 *  to Agent mode and resends the continuation. Returns the cleaned body + the
 *  continuation text (empty string if the marker is present but bare). `switchTo`
 *  is null when there's no marker. */
export function parseSwitchToAgent(text: string): { body: string; switchTo: string | null } {
  const re = /\[\s*SWITCH_TO_AGENT\s*:?\s*([^\]]*)\]\s*$/i
  const m = text.match(re)
  if (!m) return { body: text, switchTo: null }
  return { body: text.slice(0, m.index).trimEnd(), switchTo: m[1].trim() }
}

// Absolute-ish file paths: /a/b/c.ext or ~/a/b.ext or workspace-relative a/b.ext
// with a file extension. Conservative to avoid matching prose.
const FILE_RE = /(?:^|[\s(`'"])((?:~|\/)[\w./\-]+\.\w{1,8}|[\w./\-]+\/[\w./\-]+\.\w{1,8})/g

export interface TextPart { kind: 'text' | 'file'; value: string }

/** Split a line of text into text + file-path parts so paths can render as
 *  clickable chips. Only splits paths that look like real files. */
export function splitFileRefs(text: string): TextPart[] {
  const parts: TextPart[] = []
  let last = 0
  for (const m of text.matchAll(FILE_RE)) {
    const path = m[1]
    const start = m.index! + m[0].indexOf(path)
    if (start > last) parts.push({ kind: 'text', value: text.slice(last, start) })
    parts.push({ kind: 'file', value: path })
    last = start + path.length
  }
  if (last < text.length) parts.push({ kind: 'text', value: text.slice(last) })
  return parts.length ? parts : [{ kind: 'text', value: text }]
}
