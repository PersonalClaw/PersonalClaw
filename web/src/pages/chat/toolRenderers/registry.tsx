/** Tool I/O render registry (tool-io-rendering, TC1).
 *
 * ONE registry that renders a tool call's INPUT and OUTPUT by type, with rich
 * per-tool OVERRIDES for native tools (predictable schemas), a schema-driven
 * default for any tool, content-type output renderers, and a raw-text fallback
 * that is always correct. Every renderer is error-boundaried to the raw fallback
 * (a throwing renderer must never blank the card) — rich rendering is a
 * progressive enhancement over the always-right raw text.
 *
 * Selection order:
 *   input:  native override (by tool name) → schema-driven fields (from inputObj)
 *           → raw <pre> of the string input
 *   output: native override (by tool name) → content_type renderer
 *           → sniff (reuse ToolOutput) → raw <pre>
 */
import { type ReactNode } from 'react'
import type { ToolSegment } from '../chatTypes'
import { ToolOutput } from '../../tools/ToolOutput'
import { RawBlock, KeyValueFields, ContentTypeOutput } from './primitives'
import { NATIVE_RENDERERS } from './native'

/** A native per-tool override: either or both of input/output renderers. */
export interface ToolRenderer {
  /** Match by exact tool name or a predicate over the (lowercased) name. */
  match: (toolName: string) => boolean
  input?: (seg: ToolSegment) => ReactNode
  output?: (seg: ToolSegment) => ReactNode
}

function findNative(seg: ToolSegment): ToolRenderer | undefined {
  const name = (seg.tool || '').toLowerCase()
  return NATIVE_RENDERERS.find((r) => r.match(name))
}

/** Resolve the structured input OBJECT for a segment, from either the live
 *  `inputObj` (native frames) OR by parsing a JSON-string `input` (the common
 *  case: persisted history, ACP, and the `input_preview` string). Returns null
 *  when there's no object to render (scalar/empty/non-JSON string). This is what
 *  makes schema-driven field rendering work for OLD sessions + ACP, not just
 *  freshly-streamed native frames — the gap that left raw JSON in old cards. */
export function resolveInputObj(seg: ToolSegment): Record<string, unknown> | null {
  if (seg.inputObj && typeof seg.inputObj === 'object' && !Array.isArray(seg.inputObj)) {
    return seg.inputObj as Record<string, unknown>
  }
  const raw = (seg.input ?? '').trim()
  if (raw.startsWith('{')) {
    try {
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, unknown>
    } catch { /* not JSON → fall through */ }
  }
  return null
}

/** Render the tool's INPUT region (everything but the raw fallback is optional). */
export function renderToolInput(seg: ToolSegment): ReactNode {
  // Make a JSON-string input behave like a structured object for the override +
  // schema-driven paths (history/ACP carry input as a string).
  const obj = resolveInputObj(seg)
  const effective: ToolSegment = obj && !seg.inputObj ? { ...seg, inputObj: obj } : seg
  // 1. native override
  const native = findNative(effective)
  if (native?.input) {
    const node = safe(() => native.input!(effective))
    if (node !== undefined) return node
  }
  // 2. schema-driven: render the structured input object as labeled fields
  if (obj) {
    const node = safe(() => <KeyValueFields obj={obj} />)
    if (node !== undefined) return node
  }
  // 3. raw fallback (a non-JSON string input, e.g. a bash command on ACP)
  if (seg.input) return <RawBlock label="Input">{seg.input}</RawBlock>
  return null
}

/** Render the tool's OUTPUT region. */
export function renderToolOutput(seg: ToolSegment): ReactNode {
  if (seg.output == null || seg.output === '') return null
  // 1. native override
  const native = findNative(seg)
  if (native?.output) {
    const node = safe(() => native.output!(seg))
    if (node !== undefined) return node
  }
  // 2. content-type renderer. Prefer the BACKEND-declared type; if absent (the
  //    common case for web tools, ACP frames, and pre-projection history — none
  //    of which carry content_type), SNIFF it client-side so a reloaded/external
  //    diff/log/json/csv still gets its rich view instead of degrading to plain
  //    sniff. This is the fix for "old sessions render worse than live".
  const ct = (seg.contentType && seg.contentType !== 'generic')
    ? seg.contentType
    : sniffContentType(seg.output)
  if (ct && ct !== 'generic') {
    const eff: ToolSegment = ct === seg.contentType ? seg : { ...seg, contentType: ct }
    const node = safe(() => <ContentTypeOutput seg={eff} />)
    if (node !== undefined) return node
  }
  // 3. sniff (reuse the existing ToolOutput: json tree/table, markdown, raw)
  const node = safe(() => <RawBlock label="Result"><ToolOutput text={seg.output!} /></RawBlock>)
  if (node !== undefined) return node
  // 4. absolute fallback
  return <RawBlock label="Result">{seg.output}</RawBlock>
}

/** Client-side content-type sniff — a faithful mirror of the backend
 *  `projection.infer_content_type` (diff/test/json/csv markers, conservative:
 *  anything ambiguous → 'generic'). Used only when the backend didn't DECLARE a
 *  content_type, so live native frames (which do) are unaffected and old/ACP
 *  cards stop falling through to plain text. */
const _DIFF_RE = /^(diff --git |@@ -\d|index [0-9a-f]+\.\.|\+\+\+ |--- )/m
const _TEST_RE = /\b(PASSED|FAILED|\d+ passed|\d+ failed|=+ test session|FAIL\b|AssertionError)\b/
export function sniffContentType(text: string | undefined): string {
  const s = (text ?? '').slice(0, 4096)
  if (!s.trim()) return 'generic'
  if (_DIFF_RE.test(s)) return 'diff'
  if (_TEST_RE.test(s)) return 'test'
  if (/^\s*[[{]/.test(s)) {
    try { JSON.parse((text ?? '').trim()); return 'json' } catch { /* not json */ }
  }
  if (looksCsv(s)) return 'csv'
  return 'generic'
}
function looksCsv(sample: string): boolean {
  const lines = sample.split('\n').filter((l) => l.trim()).slice(0, 5)
  if (lines.length < 2) return false
  const counts = lines.map((l) => (l.match(/,/g) || []).length)
  return counts[0] >= 1 && new Set(counts).size === 1
}

/** Run a renderer; on throw, return undefined so the caller falls through to the
 *  next (ultimately raw) renderer. Never lets a renderer error blank the card. */
function safe(fn: () => ReactNode): ReactNode | undefined {
  try {
    return fn()
  } catch {
    return undefined
  }
}

export { iconForTool, labelForTool } from './native'
