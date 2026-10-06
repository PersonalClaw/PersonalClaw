import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatActivityPanel } from './ChatActivityPanel'
import { foldSubagentEvent } from './subagentCards'
import type { SubagentCard } from './chatTypes'

// ── A helper that never ran is in the Activity panel of the chat that asked for it ──────────────
//
// 🔴 She declined a helper's start, and its report was handed to the chat as a turn of its own: the
// agent went back to work by itself. Its report now starts no turn, so the chat's Subagents list is
// where it shows how it ended. The list had a card only for a helper that started, and a helper
// whose start she declined never did.

const ACTIVITY = { files: [], links: [] }

const DECLINED = {
  id: 'a1b2c3d4', session: 'chat-review', task: 'Review the last commit', agent: '',
  error: 'spawn declined, so it never started', result: '', never_ran: true, declined: true,
}

describe('the chat’s Subagents list', () => {
  it('🔴 lists a helper whose start she declined, from its ending alone', () => {
    const cards = foldSubagentEvent([], 'subagent_done', DECLINED)
    expect(cards).toEqual([expect.objectContaining({
      id: 'a1b2c3d4', task: 'Review the last commit', done: true, neverRan: true, declined: true,
      error: 'spawn declined, so it never started',
    })])
  })

  it('and an ending of a helper it never listed, which did run, adds nothing', () => {
    expect(foldSubagentEvent([], 'subagent_done', { id: 'b2', error: 'boom' })).toEqual([])
  })

  it('keeps a helper that ran as it always has: its spawn, its tool, its ending', () => {
    let cards: SubagentCard[] = foldSubagentEvent([], 'subagent_spawn', { id: 'c3', task: 'Read the log', agent: 'reader' })
    cards = foldSubagentEvent(cards, 'subagent_tool', { id: 'c3', tool: 'read_file' })
    expect(cards[0]).toMatchObject({ done: false, lastTool: 'read_file' })
    cards = foldSubagentEvent(cards, 'subagent_done', { id: 'c3', result: 'It found two errors.', elapsed: 4.2 })
    expect(cards).toEqual([expect.objectContaining({ id: 'c3', done: true, result: 'It found two errors.', elapsed: 4.2 })])
    expect(cards[0].neverRan).toBeUndefined()
  })

  it('🔴 shows it declined, with why, and not as a failure', async () => {
    const card = foldSubagentEvent([], 'subagent_done', DECLINED)
    render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} subagents={card} onKillFanout={() => {}} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Subagents' }))
    expect(screen.getByText(/· declined/)).toBeTruthy()
    const why = screen.getByText('spawn declined, so it never started')
    expect(why.className).not.toContain('text-danger')
  })

  it('🔴 says a start nobody allowed in time did not start', async () => {
    const card = foldSubagentEvent([], 'subagent_done', {
      ...DECLINED, declined: false, error: 'spawn not approved in time: nobody answered within 30 minutes, so it never started',
    })
    render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} subagents={card} onKillFanout={() => {}} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Subagents' }))
    expect(screen.getByText(/· not started/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Stop fan-out/ })).toBeNull()
  })
})
