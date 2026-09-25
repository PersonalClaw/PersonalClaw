import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen, within } from '@testing-library/react'
import { WorkBoardColumn, WORK_OUTCOME_LOOK, WORK_STATE_LABEL } from './ProjectsSection'
import { statusMeta } from '../tasks/taskMeta'
import type { WorkBoard, WorkGroup, WorkRow } from '../../lib/api'

// ── The Work board's rendering contract (WORK-CONTAINERS §1/§5.2/§6.1) ──────────
// The board is fed by `api.projectWork`. These pin the four behaviours the atom's
// done-when turns on, from rendered DOM (not source scans):
//   1. groups render in the server's order — needs-input FIRST, unconditionally;
//   2. a failed source shows an inline degraded note and the board STILL renders the
//      OK groups (the FE half of per-section isolation);
//   3. a suspended row offers Resume (row.resumable);
//   4. a collapsed row (subagent-tool noise) starts collapsed behind a disclosure.

function row(over: Partial<WorkRow> = {}): WorkRow {
  return {
    run_id: 'r1', title: 'A run', state: 'working', origin: 'manual', project_id: 'p1',
    claim: null, collapsed: false, attention: false, resumable: false, outcome: '', ...over,
  }
}
function group(state: WorkGroup['state'], rows: WorkRow[]): WorkGroup {
  return { state, count: rows.length, attention: rows.filter((r) => r.attention).length, rows }
}
function board(over: Partial<WorkBoard> = {}): WorkBoard {
  return {
    board: [], sections: [
      { name: 'runs', items: [], status: 'ok', error: '', loadedAt: 0 },
      { name: 'loops', items: [], status: 'ok', error: '', loadedAt: 0 },
      { name: 'tasks', items: [], status: 'ok', error: '', loadedAt: 0 },
    ], completeness: 'complete', attention: 0, loadedAt: 0, ...over,
  }
}

