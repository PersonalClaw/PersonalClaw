import { describe, it, expect } from 'vitest'
import { hydrateTurns, type HistMsg, type ToolSegment } from './chatTypes'

// ── A reloaded tool card renders the input object its row keeps (#5729) ──────────────────────────
//
// A tool row kept its call's input only as a preview string cut at 4,000 characters, so a call with
// a long command was rebuilt after a reload from JSON cut off mid-string. The row now keeps the
// object the live card rendered (`meta.input_obj`), and a reload hands it to the card as the live
// frame did (`inputObj`), with the string still its preview.

const ASKED: HistMsg = { role: 'user', content: 'Run the migration', ts: '2026-10-30T09:00:00+00:00' }
const INPUT = { command: "set -e\necho 'step 00001 of the migration'\n", timeout: 120 }

function toolOf(rows: HistMsg[]): ToolSegment {
  const turns = hydrateTurns([ASKED, ...rows, { role: 'assistant', content: 'Migrated.' }])
  const tools = turns.flatMap((t) => t.segments).filter((s): s is ToolSegment => s.kind === 'tool')
  expect(tools).toHaveLength(1)
  return tools[0]
}

describe('a reloaded tool card', () => {
  it('renders the input object its row keeps, with the string as its preview', () => {
    const seg = toolOf([{
      role: 'tool', content: 'bash',
      meta: { tool_call_id: 'tc-1', input: '{"command": "set -e\\necho \'step 0', input_obj: INPUT, done: true, output: 'ok' },
    }])
    expect(seg.inputObj).toEqual(INPUT)
    expect(seg.input).toBe('{"command": "set -e\\necho \'step 0')
  })

  it('takes the object a later row of the same call carries', () => {
    const seg = toolOf([
      { role: 'tool', content: 'bash', meta: { tool_call_id: 'tc-1', input: '{}' } },
      { role: 'tool', content: 'bash', meta: { tool_call_id: 'tc-1', input_obj: INPUT, done: true, output: 'ok' } },
    ])
    expect(seg.inputObj).toEqual(INPUT)
  })

  it('has no object for a row that kept none, as before', () => {
    const seg = toolOf([{ role: 'tool', content: 'Terminal', meta: { tool_call_id: 'tc-1', input: 'ls -la', done: true, output: 'ok' } }])
    expect(seg.inputObj).toBeUndefined()
    expect(seg.input).toBe('ls -la')
  })
})
