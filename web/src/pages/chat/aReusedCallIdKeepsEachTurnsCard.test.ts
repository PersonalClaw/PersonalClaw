import { describe, it, expect } from 'vitest'
import { hydrateTurns, type ApprovalSegment, type HistMsg, type ToolSegment } from './chatTypes'
import { resolveApprovalIn } from './liveToolFrames'

// ── A call id a model hands out again keeps each turn's own card ─────────────────────────────────
//
// Measured before this was written (dogfood, a scratch twin of the prototype on a local
// Ollama-shaped model): three turns each ran one tool, and live each showed its card under
// "Worked for 7s · 1 step". After a reload only the first turn kept its card. Some providers name
// each answer's calls from zero (Ollama's adapter `call-{idx}`, the OpenAI adapter when the server
// gives no id), so every answer's first call is `call-0`. The reload kept one index of cards by call
// id across the whole transcript, so the later turns' rows merged into the first turn's card: its
// output was overwritten, and the later turns drew none.
//
// Now a reload reads a call's id within the answer it belongs to, as the live page does (its card
// reducers patch the answer at the end). A call's later rows in the same answer still refine its
// one card.

const ran = (n: number): HistMsg[] => [
  { role: 'user', content: `List folder ${n}.`, ts: `2026-11-21T09:0${n}:00+00:00` },
  { role: 'assistant', content: `Looking in folder ${n}.` },
  { role: 'tool', content: 'list_dir', meta: { tool_call_id: 'call-0', input: `{"path": "f${n}"}`, done: true, output: `files of ${n}` } },
  { role: 'assistant', content: `Folder ${n} holds two files.` },
]

const toolsOf = (turn: { segments: unknown[] }) =>
  (turn.segments as { kind: string }[]).filter((s): s is ToolSegment => s.kind === 'tool')

describe('a transcript whose model numbers each answer’s calls from zero', () => {
  it('🔴 draws every turn’s own card after a reload, with its own output', () => {
    const turns = hydrateTurns([...ran(1), ...ran(2), ...ran(3)])
    const answers = turns.filter((t) => t.role === 'assistant')

    expect(answers.map((t) => toolsOf(t).map((s) => s.output))).toEqual([['files of 1'], ['files of 2'], ['files of 3']])
  })

  it('still folds a call’s later rows in the same answer into its one card', () => {
    const turns = hydrateTurns([
      ...ran(1),
      { role: 'user', content: 'Again.' },
      { role: 'tool', content: 'list_dir', meta: { tool_call_id: 'call-7', input: '{"path": "g"}' } },
      { role: 'tool', content: 'list_dir', meta: { tool_call_id: 'call-7', done: true, output: 'files of g' } },
    ])
    const last = turns[turns.length - 1]

    expect(toolsOf(last).map((s) => [s.input, s.output, s.done])).toEqual([['{"path": "g"}', 'files of g', true]])
  })
})

describe('an approval answered live under an id an earlier turn used', () => {
  it('🔴 ends the card it answers, not the earlier turn’s', () => {
    const earlier: ApprovalSegment = { kind: 'approval', id: 'call-0', tool: 'edit_file', resolved: 'approved' } as ApprovalSegment
    const asking: ApprovalSegment = { kind: 'approval', id: 'call-0', tool: 'edit_file' } as ApprovalSegment
    const turns = [
      { role: 'assistant' as const, segments: [earlier] },
      { role: 'user' as const, segments: [] },
      { role: 'assistant' as const, segments: [asking] },
    ]

    const after = resolveApprovalIn(turns, { request_id: 'call-0', outcome: 'rejected' })

    expect(after.map((t) => (t.segments[0] as ApprovalSegment | undefined)?.resolved)).toEqual(['approved', undefined, 'rejected'])
  })
})
