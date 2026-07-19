/** Post-processing of assistant text for the chat UI:
 *  - trailing `[OPTIONS: A | B | C]` → one-click suggestion chips
 *  - absolute file paths → clickable file references (open in a side panel)
 *  Kept pure + tested-shaped so the render layer stays simple. */

/** Pull a trailing `[OPTIONS: a | b | c]` (case-insensitive) off the text.
 *  Returns the cleaned text + the option labels (empty if none). */
export function parseOptions(text: string): { body: string; options: string[] } {
  // match a bracketed OPTIONS block, ideally near the end
  const re = /\[\s*OPTIONS?\s*:\s*([^\]]+)\]\s*$/i
  const m = text.match(re)
  if (!m) return { body: text, options: [] }
  const options = m[1].split('|').map((s) => s.trim()).filter(Boolean)
  return { body: text.slice(0, m.index).trimEnd(), options }
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