describe('WorkBoardColumn', () => {
  it('renders groups in server order — needs-input pinned first', () => {
    const wb = board({
      board: [
        group('needs_input', [row({ run_id: 'a', title: 'Blocked', state: 'needs_input', attention: true })]),
        group('working', [row({ run_id: 'b', title: 'Busy', state: 'working' })]),
      ],
    })
    render(<WorkBoardColumn work={wb} loading={false} onResume={() => {}} />)
    const groups = document.querySelectorAll('[data-testid^="work-group-"]')
    expect(groups[0].getAttribute('data-testid')).toBe('work-group-needs_input')
    expect(groups[1].getAttribute('data-testid')).toBe('work-group-working')
  })

  it('shows a degraded note for a failed section but still renders the OK groups', () => {
    const wb = board({
      board: [group('working', [row({ title: 'Still here' })])],
      sections: [
        { name: 'runs', items: [], status: 'ok', error: '', loadedAt: 0 },
        { name: 'tasks', items: [], status: 'error', error: 'boom', loadedAt: 0 },
      ],
      completeness: 'partial',
    })
    render(<WorkBoardColumn work={wb} loading={false} onResume={() => {}} />)
    // the degraded note is present…
    expect(screen.getByTestId('work-section-error-tasks')).toBeTruthy()
    // …and the OK group's row still rendered (not a full-board error)
    expect(screen.getByText('Still here')).toBeTruthy()
  })

  it('offers Resume on a suspended (resumable) row and calls back with the run id', () => {
    const onResume = vi.fn()
    const wb = board({
      board: [group('suspended', [row({ run_id: 'sus1', title: 'Paused work', state: 'suspended', resumable: true })])],
    })
    render(<WorkBoardColumn work={wb} loading={false} onResume={onResume} />)
    const btn = screen.getByTitle('Resume this suspended work')
    fireEvent.click(btn)
    expect(onResume).toHaveBeenCalledWith('sus1')
  })

  it('renders a claim badge naming the holder', () => {
    const wb = board({
      board: [group('working', [row({
        title: 'Claimed', claim: { holder: 'worker-7', expires_at: 9e9, taken_at: 0, renewals: 0 },
      })])],
    })
    render(<WorkBoardColumn work={wb} loading={false} onResume={() => {}} />)
    expect(screen.getByText('worker-7')).toBeTruthy()
  })

  it('starts a collapsed row collapsed, expandable on click', () => {
    const wb = board({
      board: [group('working', [row({ title: 'Subagent noise', collapsed: true })])],
    })
    render(<WorkBoardColumn work={wb} loading={false} onResume={() => {}} />)
    // Collapsed: the disclosure button carries the title; the full CircleDot row is absent.
    const disclosure = screen.getByRole('button', { name: /Subagent noise/ })
    expect(disclosure).toBeTruthy()
    fireEvent.click(disclosure)
    // After expanding, the title is shown in the full row (the disclosure is gone).
    expect(screen.getByText('Subagent noise')).toBeTruthy()
  })

  it('shows the empty state when every section is ok and the board is empty', () => {
    render(<WorkBoardColumn work={board()} loading={false} onResume={() => {}} />)
    expect(screen.getByText(/No work here yet/)).toBeTruthy()
  })

  // ── Cancelled is not done ────────────────────────────────────────────────────────────────
  // Measured on a project page: a cancelled task sat under "DONE · 2" with the same glyph as
  // the completed task beside it, so the board said the press kit had shipped. The server's
  // `done` group is "nothing left for you to do" — which a cancelled task genuinely is — so the
  // group is named for that, and every row in it says how it ended.
  it('names the terminal group Closed, not Done', () => {
    expect(WORK_STATE_LABEL.done).toBe('Closed')
    const wb = board({ board: [group('done', [row({ title: 'Shipped', state: 'done', outcome: 'completed' })])] })
    render(<WorkBoardColumn work={wb} loading={false} onResume={() => {}} />)
    const heading = screen.getByTestId('work-group-done').firstElementChild as HTMLElement
    expect(heading.textContent).toMatch(/^Closed/)
    expect(heading.textContent).not.toMatch(/Done/)
  })

  it('gives a cancelled row its own named glyph and word, distinct from a completed one', () => {
    const wb = board({
      board: [group('done', [
        row({ run_id: 'a', title: 'Draft landing page copy', state: 'done', outcome: 'completed' }),
        row({ run_id: 'b', title: 'Press kit outreach', state: 'done', outcome: 'cancelled' }),
      ])],
    })
    render(<WorkBoardColumn work={wb} loading={false} onResume={() => {}} />)
    const rows = screen.getByTestId('work-group-done').querySelectorAll('[data-outcome]')
    const [done, cancelled] = [...rows] as HTMLElement[]
    expect(done.getAttribute('data-outcome')).toBe('completed')
    expect(cancelled.getAttribute('data-outcome')).toBe('cancelled')
    // Named for assistive tech, and the word is visible for everyone else.
    expect(within(cancelled).getByRole('img', { name: 'Cancelled' })).toBeTruthy()
    expect(cancelled.textContent).toContain('Cancelled')
    expect(within(done).getByRole('img', { name: 'Completed' })).toBeTruthy()
    // Not the same glyph: the whole defect was two endings drawn identically.
    const glyph = (el: HTMLElement) => el.querySelector('svg')?.getAttribute('class')
    expect(glyph(cancelled)).not.toBe(glyph(done))
  })

  it('reads each ending from the owning registry, so the words match #/tasks', () => {
    expect(WORK_OUTCOME_LOOK.completed.label).toBe(statusMeta('done').label)
    expect(WORK_OUTCOME_LOOK.cancelled.label).toBe(statusMeta('cancelled').label)
    expect(WORK_OUTCOME_LOOK.skipped.label).toBe(statusMeta('skipped').label)
    expect(WORK_OUTCOME_LOOK.cancelled.icon).toBe(statusMeta('cancelled').icon)
  })

  it('a row that has not ended carries no ending', () => {
    const wb = board({ board: [group('queued', [row({ title: 'Assemble pricing sheet', state: 'queued' })])] })
    render(<WorkBoardColumn work={wb} loading={false} onResume={() => {}} />)
    expect(screen.getByTestId('work-group-queued').querySelector('[data-outcome]')).toBeNull()
    expect(screen.queryByRole('img')).toBeNull()
  })
})
