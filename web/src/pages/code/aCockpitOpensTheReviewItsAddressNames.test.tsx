/** A code cockpit opens the review its address names, and writes back what is clicked.
 *
 *  The files rail's tab (`pane`), the one thing the center shows (`diff` here) and the open task
 *  (`task`) are kept in the cockpit's address by its three regions' controllers. Those read the
 *  address only when the page hands it down, and fall back to local state when it does not, so
 *  this renders the whole page on a route held in memory: if the page, or a region, stops passing
 *  the address on, the review is lost on reload again and these turn red.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useCallback, useRef, useState } from 'react'

const h = vi.hoisted(() => {
  const WS = '/repo/feed'
  const loop = {
    id: 'loop-1', name: 'Feed refetch fix', kind: 'code', status: 'running', workspace_dir: WS, files_dir: '/pc/loops/loop-1',
    plan: [{ stage: 'build', title: 'Build' }], phase_status: { build: 'active' }, task_list_ids: { build: 'l-1' },
    kind_config: { project_kind: 'brownfield' },
  }
  /** The reads a restored cockpit makes: the project, its tasks, the workspace's git state and a
   *  diff's two sides. */
  const reads: Record<string, () => Promise<unknown>> = {
    uLoop: () => Promise.resolve(loop),
    allTasks: () => Promise.resolve({ tasks: [{ id: 't-3', title: 'Honour the feed ETag', status: 'open' }] }),
    agentProviders: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
    fileList: () => Promise.resolve({ entries: [] }),
    fileGitStatus: () => Promise.resolve({ branch: 'main', statuses: {}, repoRoot: WS }),
    fileGitLog: () => Promise.resolve({ repoRoot: WS, commits: [] }),
    fileRead: () => Promise.resolve({ content: 'export {}\n' }),
    fileGitOriginal: () => Promise.resolve({ content: '' }),
  }
  return { calls: [] as string[], reads }
})

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  // Every call is recorded by name; one that is not a read above answers as empty.
  const api = new Proxy(real.api as unknown as Record<string, unknown>, {
    get: (_target, prop: string) => () => {
      h.calls.push(prop)
      return prop in h.reads ? h.reads[prop]() : Promise.resolve({})
    },
  })
  return { ...real, api }
})
vi.mock('../loops/useRunStream', () => ({ useRunStream: () => ({ connected: true }) }))
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...real, useChatSocket: () => {} }
})
vi.mock('../../lib/useWorkspaceMissing', () => ({ useWorkspaceMissing: () => false }))
vi.mock('../../lib/useVisiblePoll', () => ({ useVisiblePoll: () => {} }))

const { CodeCockpitPage } = await import('./CodeCockpitPage')

type Patch = Record<string, string | null | undefined>
let writes: { patch: Patch; replace: boolean }[] = []
let address: Record<string, string> = {}

/** The page on a route held in memory: `setQuery` merges as the router's does, and records it. */
function Cockpit({ initial }: { initial: Record<string, string> }) {
  const [query, setQ] = useState(initial)
  const live = useRef(query)
  live.current = query
  address = query
  const setQuery = useCallback((patch: Patch, opts?: { replace?: boolean }) => {
    writes.push({ patch, replace: !!opts?.replace })
    const next = { ...live.current }
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === '') delete next[k]
      else next[k] = v
    }
    live.current = next
    setQ(next)
  }, [])
  return <CodeCockpitPage id="loop-1" onBack={() => {}} onDeleted={() => {}} query={query} setQuery={setQuery} />
}

beforeEach(() => {
  localStorage.clear()
  h.calls.length = 0
  writes = []
  address = {}
})
afterEach(cleanup)

/** The open task's steer box: only a task's detail view has one. */
const steerBox = () => screen.findByPlaceholderText<HTMLTextAreaElement>(/^Steer “Honour the feed ETag/)

describe('a code cockpit opens the review its address names', () => {
  it('shows the rail tab, the diff and the task the address names, and acts on nothing', async () => {
    render(<Cockpit initial={{ pane: 'changes', diff: 'src/a.py', task: 't-3' }} />)
    expect((await steerBox()).value).toBe('')
    await screen.findByText('Diff — a.py')
    expect(screen.getByRole('button', { name: 'Changes' }).getAttribute('style')).toContain('--color-surface-high')
    expect(screen.getByRole('button', { name: 'Files' }).getAttribute('style')).not.toContain('--color-surface-high')
    expect(address).toEqual({ pane: 'changes', diff: 'src/a.py', task: 't-3' })
    expect(writes).toEqual([])
    // Only reads went out: nothing was queued, steered, sent or run.
    expect(h.calls.filter((c) => !(c in h.reads))).toEqual([])
    expect(h.calls).toContain('fileGitOriginal')
  })

  it('writes what is clicked in each region back to the address', async () => {
    render(<Cockpit initial={{ pane: 'changes', diff: 'src/a.py', task: 't-3' }} />)
    await steerBox()
    await screen.findByText('Diff — a.py')
    fireEvent.click(screen.getByRole('button', { name: 'Files' }))
    await waitFor(() => expect(address).toEqual({ diff: 'src/a.py', task: 't-3' }))
    expect(writes.at(-1)).toEqual({ patch: { pane: null }, replace: true })
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    await waitFor(() => expect(address).toEqual({ task: 't-3' }))
    expect(writes.at(-1)?.replace).toBe(true)
    // Closing the task is a step Back undoes.
    fireEvent.click(screen.getByRole('button', { name: 'Tasks' }))
    await waitFor(() => expect(address).toEqual({}))
    expect(writes.at(-1)).toEqual({ patch: { task: null }, replace: false })
    expect(screen.queryByPlaceholderText(/^Steer “Honour the feed ETag/)).toBeNull()
  })
})
