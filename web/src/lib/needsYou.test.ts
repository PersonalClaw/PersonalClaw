import { describe, expect, it } from 'vitest'
import type { WaitingItem, WaitingList } from './api'
import { needsYou, waitingTitle, type NoteInput } from './needsYou'

// ── Needs You is the waiting list, in its order, then the Inbox's own asks ─────────────────────────
//
// The ids an answer posts with are read by the one parser every surface shares,
// `waitingAnswer.ts`, and held to its own test.

function waitingItem(over: Partial<WaitingItem> = {}): WaitingItem {
  return {
    id: 'approval:a1', kind: 'approval', work: 'chat:c1', session: 'c1',
    brief: { tool: 'shell.run', tool_purpose: 'List the folder', source_label: 'chat “Release notes”' },
    consequence: 'changes', risk: 'caution', asked_at: '2026-10-21T10:00:00Z', age_secs: 60,
    expires_at: null, current: true, interrupt: 'deliver',
    answer: { method: 'POST', path: '/api/approvals/a1/{answer}', takes: ['approve', 'reject'], body: {} },
    route: '#/chat/c1',
    ...over,
  }
}

const list = (...waiting: WaitingItem[]): WaitingList => ({ waiting, unreadable: [] })

const note = (over: Partial<NoteInput> = {}): NoteInput => ({
  id: 'n1', item_kind: 'user_note', status: 'pending', message: 'Call the bank', context_summary: '',
  created_at: 100, refs: {}, sender_name: '', channel_name: '', ...over,
})

describe('Needs You', () => {
  it('keeps the waiting list in the order it is served, never sorting it again', () => {
    // The list serves the broad-reach new item before the low-risk old one: consequence first.
    const destructive = waitingItem({ id: 'approval:new', consequence: 'destructive', age_secs: 5, answer: { method: 'POST', path: '/api/approvals/new/{answer}', takes: [], body: {} } })
    const old = waitingItem({ id: 'question:old', kind: 'question', consequence: 'asks', age_secs: 9000, brief: { questions: [{ question: 'Which branch?' }] } })
    const rows = needsYou(list(destructive, old))
    expect(rows.map((r) => r.key)).toEqual(['waiting:approval:new', 'waiting:question:old'])
    expect(rows[1].title).toBe('Which branch?')
  })

  it('puts the skill proposals and your notes after every waiting item, oldest first', () => {
    const rows = needsYou(
      list(waitingItem()),
      [note({ id: 'late', created_at: 300 }), note({ id: 'early', created_at: 200 })],
      [{ id: 'p1', slug: 'release-notes', description: 'Writes the notes', created_at: '1970-01-01T00:04:10Z' }],
    )
    expect(rows.map((r) => r.key)).toEqual(['waiting:approval:a1', 'note:early', 'proposal:p1', 'note:late'])
  })

  it('leaves out what the waiting list holds and what is not an ask', () => {
    const rows = needsYou(list(), [
      note({ id: 'mirror', item_kind: 'agent_request' }),
      note({ id: 'digest', item_kind: 'digest' }),
      note({ id: 'done', status: 'handled' }),
      note({ id: 'proposal-row', item_kind: 'proposal', refs: { skill_proposal: 'p1' } }),
    ], [{ id: 'p1', slug: 's', description: '', created_at: '' }])
    expect(rows.map((r) => r.key)).toEqual(['proposal:p1'])
  })

  it('names each kind by its own brief', () => {
    expect(waitingTitle(waitingItem({ kind: 'gate', brief: { workflow: 'release', ask: { prompt: 'Ship it?' } } }))).toBe('Ship it?')
    expect(waitingTitle(waitingItem({ kind: 'loop_question', brief: { name: 'Docs', merge: {} } }))).toBe('Merge what the loop made?')
    expect(waitingTitle(waitingItem({ kind: 'plan_review', brief: { step: { title: 'Split the module' } } }))).toBe('Review the plan: Split the module')
  })

  it('survives a list that is not one', () => {
    expect(needsYou(undefined)).toEqual([])
    expect(needsYou({ waiting: [null, 7, { id: '' }] } as unknown as WaitingList)).toEqual([])
  })
})
