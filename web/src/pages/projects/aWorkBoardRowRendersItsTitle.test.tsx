import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { WorkBoardColumn } from './ProjectsSection'
import type { WorkBoard, WorkGroup, WorkRow } from '../../lib/api'
import { MD_LINE, rendersLine } from '../../test/markdownSinks'

// ── A project's work board renders each row's title ────────────────────────────────────────────
//
// 🔴 A work-board row is a task's title, a run's name or a loop's goal, all an agent's words, and it
// showed their marks. Open or folded, it now renders the title as a line, and the row's open control
// is named by the same words, plain.

const SEEN = 'Skip model_calls in export_digest() in published.py'

const row = (over: Partial<WorkRow> = {}): WorkRow => ({
  run_id: 'r1', title: MD_LINE, state: 'working', source: 'run', kind: '', origin: 'manual', project_id: 'p1',
  claim: null, collapsed: false, attention: false, resumable: false, outcome: '', ...over,
} as WorkRow)
const group = (state: WorkGroup['state'], rows: WorkRow[]): WorkGroup => ({ state, count: rows.length, attention: 0, rows })
const board = (rows: WorkRow[]): WorkBoard => ({
  board: [group('working', rows)],
  sections: [{ name: 'runs', items: [], status: 'ok', error: '', loadedAt: 0 }],
  completeness: 'complete', attention: 0, loadedAt: 0,
} as unknown as WorkBoard)

describe("a work board row's title", () => {
  it('renders as a line, and its open control is named in plain text', () => {
    render(<WorkBoardColumn work={board([row()])} loading={false} onResume={() => {}} onOpen={() => {}} onStop={() => {}} />)
    const open = screen.getByTitle(`Open ${SEEN}`)
    expect(rendersLine(open)).toEqual([])
  })

  it('a folded row renders its title as a line too', () => {
    render(<WorkBoardColumn work={board([row({ run_id: 'r2', collapsed: true })])} loading={false} onResume={() => {}} onOpen={() => {}} onStop={() => {}} />)
    expect(rendersLine(screen.getByTitle(SEEN))).toEqual([])
  })
})
