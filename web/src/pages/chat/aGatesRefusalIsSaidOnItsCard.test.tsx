import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { foldStepLine, hydrateTurns, type HistMsg, type Segment, type ToolSegment } from './chatTypes'
import { applyToolCallFrame } from './liveToolFrames'
import { ToolCard } from './ToolCard'

// ── What the gateway says about a call goes on that call's card ─────────────────────────────────
//
// A gate that refuses a call before it runs (the chat's task mode, the shell denylist, a hook, an
// unattended run's bounds) and the loop breaker that sees a call keep failing each write a line
// about the call. Those lines were persisted with the turn and drawn nowhere: a line without a
// call id folds into nothing. Now each names the call it is about (`meta.about_call`) and carries
// its sentence (`meta.note`), and the sentence is said on that call's card, live (the row's
// `chat_message` frame) and after a reload (the row) through the one fold.

const ASKED: HistMsg = { role: 'user', content: 'Tidy the notes folder.' }
const NOT_RUN = 'Not run: Ask mode — only read-only tools run (switch to Agent to make changes).'
const FAILING = 'Failed 3 times in a row with the same arguments.'

const call = (id: string, tool: string, meta: HistMsg['meta'] = {}): HistMsg =>
  ({ role: 'tool', content: tool, meta: { tool_call_id: id, done: true, ...meta } })
const noteOn = (id: string, title: string, note: string): HistMsg =>
  ({ role: 'tool', content: `${title} — ${note}`, meta: { about_call: id, note } })
const steps = (messages: HistMsg[]): Segment[] =>
  hydrateTurns(messages).flatMap((t) => (t.role === 'assistant' ? t.segments : []))

describe('a line about a call, after a reload', () => {
  it('puts a refused call’s note on its card, and is no step of its own', () => {
    const segs = steps([
      ASKED,
      call('tc1', 'Write File', { kind: 'edit', detail: 'notes.md', ok: false }),
      noteOn('tc1', 'Write File', NOT_RUN),
      call('tc2', 'Terminal', { kind: 'execute', detail: 'make test', ok: false }),
      noteOn('tc2', 'Terminal', FAILING),
      { role: 'assistant', content: 'I could not write the file in Ask mode.' },
    ])
    expect(segs.map((s) => s.kind)).toEqual(['tool', 'tool', 'text'])
    expect(segs[0]).toMatchObject({ id: 'tc1', notes: [NOT_RUN] })
    expect(segs[1]).toMatchObject({ id: 'tc2', notes: [FAILING] })
  })

  it('says a line about a call the turn shows no card for on the turn, naming the call', () => {
    const segs = steps([
      ASKED,
      { role: 'tool', content: `Write File — ${NOT_RUN}`, meta: { note: NOT_RUN } },
      { role: 'assistant', content: 'Ask mode only reads.' },
    ])
    expect(segs.map((s) => s.kind)).toEqual(['activity', 'text'])
    expect(segs[0]).toMatchObject({ kind: 'activity', text: `Write File — ${NOT_RUN}` })
  })

  it('still folds a line written before notes were into nothing', () => {
    const segs = steps([
      ASKED,
      call('tc1', 'Write File', { kind: 'edit' }),
      { role: 'tool', content: 'Write File (hook blocked: deny-writes:no writes today)' },
    ])
    expect(segs.map((s) => s.kind)).toEqual(['tool'])
    expect((segs[0] as ToolSegment).notes).toBeUndefined()
  })

  it('reads what fed the turn back into its footer', () => {
    const fed = 'Injected 1,204 chars of context (memory, lessons, history, episodic)'
    const segs = steps([ASKED, { role: 'assistant', content: 'Done.', meta: { context_fed: { kind: 'context', text: fed } } }])
    expect(segs).toContainEqual({ kind: 'activity', text: fed, activityKind: 'context' })
  })

  it('reads a turn that read none of your memory back as that, never as memory fed', () => {
    const fed = 'Injected 312 chars of context, none of it from your memory: this is a Temporary chat'
    const segs = steps([ASKED, { role: 'assistant', content: 'Done.', meta: { context_fed: { kind: 'context_without_memory', text: fed } } }])
    expect(segs).toContainEqual({ kind: 'activity', text: fed, activityKind: 'context_without_memory' })
  })
})

describe('a line about a call, live', () => {
  it('lands on the card already shown, once however often its frame arrives', () => {
    let segs: Segment[] = applyToolCallFrame([], { tool_call_id: 'tc1', tool: 'Write File', kind: 'edit' })
    const meta = { about_call: 'tc1', note: NOT_RUN }
    segs = foldStepLine(segs, meta, `Write File — ${NOT_RUN}`)
    segs = foldStepLine(segs, meta, `Write File — ${NOT_RUN}`)
    expect(segs).toHaveLength(1)
    expect(segs[0]).toMatchObject({ kind: 'tool', id: 'tc1', notes: [NOT_RUN] })
  })

  it('goes on the turn when its call has no card, once', () => {
    const line = `Write File — ${NOT_RUN}`
    let segs: Segment[] = []
    segs = foldStepLine(segs, { about_call: 'tc9', note: NOT_RUN }, line)
    segs = foldStepLine(segs, { about_call: 'tc9', note: NOT_RUN }, line)
    expect(segs).toEqual([{ kind: 'activity', text: line, activityKind: 'notice' }])
  })
})

describe('the card', () => {
  it('says the note closed as it is, and in its name', () => {
    const seg: ToolSegment = { kind: 'tool', id: 'tc1', tool: 'Terminal', detail: 'make test', done: true, ok: false, notes: [FAILING] }
    render(<ToolCard seg={seg} />)
    expect(screen.getByText(FAILING)).toBeTruthy()
    const card = screen.getByRole('button', { name: 'Tool Terminal make test — failed, failed 3 times in a row with the same arguments. Expand details' })
    expect(card.getAttribute('aria-expanded')).toBe('false')
  })

  it('a call nothing was said about reads as it did', () => {
    const seg: ToolSegment = { kind: 'tool', id: 'tc2', tool: 'Terminal', detail: 'make test', done: true }
    render(<ToolCard seg={seg} />)
    expect(screen.getByRole('button', { name: 'Tool Terminal make test — completed. Expand details' })).toBeTruthy()
  })
})
