// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { RunningRow } from '../../lib/api'
import { clockTime } from '../../lib/epoch'
import { FinishedRows, RunningLane } from './RunningLane'

// ── Running now is the running list (#3889, #3813) ──────────────────────────────────────────────
//
// The lane a project's page draws for its live work, from `GET /api/running`, the one list of live
// work every reader shares (Home's band, `agents/AgentDay.tsx`, draws all of it). Each row shows the
// work's name, its exact phase as text, what it is doing, the agent doing it, its last update and a
// route that opens it. A blocked row says so, apart from working, and a row that waits on you says
// how much does. Rendered here alone: each state the lane is handed is fed, and each assertion names
// what it fed, so an empty lane cannot pass for any of them. (What a restart cut off is told on the
// band: `agents/AgentDay.restart.test.tsx`.)

const UPDATED = '2026-10-21T10:04:00Z'

function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c1', kind: 'chat', session: 'c1', phase: 'searching', line: 'Searching your knowledge',
    since: UPDATED, name: 'Plan the garden', agent: 'gardener', started_at: '2026-10-21T10:00:00Z',
    route: '#/chat/c1', blocked: false, waiting: [], project_id: '', loop_kind: '', ...over,
  }
}

const EMPTY = 'Nothing is running right now.'
const lane = (rows: RunningRow[], over: Partial<Parameters<typeof RunningLane>[0]> = {}) =>
  render(<RunningLane rows={rows} loading={false} failed={false} empty={EMPTY} {...over} />)
const working = () => screen.getByRole('region', { name: 'Running now' })

describe('each row says what the work is doing', () => {
  it('its name, its phase as text, its line, its agent, its last update and a route into it', () => {
    lane([row()])
    const card = within(working()).getByRole('listitem', { name: /Plan the garden/ })
    expect(within(card).getByText('Plan the garden')).toBeTruthy()
    expect(within(card).getByText('Searching')).toBeTruthy()
    expect(within(card).getByText('Searching your knowledge')).toBeTruthy()
    expect(within(card).getByText(/gardener/)).toBeTruthy()
    expect(within(card).getByText(`Updated ${clockTime(UPDATED)}`)).toBeTruthy()
    expect(within(card).getByRole('link', { name: 'Open Plan the garden — chat' }).getAttribute('href')).toBe('#/chat/c1')
    expect(within(working()).getByText('1', { selector: '[data-lane-count]' })).toBeTruthy()
  })

  it('🔴 a queued helper reads blocked, with its reason, and is not counted working', () => {
    lane([
      row(),
      row({ work: 'subagent:h4', kind: 'subagent', name: 'grep the tree', phase: 'queued', line: 'Waiting to start', blocked: true }),
    ])
    const card = within(working()).getByRole('listitem', { name: /grep the tree/ })
    expect(within(card).getByText('Blocked')).toBeTruthy()
    expect(within(card).getByText('Waiting to start')).toBeTruthy()
    expect(within(working()).getByText('1 blocked')).toBeTruthy()
  })

  it('a held run and a held loop say what holds them and what moves them again, and never read as running', () => {
    // The running list's own words for work incident mode holds (`work_phase.HELD`).
    const held = 'Held until incident mode is off'
    lane([
      row({ work: 'workflow:r1', kind: 'workflow', session: '', name: 'nightly-digest', phase: 'queued', line: held, blocked: true }),
      row({ work: 'loop:l1', kind: 'loop', session: '', name: 'Fix the README', phase: 'queued', line: held, blocked: true }),
    ])
    for (const name of [/nightly-digest/, /Fix the README/]) {
      const card = within(working()).getByRole('listitem', { name })
      expect(within(card).getByText(held)).toBeTruthy()
      expect(within(card).getByText('Blocked')).toBeTruthy()
      expect(card.textContent ?? '', 'a held row reads as running').not.toMatch(/\b(Running|Thinking|Working)\b/)
    }
  })

  it('a row that waits on you says so', () => {
    lane([row({ phase: 'approval', line: 'Needs your OK: shell', waiting: ['ask-1'] })])
    const card = within(working()).getByRole('listitem', { name: /Plan the garden/ })
    expect(within(card).getByText('1 waiting on you')).toBeTruthy()
  })
})

