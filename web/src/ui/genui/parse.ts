/** The genui DSL parser (AMBIENT-SURFACES §5.2) — PURE, no React.
 *
 *  A `<widget kind="genui">` body is a line-oriented DSL, one component per line:
 *
 *      id = Component(key: value, key2: [a, b], …)
 *
 *  Top-down generation (structure before data), forward references legal — a
 *  parent line may reference child ids that appear LATER. Values are JSON-ish:
 *  quoted strings, numbers, booleans, and `[…]` arrays (of scalars, or of bare
 *  ids used as child references). This is roughly half the tokens of full JSON
 *  and paints progressively as it streams (the renderer re-parses per chunk).
 *
 *  Parsing is deliberately separate from validation/rendering so it can be unit-
 *  tested in isolation and reused by both the streaming renderer and any tooling.
 *  A malformed line is recorded as a parse error and skipped — never fatal. */

/** One parsed component invocation. `argKeys` preserves author order for the
 *  excess-args check; `refs` names the arg keys whose values are id references. */
export interface ParsedLine {
  /** The `id` to the left of `=` (unique key + reference target). */
  id: string
  /** The `Component` name to the right. */
  component: string
  /** Parsed scalar/array arg values by key (id-reference args are omitted here). */
  args: Record<string, unknown>
  /** Order args were written (drives the excess-args verdict). */
  argKeys: string[]
  /** arg key → referenced line id(s) — resolved to children by the renderer. */
  refs: Record<string, string[]>
  /** The source line number (1-based) for error reporting. */
  line: number
}

export interface ParsedProgram {
  lines: ParsedLine[]
  /** Lines that were syntactically malformed (surfaced, not rendered). */
  parseErrors: { line: number; text: string; message: string }[]
}

/** A bare identifier: a line id or a child reference. Kept strict so a quoted
 *  string is never mistaken for a ref. */
const ID_RE = /^[A-Za-z_][A-Za-z0-9_-]*$/

/** Split the top-level args of `key: value, key2: [a, b]` respecting brackets
 *  and quotes, so a comma inside `[…]` or a string doesn't split an arg. */
function splitArgs(body: string): string[] {
  const out: string[] = []
  let depth = 0
  let quote = ''
  let start = 0
  for (let i = 0; i < body.length; i++) {
    const ch = body[i]
    if (quote) {
      if (ch === quote && body[i - 1] !== '\\') quote = ''
      continue
    }
    if (ch === '"' || ch === "'") quote = ch
    else if (ch === '[' || ch === '(') depth++
    else if (ch === ']' || ch === ')') depth--
    else if (ch === ',' && depth === 0) {
      out.push(body.slice(start, i))
      start = i + 1
    }
  }
  const tail = body.slice(start)
  if (tail.trim()) out.push(tail)
  return out
}

/** Parse a single scalar/array value. Returns `{ refs }` when the value is a bare
 *  id or an array of bare ids (a child reference), else `{ value }`. */
function parseValue(raw: string): { value?: unknown; refs?: string[] } {
  const t = raw.trim()
  if (!t) return { value: '' }
  // Array literal.
  if (t.startsWith('[') && t.endsWith(']')) {
    const inner = t.slice(1, -1).trim()
    if (!inner) return { value: [] }
    const parts = splitArgs(inner).map((p) => p.trim())
    // All-bare-ids → a refs array (children); otherwise a scalar array.
    if (parts.every((p) => ID_RE.test(p))) return { refs: parts }
    return { value: parts.map((p) => parseScalar(p)) }
  }
  // Bare id → a single child reference.
  if (ID_RE.test(t) && t !== 'true' && t !== 'false') return { refs: [t] }
  return { value: parseScalar(t) }
}

/** A single scalar: quoted string, number, or boolean. An unquoted non-number
 *  falls back to its literal text (tolerant — the DSL is model-authored). */
function parseScalar(t: string): unknown {
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
    return t.slice(1, -1).replace(/\\(["'])/g, '$1')
  }
  if (t === 'true') return true
  if (t === 'false') return false
  if (t !== '' && !Number.isNaN(Number(t))) return Number(t)
  return t
}

const LINE_RE = /^([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([\s\S]*)\)\s*$/

/** Parse a genui DSL body into a program. Blank lines and `#`/`//` comments are
 *  ignored; a line that doesn't match the `id = Component(...)` shape is recorded
 *  as a parse error and skipped (never fatal — the rest still renders). */
export function parseGenUi(body: string): ParsedProgram {
  const lines: ParsedLine[] = []
  const parseErrors: ParsedProgram['parseErrors'] = []
  const raw = body.split('\n')
  for (let n = 0; n < raw.length; n++) {
    const text = raw[n].trim()
    if (!text || text.startsWith('#') || text.startsWith('//')) continue
    const m = LINE_RE.exec(text)
    if (!m) {
      parseErrors.push({ line: n + 1, text, message: 'expected `id = Component(args…)`' })
      continue
    }
    const [, id, component, argBlob] = m
    const args: Record<string, unknown> = {}
    const refs: Record<string, string[]> = {}
    const argKeys: string[] = []
    for (const piece of splitArgs(argBlob)) {
      const colon = piece.indexOf(':')
      if (colon < 0) continue
      const key = piece.slice(0, colon).trim()
      if (!key) continue
      argKeys.push(key)
      const parsed = parseValue(piece.slice(colon + 1))
      if (parsed.refs) refs[key] = parsed.refs
      else args[key] = parsed.value
    }
    lines.push({ id, component, args, argKeys, refs, line: n + 1 })
  }
  return { lines, parseErrors }
}
