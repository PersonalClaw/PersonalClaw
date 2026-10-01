import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, within, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Loop, WorkBoard, WorkRow } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

// ── A loop at work can be stopped from wherever it shows as working ────────────────────────────
//
// 🔴 Before: a Code loop left "planning" by a Cancel that sent nothing ran its planner on, through
// two restarts, and nothing that showed it could stop it. Its walkthrough offered only "Cancel and
// edit the task" and "Retry this step"; the Code list showed a spinner and a Delete; the project's
// Work board said "Working · 1" with nothing to click; and the Loops list left code loops out.
// Each now offers what the loop's own page does: Stop, behind the one Stop confirm (worded for a
// loop still planning), and a way to the loop.

const { STORE, confirmed } = vi.hoisted(() => ({
  STORE: { loops: [] as Loop[] },
  confirmed: [] as { title?: string }[],
}))

vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirm: (opts: { title?: string }) => { confirmed.push(opts); return Promise.resolve(true) },
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loops),
    uLoop: (id: string) => {
      const hit = STORE.loops.find((l) => l.id === id)
      return hit ? Promise.resolve(hit) : Promise.reject(Object.assign(new Error('Not found'), { status: 404 }))
    },
    uLoopAction: vi.fn((id: string) => {
      const hit = STORE.loops.find((l) => l.id === id)
      if (hit) hit.status = 'stopped'
      return Promise.resolve(hit ?? null)
    }),
    deleteULoop: () => Promise.resolve(),
  },
}))

const { api } = await import('../../lib/api')

function loop(over: Partial<Loop>): Loop {
  return {
    id: 'c0ffee01', kind: 'code', name: 'Digest titles', task: 'escape digest titles once',
    execution: 'solo', agent: '', model: '', attended: true, max_cycles: 30, idle_secs: 0,
    success_criteria: null, status: 'planning', total_cycles: 0, error_message: null,
    created_at: 1_780_000_000, started_at: null, completed_at: null, kind_config: { project_kind: 'brownfield' },
    findings: [], plan: [], ...over,
  } as Loop
}

beforeEach(() => {
  STORE.loops = []
  confirmed.length = 0
  vi.mocked(api.uLoopAction).mockClear()
  resetDataStore()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
})
afterEach(() => { cleanup(); resetDataStore() })

describe('stopLoop', () => {
  it('words the confirm for a loop still planning, then stops it', async () => {
    const { stopLoop } = await import('./stopLoop')
    STORE.loops = [loop({})]

    expect(await stopLoop('c0ffee01')).toBe(true)

    // Named: from a list of loops, "this run" says nothing about which one.
    expect(confirmed.map((c) => c.title)).toEqual(['Stop planning \u201cDigest titles\u201d?'])
    expect(api.uLoopAction).toHaveBeenCalledWith('c0ffee01', 'stop')
  })

  it('offers no stop to a loop that has already ended', async () => {
    const { stopLoop } = await import('./stopLoop')
    STORE.loops = [loop({ status: 'complete' })]

    expect(await stopLoop('c0ffee01')).toBe(false)

    expect(confirmed).toEqual([])
    expect(api.uLoopAction).not.toHaveBeenCalled()
  })
})

describe('the Code list', () => {
  it('a planning project has a visible Stop, and a finished one has none', async () => {
    const { CodeSection } = await import('../code/CodeSection')
    STORE.loops = [loop({}), loop({ id: 'c0ffee02', name: 'Old fix', status: 'complete' })]
    const navigate = vi.fn()
    render(<CodeSection sub="history" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)

    const planning = (await screen.findByText('Digest titles')).closest<HTMLElement>('[role="button"]')!
    const done = screen.getByText('Old fix').closest<HTMLElement>('[role="button"]')!
    expect(within(done).queryByRole('button', { name: /^stop$/i })).toBeNull()
    await userEvent.click(within(planning).getByRole('button', { name: /^stop$/i }))

    await waitFor(() => expect(api.uLoopAction).toHaveBeenCalledWith('c0ffee01', 'stop'))
    expect(navigate, 'the Stop opened the project instead').not.toHaveBeenCalled()
  })

  it('Enter on the Stop control is the control\'s, not the row\'s', async () => {
    const { CodeSection } = await import('../code/CodeSection')
    STORE.loops = [loop({})]
    const navigate = vi.fn()
    render(<CodeSection sub="history" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)

    const stop = await screen.findByRole('button', { name: /^stop$/i })
    stop.focus()
    await userEvent.keyboard('{Enter}')

    await waitFor(() => expect(api.uLoopAction).toHaveBeenCalledWith('c0ffee01', 'stop'))
    expect(navigate).not.toHaveBeenCalled()
  })
})

describe('a project\'s Work board', () => {
  const row = (over: Partial<WorkRow>): WorkRow => ({
    run_id: 'c0ffee01', title: 'Digest titles', state: 'working', source: 'loop', kind: 'code', origin: 'manual',
    project_id: 'p-1', claim: null, collapsed: false, attention: false, resumable: false, outcome: '', ...over,
  })
  const board = (rows: WorkRow[]): WorkBoard => ({
    board: [{ state: 'working', count: rows.length, attention: 0, rows }],
    sections: [{ name: 'loops', items: [], status: 'ok', error: '', loadedAt: 0 }],
    completeness: 'complete', attention: 0, loadedAt: 0,
  })

  it('a loop working there opens on its page and can be stopped from its row', async () => {
    const { WorkBoardColumn } = await import('../projects/ProjectsSection')
    const onOpen = vi.fn()
    const onStop = vi.fn()
    const working = row({})
    render(<WorkBoardColumn work={board([working, row({ run_id: 'run-7', title: 'Nightly digest', source: 'run', kind: '' })])}
      loading={false} onResume={() => {}} onOpen={onOpen} onStop={onStop} />)

    await userEvent.click(screen.getByRole('button', { name: 'Digest titles' }))
    expect(onOpen).toHaveBeenCalledWith(working)
    const card = screen.getByRole('button', { name: 'Digest titles' }).parentElement!
    await userEvent.click(within(card).getByRole('button', { name: /^stop$/i }))
    expect(onStop).toHaveBeenCalledWith(working)
    const run = screen.getByRole('button', { name: 'Nightly digest' }).parentElement!
    expect(within(run).queryByRole('button', { name: /^stop$/i }), 'a run row offered the loop Stop').toBeNull()
  })

  it('each row opens where its own page is', async () => {
    const { workRoute } = await import('../projects/ProjectsSection')
    expect(workRoute(row({}))).toBe('code/c0ffee01')
    expect(workRoute(row({ kind: 'goal' }))).toBe('loops/c0ffee01')
    expect(workRoute(row({ source: 'run', run_id: 'run-7' }))).toBe('workflows/runs/run-7')
    expect(workRoute(row({ source: 'task', run_id: 't-1' }))).toBe('tasks?open=t-1')
  })
})

describe('the Loops list', () => {
  it('says how many code loops are at work and links to the list that has them', async () => {
    const { LoopsListPage } = await import('../loops/LoopsListPage')
    STORE.loops = [loop({}), loop({ id: 'c0ffee02', status: 'stopped' })]
    const onOpenCode = vi.fn()
    render(<LoopsListPage onOpen={() => {}} onCreate={() => {}} onOpenCode={onOpenCode} query={{}} setQuery={() => {}} />)

    expect(await screen.findByText(/1 code loop is at work/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Open the Code list' }))
    expect(onOpenCode).toHaveBeenCalled()
  })
})
