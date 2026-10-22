import type { ChatTurn, Segment, ToolSegment } from '../chatTypes'
import { coreToolName } from '../coreToolName'

// ── What a chat made (#3820's session inspector, Outputs) ──────────────────────────────────────
//
// The artifacts the chat's calls saved and the files they wrote, newest first, read from the calls'
// own recorded inputs and results. Each opens in the detail tile beside the thread, under its
// address (`artifact:<slug>`, `file:<path>`), so a reload and a copied link open the same one.

export type ChatOutput =
  | { kind: 'artifact'; slug: string; name: string; address: string }
  | { kind: 'file'; path: string; name: string; address: string }

/** The sentence an artifact tool's result opens with (`mcp_artifacts.py`): "Saved artifact
 *  'Plan' (slug: plan-1, …" or "… as its next version (slug: …". */
const SAVED_ARTIFACT = /Saved artifact '([^']*)'(?: as its next version)? \(slug: ([\w.-]+)/

/** The native agent's file writers. An agent CLI's call says it edits by its kind. */
const FILE_WRITERS = new Set(['write_file', 'edit_file'])

const asObj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
function inputOf(seg: ToolSegment): Record<string, unknown> {
  if (seg.inputObj && typeof seg.inputObj === 'object') return asObj(seg.inputObj)
  try { return asObj(JSON.parse(seg.input ?? '')) } catch { return {} }
}
const baseName = (p: string) => p.replace(/\/+$/, '').split('/').pop() || p

/** What one finished call made, if anything. */
function outputOf(seg: ToolSegment): ChatOutput | null {
  if (!seg.done || seg.ok === false) return null
  const saved = SAVED_ARTIFACT.exec(seg.output ?? '')
  if (saved) return { kind: 'artifact', slug: saved[2], name: saved[1] || saved[2], address: `artifact:${saved[2]}` }
  const writes = FILE_WRITERS.has(coreToolName(seg.tool) ?? '') || seg.toolKind === 'edit'
  if (!writes) return null
  const input = inputOf(seg)
  const path = String(input.path ?? input.file_path ?? '').trim()
  return path ? { kind: 'file', path, name: baseName(path), address: `file:${path}` } : null
}

/** The artifacts a turn's own calls saved, each once, in the order the turn saved them: the cards
 *  under its answer (`transcript/TurnArtifacts`). */
export function turnArtifacts(segments: Segment[]): Extract<ChatOutput, { kind: 'artifact' }>[] {
  const seen = new Map<string, Extract<ChatOutput, { kind: 'artifact' }>>()
  for (const seg of segments) {
    if (seg.kind !== 'tool') continue
    const out = outputOf(seg as ToolSegment)
    if (out?.kind === 'artifact') seen.set(out.slug, out)
  }
  return [...seen.values()]
}

/** The chat's outputs, each once (an artifact by its slug, a file by its path), the latest first. */
export function chatOutputs(turns: ChatTurn[]): ChatOutput[] {
  const seen = new Map<string, ChatOutput>()
  for (const t of turns) {
    if (t.role === 'user') continue
    for (const seg of t.segments) {
      if (seg.kind !== 'tool') continue
      const out = outputOf(seg as ToolSegment)
      if (!out) continue
      // A later save of the same thing moves it to the front, under its latest name.
      seen.delete(out.address)
      seen.set(out.address, out)
    }
  }
  return [...seen.values()].reverse()
}
