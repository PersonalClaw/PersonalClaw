/** A code cockpit's diff review opens in the shared pane host, beside the editor.
 *
 *  The diff the Changes tab opens, or the address names (`diff=<path>`), used to take the editor's
 *  place. It now docks in the pane host beside the editor, at the width the cockpit last left it,
 *  and its close gives focus back to the row it was opened from. What it shows, its address and
 *  how it closes are as before: the address still names one of a file, a diff or a commit, and
 *  a diff it refuses opens no pane. This renders the whole page on a route held in memory.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useCallback, useRef, useState } from 'react'

const h = vi.hoisted(() => {
  const WS = '/repo/feed'
  const loop = {
    id: 'loop-1', name: 'Feed refetch fix', kind: 'code', status: 'running', workspace_dir: WS, files_dir: '/pc/loops/loop-1',
    plan: [{ stage: 'build', title: 'Build' }], phase_status: { build: 'active' }, task_list_ids: { build: 'l-1' },
    kind_config: { project_kind: 'brownfield' },
  }
  /** The reads a cockpit makes: the project, its tasks, the workspace's git state, with one
   *  changed file, and a diff's two sides. */
  const reads: Record<string, () => Promise<unknown>> = {
    uLoop: () => Promise.resolve(loop),
    allTasks: () => Promise.resolve({ tasks: [] }),
    agentProviders: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
    fileList: () => Promise.resolve({ entries: [] }),
    fileGitStatus: () => Promise.resolve({ branch: 'main', statuses: { [`${WS}/src/a.py`]: ' M' }, repoRoot: WS }),
    fileGitLog: () => Promise.resolve({ repoRoot: WS, commits: [] }),
    fileRead: () => Promise.resolve({ content: 'export {}\n' }),
    fileGitOriginal: () => Promise.resolve({ content: '' }),
  }
  return { calls: [] as string[], reads }
})

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
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
/** Moves the address as Back would, without recording a write. */
let go: (query: Record<string, string>) => void = () => {}

function Cockpit({ initial }: { initial: Record<string, string> }) {
  const [query, setQ] = useState(initial)
  const live = useRef(query)
  live.current = query
  address = query
  go = (next) => act(() => { live.current = next; setQ(next) })
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

const diffPane = () => screen.findByRole('region', { name: 'Diff — a.py' })
/** The editor's own surface, shown while no file is open. */
const editor = () => screen.getByText('Open a file from the tree to view + edit it.')
const follows = (a: Node, b: Node) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

describe("a code cockpit's diff opens in the pane host", () => {
  it('opens the diff the address names in a pane beside the editor, at the remembered width, and acts on nothing', async () => {
    localStorage.setItem('pane-host-code-w', '560')
    render(<Cockpit initial={{ diff: 'src/a.py' }} />)
    const pane = await diffPane()
    expect(follows(editor(), pane)).toBe(true)
    expect(pane.style.width).toBe('560px')
    expect(screen.getByRole('separator', { name: 'Resize diff — arrow keys to resize' })).toHaveAttribute('aria-valuenow', '560')
    expect(address).toEqual({ diff: 'src/a.py' })
    expect(writes).toEqual([])
    expect(h.calls.filter((c) => !(c in h.reads))).toEqual([])
  })

  it('follows the address as Back moves it, and opens none it refuses', async () => {
    render(<Cockpit initial={{ diff: 'src/a.py' }} />)
    await diffPane()
    go({ diff: 'src/b.py' })
    expect(await screen.findByRole('region', { name: 'Diff — b.py' })).toBeInTheDocument()
    go({ commit: '0123abc' })
    await waitFor(() => expect(screen.queryByRole('region', { name: /^Diff/ })).toBeNull())
    cleanup()
    render(<Cockpit initial={{ diff: '../a.py' }} />)
    await waitFor(() => expect(address).toEqual({}))
    expect(screen.queryByRole('region', { name: /^Diff/ })).toBeNull()
  })

  it('opens from the Changes tab, keeps a resize out of the address, and gives focus back to the row on close', async () => {
    render(<Cockpit initial={{ pane: 'changes' }} />)
    const row = await screen.findByRole('button', { name: /src\/a\.py/ })
    row.focus()
    fireEvent.click(row)
    await diffPane()
    await waitFor(() => expect(address).toEqual({ pane: 'changes', diff: 'src/a.py' }))
    const writesBefore = writes.length
    fireEvent.keyDown(screen.getByRole('separator', { name: 'Resize diff — arrow keys to resize' }), { key: 'ArrowLeft' })
    expect(screen.getByRole('separator', { name: 'Resize diff — arrow keys to resize' })).toHaveAttribute('aria-valuenow', '496')
    expect(writes).toHaveLength(writesBefore)
    const close = screen.getByRole('button', { name: 'Close diff' })
    close.focus()
    fireEvent.click(close)
    await waitFor(() => expect(address).toEqual({ pane: 'changes' }))
    expect(screen.queryByRole('region', { name: /^Diff/ })).toBeNull()
    expect(document.activeElement).toBe(row)
  })
})
