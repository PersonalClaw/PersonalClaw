import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { foldStepLine, hydrateTurns, type HistMsg, type Segment, type ToolSegment } from './chatTypes'
import { applyToolCallFrame, applyToolResultFrame } from './liveToolFrames'
import { ToolCard } from './ToolCard'

// ── The rows a reload rebuilds a turn from: calls, and lines ABOUT calls ────────────────────────
//
// The gateway persists each call as a `tool` row carrying the call's id, and it also writes
// `tool` rows that are lines about a step: how the step's approval ended ("bash (rejected)"), why
// a gate refused it, a loop-breaker warning. The live page never draws a card for those lines;
// a reload drew each as a step of its own, reading "completed", its words put through the
// tool-name humanizer. A line about a step is not a step: it carries no call id, in a turn the
// gateway ran. An imported conversation's call lines carry no ids either, and are still calls.

const NOTE = "Ran without asking you — allowed by Claude Code's own settings."

const asked: HistMsg = { role: 'user', content: 'Tidy the notes folder.' }
const call = (id: string, tool: string, meta: HistMsg['meta'] = {}): HistMsg =>
  ({ role: 'tool', content: tool, meta: { tool_call_id: id, done: true, ...meta } })
const line = (content: string): HistMsg => ({ role: 'tool', content })
const steps = (messages: HistMsg[]): Segment[] =>
  hydrateTurns(messages).flatMap((t) => (t.role === 'assistant' ? t.segments : []))

describe('rehydrating the rows of a turn the gateway ran', () => {
  it('draws each call once, and no line about a step as a step', () => {
    const segs = steps([
      asked,
      call('tc1', 'Terminal', { kind: 'execute', detail: 'git log --oneline', ungated: NOTE }),
      line('Terminal (ungated: claude-code executed it without asking the host)'),
      line('rm -rf build (blocked: matches a denied command)'),
      line('touch notes.md (Ask mode — only read-only tools run (switch to Agent to make changes))'),
      line('Terminal has failed 3 times the same way; trying something else.'),
      { role: 'assistant', content: 'Done.' },
    ])
    expect(segs.map((s) => s.kind)).toEqual(['tool', 'text'])
    const card = segs[0] as ToolSegment
    expect(card.tool).toBe('Terminal')
    expect(card.ungated).toBe(NOTE)
  })

  it('reads a denied call as its approval, said once, even with no card before it', () => {
    const segs = steps([
      asked,
      { role: 'permission', content: 'bash', meta: { approval_id: 'r1', resolved: 'rejected' } },
      line('bash (rejected)'),
    ])
    expect(segs.map((s) => s.kind)).toEqual(['approval'])
    expect(segs[0]).toMatchObject({ kind: 'approval', tool: 'bash', resolved: 'rejected' })
  })

  it('puts the option a refusal was sent as on its approval’s line, live as after a reload', () => {
    const answered = 'Answered “No, continue without running it”'
    const reloaded = steps([
      asked,
      call('tc1', 'Terminal', { detail: 'git push', ok: false }),
      { role: 'permission', content: 'git push', meta: { approval_id: 'r1', tool_call_id: 'tc1', resolved: 'rejected' } },
      { role: 'tool', content: 'git push (rejected)', meta: { detail: answered } },
    ])
    expect(reloaded.map((s) => s.kind)).toEqual(['tool', 'approval'])
    expect(reloaded[0]).toMatchObject({ kind: 'tool', detail: 'git push' })
    expect(reloaded[1]).toMatchObject({ kind: 'approval', resolved: 'rejected', detail: answered })

    let live: Segment[] = [
      { kind: 'tool', id: 'tc1', tool: 'Terminal', detail: 'git push', done: true, ok: false },
      { kind: 'approval', id: 'r1', tool: 'git push', resolved: 'rejected' },
    ]
    live = foldStepLine(live, { detail: answered })
    expect(live[1]).toMatchObject({ kind: 'approval', detail: answered })
    // A line after a call's card adds nothing to the card: its detail is the command it ran.
    expect(foldStepLine(live.slice(0, 1), { detail: answered })).toEqual(live.slice(0, 1))
  })

  it('still draws an imported conversation’s call lines, which carry no ids at all', () => {
    const segs = steps([
      { role: 'user', content: 'What changed in the fetcher?' },
      line('Bash: git log --oneline -- src/feedsmith/fetch.py'),
      line('Read: src/feedsmith/fetch.py'),
      { role: 'assistant', content: 'The async rewrite.' },
    ])
    expect(segs.filter((s) => s.kind === 'tool')).toHaveLength(2)
  })
})

describe('a call the agent CLI ran without asking her', () => {
  it('is marked on its card live, by an update to the card already shown', () => {
    let segs: Segment[] = []
    segs = applyToolCallFrame(segs, { tool_call_id: 'tc1', tool: 'Read File', kind: 'read', input_preview: '{}' })
    segs = applyToolResultFrame(segs, { tool_call_id: 'tc1', output: '41 lines' })
    segs = applyToolCallFrame(segs, { tool_call_id: 'tc1', tool: 'Read File', ungated: NOTE, update: true })
    expect(segs).toHaveLength(1)
    expect(segs[0]).toMatchObject({ kind: 'tool', tool: 'Read File', done: true, ungated: NOTE })
  })

  it('says so on the card, closed as it is, and in the card’s name', () => {
    const seg: ToolSegment = { kind: 'tool', id: 'tc1', tool: 'Terminal', detail: 'git log --oneline', done: true, ungated: NOTE }
    render(<ToolCard seg={seg} />)
    expect(screen.getByText(NOTE)).toBeTruthy()
    const card = screen.getByRole('button', { name: /^Tool Terminal git log --oneline — completed, ran without asking you/ })
    expect(card.getAttribute('aria-expanded')).toBe('false')
  })

  it('a call she approved reads as it did', () => {
    const seg: ToolSegment = { kind: 'tool', id: 'tc2', tool: 'Terminal', detail: 'git log --oneline', done: true }
    render(<ToolCard seg={seg} />)
    expect(screen.queryByText(/without asking/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Tool Terminal git log --oneline — completed. Expand details' })).toBeTruthy()
  })
})
