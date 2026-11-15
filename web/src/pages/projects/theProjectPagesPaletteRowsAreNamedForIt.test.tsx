import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import type { ProjectItem } from '../../lib/api'
import { projectCommands, OPEN_A_PROJECT } from '../../app/paletteProjectRows'
import { useOpenProjectPage, useProjectPageActions, type ProjectPageActions } from '../../app/projectPageActions'

// ── A project page's palette rows are named for it (§5.15; fix round 2) ─────────────────────────
//
// While the page is open its seven rows are listed once each, named for the project, and run
// ("Start a chat in Garden" focuses the box). Away from it the six page-bound rows are listed once
// each, dimmed, under §7's labels with no project and the reason "Open a project to use this.", and
// run nothing. "Start a chat in <project>" stays one row per project, never dimmed.

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const PROJECTS: ProjectItem[] = [
  { id: 'p-garden', name: 'Garden', status: 'active' },
  { id: 'p-house', name: 'House move', status: 'active' },
]
const actions = (): ProjectPageActions => ({
  id: 'p-garden', name: 'Garden',
  startChat: vi.fn(), newGoalLoop: vi.fn(), newTask: vi.fn(), editBrief: vi.fn(), editInstructions: vi.fn(), openFolder: vi.fn(), told: vi.fn(),
})
const PAGE_LABELS = ['New goal loop in Garden', 'New task in Garden', "Edit Garden's brief", "Edit Garden's instructions", "Open Garden's folder", 'What a new chat in Garden is told']
const ANYWHERE = ['New goal loop in a project', 'New task in a project', "Edit a project's brief", "Edit a project's instructions", "Open a project's folder", 'What a new chat in a project is told']

describe("a project page's palette rows", () => {
  it('are named for the open project, listed once each, and run', () => {
    const page = actions()
    const navigate = vi.fn()
    const rows = projectCommands(PROJECTS, [], () => 'Go to', navigate, page)
    const labels = rows.map((r) => r.label)
    for (const l of PAGE_LABELS) expect(labels.filter((x) => x === l), l).toHaveLength(1)
    expect(labels.filter((x) => x === 'Start a chat in Garden')).toHaveLength(1)
    rows.find((r) => r.label === 'Start a chat in Garden')!.run()
    expect(page.startChat, 'Start a chat in Garden focuses the box').toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
    rows.find((r) => r.label === "Edit Garden's brief")!.run()
    expect(page.editBrief).toHaveBeenCalled()
    expect(rows.filter((r) => r.disabledReason)).toEqual([])
    // The page's rows come after the project rows, so the empty palette's first screen is unchanged.
    expect(labels.indexOf('New goal loop in Garden')).toBeGreaterThan(labels.indexOf('All loops'))
  })

  it('away from it, are listed once each, dimmed with one reason, and run nothing', () => {
    const navigate = vi.fn()
    const rows = projectCommands(PROJECTS, [], () => 'Go to', navigate, null)
    for (const l of ANYWHERE) {
      const row = rows.filter((r) => r.label === l)
      expect(row, l).toHaveLength(1)
      expect(row[0].disabledReason).toBe(OPEN_A_PROJECT)
    }
    expect(OPEN_A_PROJECT).toBe('Open a project to use this.')
    for (const name of ['Garden', 'House move']) {
      const start = rows.filter((r) => r.label === `Start a chat in ${name}`)
      expect(start).toHaveLength(1)
      expect(start[0].disabledReason, 'Start a chat in a project is never dimmed').toBeUndefined()
    }
  })

  it('are published only while the page is mounted', () => {
    let seen: ProjectPageActions | null = null
    function Reader() { seen = useOpenProjectPage(); return null }
    function Page() { useProjectPageActions(actions()); return null }
    const { rerender } = render(<><Reader /><Page /></>)
    expect(seen).not.toBeNull()
    expect((seen as unknown as ProjectPageActions).name).toBe('Garden')
    act(() => { rerender(<Reader />) })
    expect(seen).toBeNull()
  })
})
