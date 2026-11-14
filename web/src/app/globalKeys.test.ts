import { describe, expect, it } from 'vitest'
import { waitingItem, waitingList } from '../test/waiting'
import { nextNeedsYouRoute, unreadNeedsYou } from './globalKeys'

// ── ⌘⌥A goes to the next thing that needs you, in Needs you's order ─────────────────────────────
//
// It read Home's old lanes (approvals first, then your turn), so it skipped what the waiting list
// holds and the lanes did not (a run's step, a loop's question, a browser grant) and went nowhere
// for a proposal or a note. It reads Needs you's one read now, in its order: the first row's own
// route, where it is answered, or Needs you with that row's panel open when Needs you is where it
// is answered. Nothing listed goes nowhere, and says why when a source could not be read.

const read = (waiting = waitingList(), items: unknown[] = [], proposals: unknown[] = []) =>
  ({ waiting, items, proposals }) as Parameters<typeof nextNeedsYouRoute>[0]

describe('⌘⌥A', () => {
  it('a chat’s ask goes to its chat', () => {
    expect(nextNeedsYouRoute(read(waitingList(waitingItem({ kind: 'approval', id: 'approval:a1', route: '#/chat/chat-1' }))))).toBe('chat/chat-1')
  })

  it('follows the list’s order, not the kind: a run’s step served first goes first', () => {
    const list = waitingList(
      waitingItem({ kind: 'gate', id: 'gate:run-7:push:1', route: '#/workflows/runs/run-7' }),
      waitingItem({ kind: 'approval', id: 'approval:a1', route: '#/chat/chat-1' }),
    )
    expect(nextNeedsYouRoute(read(list))).toBe('workflows/runs/run-7')
  })

  it('a browser grant, answered on Needs you, opens there with its panel open', () => {
    const list = waitingList(waitingItem({ kind: 'browse_grant', id: 'browse_grant:g1', route: '#/mission-control' }))
    expect(nextNeedsYouRoute(read(list))).toBe('mission-control?open=waiting%3Abrowse_grant%3Ag1')
  })

  it('a proposal or a note, with nothing waiting, opens on Needs you with its panel open', () => {
    expect(nextNeedsYouRoute(read(waitingList(), [], [{ id: 'p1', slug: 'tidy', description: 'Tidy notes', created_at: 1 }])))
      .toBe('mission-control?open=proposal%3Ap1')
    expect(nextNeedsYouRoute(read(waitingList(), [{ id: 'n1', item_kind: 'user_note', status: 'pending', message: 'Call the bank', created_at: 1 }])))
      .toBe('mission-control?open=note%3An1')
  })

  it('nothing listed goes nowhere', () => {
    expect(nextNeedsYouRoute(read())).toBeNull()
    expect(unreadNeedsYou(read()), 'every source was read').toBe('')
  })

  it('with a source unread and nothing listed, says something may be waiting there', () => {
    expect(unreadNeedsYou(read({ waiting: [], unreadable: ['approval'] }))).toBe("Couldn't read approvals, so something may be waiting.")
  })
})
