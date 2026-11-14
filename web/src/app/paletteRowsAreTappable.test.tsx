import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import { BookOpen, FileCode, Settings } from 'lucide-react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, type ProjectItem, type RunningRow, type WaitingItem } from '../lib/api'
import { resetDataStore } from '../lib/data'
import { CommandPalette, type Command } from './CommandPalette'
import { PaletteProjectRows } from './paletteProjectRows'
import { useState } from 'react'

// ── The palette's rows are tappable, and its projects are in it ──────────────────────────────────
//
// The palette opens on a phone too, so each row is a 44 px target below 1024 px. And it lists the
// projects: a Go to row for each unarchived one, its mark in its icon's place and its pulse after
// the "Go to"; a "Start a chat in <project>" row each; New project, Import a project and All loops,
// all after the Library's Go to rows, so the empty palette's first screen is the destinations it
// always was. Each row opens what it names.

const commands: Command[] = [
  { id: 'go:knowledge', label: 'Knowledge', hint: 'Go to', icon: BookOpen, run: () => {} },
  { id: 'go:artifacts', label: 'Artifacts', hint: 'Go to', icon: FileCode, run: () => {} },
  { id: 'act:settings', label: 'Open Settings', hint: 'Action', icon: Settings, run: () => {} },
]
const project = (id: string, name: string, over: Partial<ProjectItem> = {}) => ({ id, name, status: 'active', ...over }) as ProjectItem
const ask = (id: string) => ({ id, kind: 'loop_question', work: `loop:${id}`, project_id: 'p-garden', brief: {}, loop_kind: 'goal' }) as unknown as WaitingItem
const run = (work: string) => ({ work, kind: 'loop', project_id: 'p-garden', waiting: [], phase: 'tool', line: '', since: null, name: work }) as unknown as RunningRow

const navigate = vi.fn()

beforeEach(() => {
  Element.prototype.scrollIntoView ??= () => {}
  resetDataStore(); navigate.mockReset()
  vi.spyOn(api, 'projects').mockResolvedValue([project('p-garden', 'Garden'), project('p-personal', 'Personal', { is_builtin: true }), project('p-old', 'Old', { status: 'archived' })])
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '', pinned_project_ids: [] })
  vi.spyOn(api, 'waiting').mockResolvedValue({ waiting: [ask('a'), ask('b')], unreadable: [] })
  vi.spyOn(api, 'inboxOpen').mockResolvedValue([])
  vi.spyOn(api, 'skillProposals').mockResolvedValue({ proposals: [], lastReview: null })
  vi.spyOn(api, 'running').mockResolvedValue({ running: [run('loop:x'), run('loop:y')], finished: [] })
})
afterEach(() => { vi.restoreAllMocks(); resetDataStore() })

async function open() {
  render(<CommandPalette commands={commands} navigate={navigate} projectRowsAfter="go:artifacts" />)
  act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })) })
  const list = await screen.findByRole('listbox')
  await waitFor(() => expect(within(list).getByRole('option', { name: /^Garden/ })).toBeInTheDocument())
  return list
}

const labels = (list: HTMLElement) => within(list).getAllByRole('option').map((o) => o.querySelector('span span')?.textContent)

describe("the palette's project rows settle", () => {
  it('when the settings read fails: the rows are handed up a bounded number of times', async () => {
    vi.mocked(api.projectSettings).mockRejectedValue(new Error('500'))
    let renders = 0
    let handed: Command[] = []
    // The palette hands its state's setter, whose identity holds, as here.
    function Host() {
      const [rows, setRows] = useState<Command[]>([])
      renders += 1
      handed = rows
      return <PaletteProjectRows navigate={navigate} onRows={setRows} />
    }
    render(<Host />)
    await waitFor(() => expect(handed.some((r) => r.label === 'Garden')).toBe(true))
    await act(async () => { await new Promise((r) => setTimeout(r, 50)) })
    expect(renders, 'an empty pin list that is a new array each render never settles').toBeLessThan(20)
  })
})

describe("the palette's rows", () => {
  it('are each a 44 px target below 1024 px', async () => {
    const list = await open()
    for (const option of within(list).getAllByRole('option')) {
      expect(option.className.split(/\s+/)).toEqual(expect.arrayContaining(['max-lg:min-h-11']))
    }
  })

  it('list each unarchived project after the Library, with its pulse after "Go to", or "Go to" alone', async () => {
    const list = await open()
    expect(labels(list)).toEqual([
      'Knowledge', 'Artifacts', 'Garden', 'Personal', 'Start a chat in Garden', 'Start a chat in Personal',
      'New project', 'Import a project', 'All loops', 'Open Settings',
    ])
    const garden = within(list).getByRole('option', { name: /^Garden/ })
    await waitFor(() => expect(garden.textContent).toContain('Go to · 2 need you · 2 running'))
    expect(within(list).getByRole('option', { name: /^Personal/ }).textContent).toMatch(/Go to$/)
    expect(within(list).getByRole('option', { name: /^Start a chat in Garden/ }).textContent).toContain('Action')
  })

  it("draws a project's mark in its icon's place, hidden, its name still its label", async () => {
    const list = await open()
    const garden = within(list).getByRole('option', { name: /^Garden/ })
    const mark = garden.querySelector('[aria-hidden="true"]')
    expect(mark?.textContent).toBe('G')
    expect(garden.querySelector('svg.lucide')).toBeNull()
    expect(within(list).getByRole('option', { name: /^Knowledge/ }).querySelector('svg')).not.toBeNull()
  })

  it('open what they name', async () => {
    render(<CommandPalette commands={commands} navigate={navigate} projectRowsAfter="go:artifacts" />)
    for (const [name, to] of [
      [/^Garden/, 'projects/p-garden'],
      [/^Start a chat in Garden/, 'chat/new?project=p-garden'],
      [/^New project/, 'projects?new=1'],
      [/^Import a project/, 'projects?import=1'],
      [/^All loops/, 'loops/history'],
    ] as const) {
      act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true })) })
      const option = await within(await screen.findByRole('listbox')).findByRole('option', { name })
      act(() => { option.click() })
      expect(navigate).toHaveBeenLastCalledWith(to)
      await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())
    }
  })
})

describe('the Projects row in the sidebar', () => {
  it('carries no badge', () => {
    const app = readFileSync(join(process.cwd(), 'src/app/App.tsx'), 'utf8')
    expect(app).not.toMatch(/n\.id === 'projects' &&/)
    expect(app).not.toMatch(/activeLoops/)
  })
})
