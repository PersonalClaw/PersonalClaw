/** A code session opens with its review pane on its uncommitted changes (#3821).
 *
 *  A cockpit opened at its bare address (`#/code/<id>`, the list's and the palette's link) opens the
 *  pane beside the editor on the first uncommitted change's diff, and the pane lists every change to
 *  step through, the one shown marked. The address names the change shown (`diff=<path>`), as a
 *  replace, so a reload, Back or a copied address opens the same review. An address that names
 *  anything else is followed as it is, and a clean tree opens nothing. A review closed from the
 *  pane stays closed when the page reloads in that tab. This renders the whole page on a route held
 *  in memory.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useCallback, useRef, useState } from 'react'

const h = vi.hoisted(() => {
  const WS = '/repo/feed'
  const loop = {
    id: 'loop-1', name: 'Feed refetch fix', kind: 'code', status: 'running', workspace_dir: WS, files_dir: '/pc/loops/loop-1',
    plan: [{ stage: 'build', title: 'Build' }], phase_status: { build: 'active' }, task_list_ids: { build: 'l-1' },
    kind_config: { project_kind: 'brownfield' },
  }
  const two = { [`${WS}/src/a.py`]: ' M', [`${WS}/src/b.py`]: '??' }
  const state = { statuses: two as Record<string, string> }
  const reads: Record<string, () => Promise<unknown>> = {
    uLoop: () => Promise.resolve(loop),
    allTasks: () => Promise.resolve({ tasks: [] }),
    agentProviders: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
    fileList: () => Promise.resolve({ entries: [] }),
    fileGitStatus: () => Promise.resolve({ branch: 'main', statuses: state.statuses, repoRoot: WS }),
    fileGitLog: () => Promise.resolve({ repoRoot: WS, commits: [] }),
    fileRead: () => Promise.resolve({ content: 'export {}\n' }),
    fileGitOriginal: () => Promise.resolve({ content: '' }),
  }
  return { calls: [] as string[], reads, state, two }
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
const { registerBuiltinContentTypes } = await import('../../ui/content/registerBuiltins')
// An address that names a file opens it in the editor, which resolves its content type.
registerBuiltinContentTypes()
// …and scrolls its tab into view, which jsdom does not draw.
Element.prototype.scrollIntoView = () => {}

type Patch = Record<string, string | null | undefined>
let writes: { patch: Patch; replace: boolean }[] = []
let address: Record<string, string> = {}

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
  sessionStorage.clear()
  h.calls.length = 0
  h.state.statuses = h.two
  writes = []
  address = {}
})
afterEach(cleanup)

const review = (name: string) => screen.findByRole('region', { name: `Diff — ${name}` })
/** The review opened on `a.py` and the address names it, as the page is once it has settled. */
const opened = async () => {
  const pane = await review('a.py')
  await waitFor(() => expect(address).toEqual({ diff: 'src/a.py' }))
  return pane
}
const noReview = () => expect(screen.queryByRole('region', { name: /^Diff/ })).toBeNull()
/** The page has read the workspace's changes and had its chance to act on them. */
const settled = async () => {
  await waitFor(() => expect(h.calls).toContain('fileGitStatus'))
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}
const changes = (pane: HTMLElement) => within(within(pane).getByRole('list', { name: 'Uncommitted changes' }))
const current = (pane: HTMLElement) => changes(pane).getAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'true')

describe('a code session opens on its uncommitted changes', () => {
  it('opens the pane on the first change at the bare address, and lists every change with the one shown marked', async () => {
    render(<Cockpit initial={{}} />)
    const pane = await review('a.py')
    await waitFor(() => expect(address).toEqual({ diff: 'src/a.py' }))
    expect(writes.every((w) => w.replace), 'opening it is not a history step').toBe(true)
    expect(changes(pane).getAllByRole('button').map((b) => b.textContent)).toEqual(['modifiedsrc/a.py', 'newsrc/b.py'])
    expect(current(pane).map((b) => b.textContent)).toEqual(['modifiedsrc/a.py'])
    expect(screen.getByText('Open a file from the tree to view + edit it.'), 'the editor stays beside it').toBeInTheDocument()
  })

  it('shows the change chosen in the pane, and the address follows', async () => {
    render(<Cockpit initial={{}} />)
    fireEvent.click(changes(await opened()).getByRole('button', { name: /src\/b\.py/ }))
    const pane = await review('b.py')
    await waitFor(() => expect(address).toEqual({ diff: 'src/b.py' }))
    expect(current(pane).map((b) => b.textContent)).toEqual(['newsrc/b.py'])
  })

  it('stays closed after a close when the tab reloads, and a new tab opens it again', async () => {
    render(<Cockpit initial={{}} />)
    await opened()
    fireEvent.click(screen.getByRole('button', { name: 'Close diff' }))
    await waitFor(() => expect(address).toEqual({}))
    cleanup()
    h.calls.length = 0
    render(<Cockpit initial={{}} />)
    await settled()
    noReview()
    cleanup()
    sessionStorage.clear()
    render(<Cockpit initial={{}} />)
    expect(await review('a.py')).toBeInTheDocument()
  })

  it.each([
    ['a file', { file: 'src/a.py' }],
    ['a commit', { commit: '0123abc' }],
    ['the rail tab', { pane: 'changes' }],
    ['a task', { task: 't-1' }],
    ['a refused path', { diff: '../a.py' }],
  ])('opens no review of its own at an address that names %s', async (_what, initial) => {
    render(<Cockpit initial={initial} />)
    await settled()
    noReview()
    expect(address.diff, 'the address is followed, not replaced').toBeUndefined()
  })

  it('opens nothing on a clean tree', async () => {
    h.state.statuses = {}
    render(<Cockpit initial={{}} />)
    await settled()
    noReview()
    expect(address).toEqual({})
  })
})