describe('every state of the lane', () => {
  it('empty: it says what its reader says of nothing running', () => {
    lane([])
    expect(within(working()).getByText(EMPTY)).toBeTruthy()
  })

  it('loading: it says so, never that nothing runs', () => {
    lane([], { loading: true })
    expect(within(working()).getByText('Loading…')).toBeTruthy()
    expect(within(working()).queryByText(EMPTY)).toBeNull()
  })

  it('a list that cannot be read: it says so, never that nothing runs', () => {
    lane([], { failed: true })
    expect(within(working()).getByText('Could not read what is running.')).toBeTruthy()
    expect(within(working()).queryByText(EMPTY)).toBeNull()
  })

  it('large: the count is all of it, the first rows show, and the rest are one press away', async () => {
    lane(Array.from({ length: 60 }, (_, i) => row({ work: `subagent:h${i}`, kind: 'subagent', name: `Helper ${i}` })))
    within(working()).getByText('Helper 0')
    expect(within(working()).getByText('60', { selector: '[data-lane-count]' })).toBeTruthy()
    expect(within(working()).queryByText('Helper 59')).toBeNull()
    const more = within(working()).getByRole('button', { name: 'Show all 60' })
    await userEvent.click(more)
    expect(within(working()).getByText('Helper 59')).toBeTruthy()
    // The button stays, so focus stays on it, and takes the lane back to its first rows.
    expect(document.activeElement).toBe(more)
    expect(more.textContent).toBe('Show the first 25')
  })
})

describe('the just-finished tail', () => {
  it('failed and just finished: each ending says how it ended, when, and opens the work', () => {
    const ended = '2026-10-21T10:06:00Z'
    render(<FinishedRows interrupted={[]} finished={[
      row({ work: 'workflow:r9', kind: 'workflow', session: '', name: 'nightly', phase: 'done', line: 'Done in 12 seconds', since: ended, route: '#/workflows/runs/r9' }),
      row({ work: 'subagent:h2', kind: 'subagent', name: 'fetch the page', phase: 'error', line: 'Stopped: the site refused', since: ended }),
    ]} />)
    const tail = screen.getByRole('list', { name: 'Just finished' })
    const done = within(tail).getByRole('listitem', { name: /nightly/ })
    expect(within(done).getByText('Done')).toBeTruthy()
    expect(within(done).getByText('Done in 12 seconds')).toBeTruthy()
    expect(within(done).getByText(`Ended ${clockTime(ended)}`)).toBeTruthy()
    expect(within(done).getByRole('link', { name: 'Open nightly — Done' }).getAttribute('href')).toBe('#/workflows/runs/r9')
    const failed = within(tail).getByRole('listitem', { name: /fetch the page/ })
    expect(within(failed).getByText('Stopped')).toBeTruthy()
    expect(within(failed).getByText('Stopped: the site refused')).toBeTruthy()
  })

  it('interrupted: work a restart cut off, said as interrupted, opens the work, and an ending the gateway kept wins', () => {
    const cut = { row: row(), at: Date.now() }
    const { rerender } = render(<FinishedRows finished={[]} interrupted={[cut]} />)
    const item = within(screen.getByRole('list', { name: 'Just finished' })).getByRole('listitem', { name: /Plan the garden/ })
    expect(within(item).getByText('Interrupted')).toBeTruthy()
    expect(within(item).getByText('The gateway restarted while it ran.')).toBeTruthy()
    expect(within(item).getByRole('link', { name: 'Open Plan the garden — Interrupted' }).getAttribute('href')).toBe('#/chat/c1')
    rerender(<FinishedRows finished={[row({ phase: 'done', line: 'Done' })]} interrupted={[cut]} />)
    expect(screen.queryByText('Interrupted')).toBeNull()
  })

  it('nothing ended: it draws nothing', () => {
    const { container } = render(<FinishedRows finished={[]} interrupted={[]} />)
    expect(container.textContent).toBe('')
  })
})
