import { describe, it, expect } from 'vitest'
import { hydrateTurns, type HistMsg, type ToolSegment } from './chatTypes'
import { iconForTool } from './toolRenderers/native'

// ── AAP-8 §2.5 gap 7, the `tool_kind` half — the READER side ────────────────────
//
// The backend now persists the CLI-declared kind on a tool row's `meta.kind`
// (`chat_runner.py`, beside the `tool_call` WS broadcast that already carried it). This
// file pins the other half of that seam, and it is a genuinely separate half: before
// this change `HistMsg.meta` declared no `kind` key at all and `hydrateTurns` never set
// `ToolSegment.toolKind`, so `toolKind` was populated on exactly one of the two paths
// that build a tool card — the live socket (`ChatPage.tsx`) — and was `undefined` on
// every reload. Writing the backend key without this would have been a value nothing
// reads.
//
// What reads it: `iconForTool` resolves an icon by explicit native name → declared ACP
// kind (`_BY_KIND`) → keyword regex over the CLI's prose title. So the observable
// consequence of the missing field was that a reloaded ACP card was iconned by its
// title's *wording*, and that rung grades the honest provider worst — kiro's truthful
// `Running: pwd` and codex's mislabelled `Read file '…'` for the same shell command land
// on different icons (`G34`, in the other direction).

/** A persisted tool row, in the shape `_prepare_messages` puts on the wire. */
const toolRow = (meta: Record<string, unknown>): HistMsg => ({
  role: 'tool', content: 'Edit src/a.py', meta: meta as HistMsg['meta'],
})

const firstTool = (msgs: HistMsg[]): ToolSegment => {
  const segs = hydrateTurns(msgs).flatMap((t) => t.segments)
  const tool = segs.find((s): s is ToolSegment => s.kind === 'tool')
  if (!tool) throw new Error('hydrateTurns produced no tool segment')
  return tool
}

// One row per provider, carrying the kind each CLI actually declares. Kept as three
// separate cases rather than one loop-free assertion because the row this fixes read
// "holds on 1 of 3 providers" — a single-shape test is how that gets written.
const DECLARED: Record<string, string> = {
  claude: 'edit',    // claude-agent-acp's per-tool table: Write/Edit → "edit"
  codex: 'read',     // codex-acp declares kind on the frame
  kiro: 'execute',   // kiro-cli, native ACP, no adapter
}

describe('a persisted tool row rehydrates the declared kind', () => {
  for (const [provider, kind] of Object.entries(DECLARED)) {
    it(`carries ${provider}'s declared kind onto the segment`, () => {
      const seg = firstTool([toolRow({ tool_call_id: 't1', input: '{"a":1}', kind })])
      expect(seg.toolKind).toBe(kind)
    })
  }

  it('lets the declared kind pick the icon instead of the title regex', () => {
    // The whole point, end to end. Both rows have a title that the regex fallback reads
    // as a FILE READ (`/read|cat|view|open/`), while the CLI declared `execute`. Before
    // this change the reloaded card showed the read icon; now the declaration wins.
    const declared = firstTool([toolRow({ tool_call_id: 't1', input: '{}', kind: 'execute' })])
    const undeclared = firstTool([toolRow({ tool_call_id: 't1', input: '{}' })])
    const titled = { ...declared, tool: 'Read file probe.txt' }
    expect(iconForTool(titled)).not.toBe(iconForTool({ ...undeclared, tool: 'Read file probe.txt' }))
  })

  it('leaves toolKind undefined when the row declared none', () => {
    // Vacuity floor. The native runtime declares no kind and the backend omits the key
    // rather than persisting `""`, so the segment must stay undefined and let
    // `iconForTool`'s native-name rung decide — not fall into `_BY_KIND[""]`.
    const seg = firstTool([toolRow({ tool_call_id: 't1', input: '{}' })])
    expect(seg.toolKind).toBeUndefined()
  })

  it('a later row for the same call refines the kind but never erases it', () => {
    // Mirrors the backend's own correlation rule (`SeenToolCall` / `replace`): only a
    // POSITIVE declaration overwrites, so a result row that omits the kind must not
    // blank the one the opening row declared.
    const opened = toolRow({ tool_call_id: 't1', input: '{}', kind: 'edit' })
    const resulted = toolRow({ tool_call_id: 't1', output: 'done', done: true })
    expect(firstTool([opened, resulted]).toolKind).toBe('edit')
  })

  it('a later row MAY refine the kind when it declares one', () => {
    const opened = toolRow({ tool_call_id: 't1', input: '{}', kind: 'unknown' })
    const refined = toolRow({ tool_call_id: 't1', kind: 'execute', done: true })
    expect(firstTool([opened, refined]).toolKind).toBe('execute')
  })
})
