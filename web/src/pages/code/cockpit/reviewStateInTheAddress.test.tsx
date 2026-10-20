import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import { useCallback, useRef, useState } from 'react'
import type { CodeProject, TaskItem } from '../../../lib/api'

// ── A code cockpit's review state is in its address ───────────────────────────────────────────
//
// The files rail's tab (`pane`), the open task (`task`) and the one thing the center shows (`file`,
// `diff` or `commit`) are read from the cockpit's address and written back as they change, so a
// reload, Back, Forward, a copied address or a `personalclaw://` link opens the same review. Each
// region is driven here through a route held in memory, which records every write and lets a test
// move the address as Back would.

const h = vi.hoisted(() => ({
  calls: [] as string[], fileRead: vi.fn(), allTasks: vi.fn(), useGitStatus: vi.fn(), useVisiblePoll: vi.fn(),
}))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const handled: Record<string, (...a: unknown[]) => unknown> = { fileRead: h.fileRead, allTasks: h.allTasks }
  // Every call the regions make is recorded by name, so a test can read off that none acts.
  const api = new Proxy(real.api as unknown as Record<string, unknown>, {
    get: (_target, prop: string) => (...args: unknown[]) => {
      h.calls.push(prop)
      return prop in handled ? handled[prop](...args) : Promise.resolve({})
    },
  })
  return { ...real, api }
})
vi.mock('../../files/filesData', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../files/filesData')>()
  return { ...real, useGitStatus: h.useGitStatus }
})
vi.mock('../../../lib/useVisiblePoll', () => ({ useVisiblePoll: h.useVisiblePoll }))

type Patch = Record<string, string | null | undefined>
type Write = { patch: Patch; replace: boolean }
let writes: Write[] = []
/** Moves the address as Back or a link into the open window would, without recording a write;
 *  `alongside` runs in the same update, so both land in one render. */
let go: (query: Record<string, string>, alongside?: () => void) => void = () => {}

/** More writes than any test makes: past it, the address and a region are chasing each other. */
const RUNAWAY = 50

/** A route held in memory: `setQuery` merges as the router's does, and records the write. */
function useMemoryRoute(initial: Record<string, string>) {
  const [query, setQ] = useState(initial)
  const live = useRef(query)
  live.current = query
  go = (next, alongside) => act(() => { setQ(next); alongside?.() })
  const setQuery = useCallback((patch: Patch, opts?: { replace?: boolean }) => {
    writes.push({ patch, replace: !!opts?.replace })
    if (writes.length > RUNAWAY) throw new Error(`${writes.length} writes: the address and the page chase each other`)
    const next = { ...live.current }
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === '') delete next[k]
      else next[k] = v
    }
    live.current = next
    setQ(next)
  }, [])
  return { query, setQuery }
}

/** The calls that would start, send, approve, answer, steer or run something. */
const ACTS = /^(send|create|start|approve|answer|run|resolve|decide|steer|submit|cancel|delete|uLoop(Queue|Nudge|Action)|codeAction|save|write)/i
const acted = () => h.calls.filter((c) => ACTS.test(c))

const WS = '/repo/feed'
let useFilesRail: typeof import('./files/useFilesRail')['useFilesRail']
let useRightPanel: typeof import('./useRightPanel')['useRightPanel']
let useCenterEditor: typeof import('./useCenterEditor')['useCenterEditor']

beforeEach(async () => {
  localStorage.clear()
  h.calls.length = 0
  writes = []
  for (const f of [h.fileRead, h.allTasks, h.useGitStatus, h.useVisiblePoll]) f.mockReset()
  h.fileRead.mockResolvedValue({ content: 'export {}\n' })
  h.useGitStatus.mockReturnValue({ statuses: {} })
  h.allTasks.mockImplementation((q: { task_list: string }) => Promise.resolve({ tasks: LISTS[q.task_list] ?? [] }))
  ;({ useFilesRail } = await import('./files/useFilesRail'))
  ;({ useRightPanel } = await import('./useRightPanel'))
  ;({ useCenterEditor } = await import('./useCenterEditor'))
})
afterEach(() => cleanup())

describe('the files rail tab is in the address (`pane`)', () => {
  const mount = (initial: Record<string, string> = {}) => renderHook(() => {
    const route = useMemoryRoute(initial)
    return { route, rail: useFilesRail({ ws: WS, isProjectDir: false, running: false, ...route }) }
  })

  it('opens on the tab the address names, and on Files for none or another word', () => {
    expect(mount({ pane: 'changes' }).result.current.rail.tab).toBe('changes')
    expect(mount({}).result.current.rail.tab).toBe('files')
    expect(mount({ pane: 'activity' }).result.current.rail.tab).toBe('files')
  })

  it('writes the tab it is switched to, as a replace, with Files as no key', () => {
    const { result } = mount()
    act(() => result.current.rail.setTab('changes'))
    expect(result.current.route.query).toEqual({ pane: 'changes' })
    act(() => result.current.rail.setTab('files'))
    expect(result.current.route.query).toEqual({})
    expect(writes.every((w) => w.replace)).toBe(true)
  })

  it('follows the address when it moves back to the other tab', () => {
    const { result } = mount({ pane: 'changes' })
    go({})
    expect(result.current.rail.tab).toBe('files')
  })

  it('lands ⌘P on the Files tab in the address too', () => {
    const { result } = mount({ pane: 'changes' })
    act(() => { window.dispatchEvent(new CustomEvent('ne:code-focus-find')) })
    expect(result.current.rail.tab).toBe('files')
    expect(result.current.route.query).toEqual({})
  })
})

const task = (id: string, status = 'open'): TaskItem => ({ id, title: `Task ${id}`, status } as TaskItem)
const LISTS: Record<string, TaskItem[]> = { 'l-1': [task('t-1', 'done'), task('t-2', 'in_progress')], 'l-2': [task('t-3'), task('t-4')] }
const project = () => ({
  id: 'loop-1', status: 'running', task_list_ids: { ideation: 'l-1', review: 'l-2' },
  stage_plan: [{ stage: 'ideation', title: 'Ideation' }, { stage: 'review', title: 'Review' }], findings: [],
} as unknown as CodeProject)

describe('the open task is in the address (`task`)', () => {
  const mount = (initial: Record<string, string> = {}, answerWanted = false) => renderHook(({ answerWanted: a }) => {
    const route = useMemoryRoute(initial)
    return { route, panel: useRightPanel({ project: project(), tasksNonce: 0, answerWanted: a, ...route }) }
  }, { initialProps: { answerWanted } })

  it('opens the task the address names, and acts on nothing', async () => {
    const { result } = mount({ task: 't-3' })
    await waitFor(() => expect(result.current.panel.selected?.id).toBe('t-3'))
    expect(acted()).toEqual([])
    expect(writes).toEqual([])
  })

  it('writes the task it opens as a push, so Back closes it, and drops it on close', async () => {
    const { result } = mount()
    await waitFor(() => expect(result.current.panel.loading).toBe(false))
    act(() => result.current.panel.setSelectedTaskId('t-2'))
    expect(result.current.route.query).toEqual({ task: 't-2' })
    expect(writes.at(-1)?.replace).toBe(false)
    go({})
    expect(result.current.panel.selected).toBeNull()
    act(() => result.current.panel.setSelectedTaskId('t-3'))
    act(() => result.current.panel.setSelectedTaskId(null))
    expect(result.current.route.query).toEqual({})
  })

  it('drops a task that is not in the lists from the address, as a replace, once they are read', async () => {
    const { result } = mount({ task: 't-gone' })
    await waitFor(() => expect(result.current.route.query).toEqual({}))
    expect(result.current.panel.selected).toBeNull()
    expect(writes.at(-1)?.replace).toBe(true)
  })

  it('opens no task for an id outside an id\'s shape, and drops it before the lists are read', async () => {
    // The lists never arrive here, so the drop cannot come from the stale-task check: it is the
    // id's own shape that refuses it. The page holds an id to the link parser's rule: an id's
    // characters (no `@`, `~`, space or slash) and at most 128 of them.
    h.allTasks.mockImplementation(() => new Promise(() => {}))
    for (const id of ['t 3/../x', 'a@b', 'a~b', 'a'.repeat(129)]) {
      cleanup()
      writes = []
      const { result } = mount({ task: id })
      await waitFor(() => expect(result.current.route.query, id).toEqual({}))
      expect(result.current.panel.loading, id).toBe(true)
      expect(result.current.panel.selected, id).toBeNull()
      expect(writes, id).toEqual([{ patch: { task: null }, replace: true }])
    }
  })

  it('keeps an id of an id\'s characters at the cap while the lists are read', async () => {
    h.allTasks.mockImplementation(() => new Promise(() => {}))
    const id = 'A-z0.9_:'.repeat(16)
    expect(id).toHaveLength(128)
    const { result } = mount({ task: id })
    await act(async () => {})
    expect(result.current.route.query).toEqual({ task: id })
    expect(writes).toEqual([])
  })

  it("drops the task for the toast's Respond, as a replace", async () => {
    const { result, rerender } = mount({ task: 't-3' })
    await waitFor(() => expect(result.current.panel.selected?.id).toBe('t-3'))
    rerender({ answerWanted: true })
    await waitFor(() => expect(result.current.route.query).toEqual({}))
    expect(writes).toEqual([{ patch: { task: null }, replace: true }])
  })

  it("writes nothing for the toast's Respond when no task is open", async () => {
    const { result, rerender } = mount({ pane: 'changes' })
    await waitFor(() => expect(result.current.panel.loading).toBe(false))
    rerender({ answerWanted: true })
    await act(async () => {})
    expect(result.current.route.query).toEqual({ pane: 'changes' })
    expect(writes).toEqual([])
  })
})

describe('the one thing the center shows is in the address (`file`, `diff`, `commit`)', () => {
  const fire = (name: string, detail: unknown) => act(() => { window.dispatchEvent(new CustomEvent(name, { detail })) })
  const mount = (initial: Record<string, string> = {}, ws = WS) => renderHook(() => {
    const route = useMemoryRoute(initial)
    return { route, center: useCenterEditor({ ws, showTerm: false, onCloseTerm: () => {}, ...route }) }
  })

  it('opens the file the address names, inside the workspace', async () => {
    const { result } = mount({ file: 'src/a.py' })
    await waitFor(() => expect(result.current.center.active?.path).toBe(`${WS}/src/a.py`))
    expect(result.current.center.diff).toBeNull()
    expect(result.current.center.commit).toBeNull()
  })

  it('shows the diff the address names', async () => {
    const { result } = mount({ diff: 'src/a.py' })
    await waitFor(() => expect(result.current.center.diff).toEqual({ path: `${WS}/src/a.py`, name: 'a.py' }))
  })

  it('shows the commit the address names, by its hash only', async () => {
    const { result } = mount({ commit: '0123abc' })
    await waitFor(() => expect(result.current.center.commit).toEqual({ hash: '0123abc', subject: '' }))
  })

  it('restores a review and acts on nothing: only reads go out', async () => {
    const { result } = mount({ diff: 'src/a.py' })
    await waitFor(() => expect(result.current.center.diff).not.toBeNull())
    expect(acted()).toEqual([])
    // Opening what the address names writes nothing back to it.
    await act(async () => {})
    expect(writes).toEqual([])
  })

  it('writes what it shows as it changes, as a replace, naming one of the three', async () => {
    const { result } = mount()
    fire('ne:code-open-file', { name: 'a.py', path: `${WS}/src/a.py`, is_dir: false })
    await waitFor(() => expect(result.current.route.query).toEqual({ file: 'src/a.py' }))
    fire('ne:code-open-diff', { name: 'b.py', path: `${WS}/src/b.py` })
    await waitFor(() => expect(result.current.route.query).toEqual({ diff: 'src/b.py' }))
    fire('ne:code-open-commit', { hash: '0123abc', subject: 'Add the feed' })
    await waitFor(() => expect(result.current.route.query).toEqual({ commit: '0123abc' }))
    act(() => result.current.center.setCommit(null))
    await waitFor(() => expect(result.current.route.query).toEqual({ file: 'src/a.py' }))
    expect(writes.length).toBeGreaterThan(0)
    expect(writes.every((w) => w.replace)).toBe(true)
  })

  it('a file the tab strip held from before does not overwrite what the address names', async () => {
    localStorage.setItem(`files-open-tabs:${WS}`, JSON.stringify([{ name: 'old.py', path: `${WS}/old.py` }]))
    localStorage.setItem(`files-open-tabs:${WS}-active`, `${WS}/old.py`)
    const { result } = mount({ diff: 'src/a.py' })
    // The restored tab is really there, so the check below is not vacuous.
    expect(result.current.center.tabs.map((t) => t.path)).toContain(`${WS}/old.py`)
    await waitFor(() => expect(result.current.center.diff?.path).toBe(`${WS}/src/a.py`))
    expect(result.current.route.query).toEqual({ diff: 'src/a.py' })
  })

  it('follows the address when it moves, as Back does', async () => {
    const { result } = mount({ diff: 'src/a.py' })
    await waitFor(() => expect(result.current.center.diff).not.toBeNull())
    go({ commit: '0123abc' })
    await waitFor(() => expect(result.current.center.commit?.hash).toBe('0123abc'))
    expect(result.current.center.diff).toBeNull()
    go({ file: 'src/b.py' })
    await waitFor(() => expect(result.current.center.active?.path).toBe(`${WS}/src/b.py`))
    expect(result.current.center.commit).toBeNull()
  })

  it('shows nothing an address names twice, or outside the workspace, and corrects the address', async () => {
    const refusedAddresses: Record<string, string>[] = [{ file: 'a.py', diff: 'b.py' }, { diff: '../x' }, { file: '/etc/passwd' }, { commit: 'HEAD' }]
    for (const q of refusedAddresses) {
      cleanup()
      const { result } = mount(q)
      await waitFor(() => expect(result.current.route.query, JSON.stringify(q)).toEqual({}))
      expect(result.current.center.diff, JSON.stringify(q)).toBeNull()
      expect(result.current.center.commit, JSON.stringify(q)).toBeNull()
      expect(result.current.center.active, JSON.stringify(q)).toBeNull()
    }
    // A correction, not a step Back would undo.
    expect(writes.length).toBeGreaterThanOrEqual(refusedAddresses.length)
    expect(writes.every((w) => w.replace)).toBe(true)
  })

  it('closes what it showed when the address moves to one it refuses', async () => {
    const moves: [Record<string, string>, Record<string, string>][] = [
      [{ diff: 'src/a.py' }, { file: 'src/a.py', diff: 'src/b.py' }],
      [{ commit: '0123abc' }, { diff: 'src/b.py', commit: '0123abc' }],
      [{ diff: 'src/a.py' }, { diff: '../x' }],
      [{ commit: '0123abc' }, { commit: '0123abc~1' }],
    ]
    for (const [from, to] of moves) {
      cleanup()
      writes = []
      const { result } = mount(from)
      await waitFor(() => expect(result.current.center.diff ?? result.current.center.commit, JSON.stringify(from)).not.toBeNull())
      go(to)
      await waitFor(() => expect(result.current.route.query, JSON.stringify(to)).toEqual({}))
      expect(result.current.center.diff, JSON.stringify(to)).toBeNull()
      expect(result.current.center.commit, JSON.stringify(to)).toBeNull()
      expect(writes.length, JSON.stringify(to)).toBeGreaterThan(0)
      expect(writes.every((w) => w.replace), JSON.stringify(to)).toBe(true)
    }
  })

  it('opens nothing again for an address the tab restored from an earlier visit already shows', async () => {
    // A reload: the tab strip and the address both hold the file. An open sent again would be a
    // manual open, which stops the center following the worker.
    localStorage.setItem(`files-open-tabs:${WS}`, JSON.stringify([{ name: 'a.py', path: `${WS}/src/a.py` }]))
    localStorage.setItem(`files-open-tabs:${WS}-active`, `${WS}/src/a.py`)
    const opened: unknown[] = []
    const onOpen = (e: Event) => opened.push((e as CustomEvent).detail)
    window.addEventListener('ne:code-open-file', onOpen)
    try {
      const { result } = mount({ file: 'src/a.py' })
      expect(result.current.center.active?.path).toBe(`${WS}/src/a.py`)
      await act(async () => {})
      expect(opened).toEqual([])
      expect(writes).toEqual([])
    } finally { window.removeEventListener('ne:code-open-file', onOpen) }
  })

  it('keeps a clicked commit\'s subject once the address names it', async () => {
    const { result } = mount()
    fire('ne:code-open-commit', { hash: '0123abc', subject: 'Add the feed' })
    await waitFor(() => expect(result.current.route.query).toEqual({ commit: '0123abc' }))
    await act(async () => {})
    expect(result.current.center.commit).toEqual({ hash: '0123abc', subject: 'Add the feed' })
  })

  it('keeps a clicked commit open whose short hash the address cannot hold, and names none of the three', async () => {
    // The Changes panel lists commits by git's short hash, which can be 4 to 6 characters; the
    // address holds a commit only by 7 to 40. The commit stays shown, as a file outside the
    // workspace does, and the address no longer names the file shown before it.
    for (const hash of ['abcd', 'abc12', 'abc123']) {
      cleanup()
      writes = []
      const { result } = mount({ file: 'src/a.py' })
      await waitFor(() => expect(result.current.center.active?.path, hash).toBe(`${WS}/src/a.py`))
      fire('ne:code-open-commit', { hash, subject: 'Short' })
      await waitFor(() => expect(result.current.route.query, hash).toEqual({}))
      await act(async () => {})
      expect(result.current.center.commit, hash).toEqual({ hash, subject: 'Short' })
      expect(writes.every((w) => w.replace), hash).toBe(true)
      // Closed, the center shows the file again, and the address names it again.
      act(() => result.current.center.setCommit(null))
      await waitFor(() => expect(result.current.route.query, hash).toEqual({ file: 'src/a.py' }))
    }
  })

  it('writes no file whose path does not stay inside the workspace', async () => {
    // The cockpit closes a tab outside its workspace at once; a path under it that climbs out
    // with `..` stays open as a tab, and is still not written to the address.
    const { result } = mount()
    fire('ne:code-open-file', { name: 'x.py', path: `${WS}/../x.py`, is_dir: false })
    await waitFor(() => expect(result.current.center.active?.path).toBe(`${WS}/../x.py`))
    expect(result.current.route.query).toEqual({})
    expect(writes).toEqual([])
  })

  it('reads nothing from the address, and writes nothing to it, before the cockpit knows its workspace', async () => {
    const { result } = mount({ diff: 'src/a.py' }, '')
    await act(async () => {})
    expect(result.current.center.diff).toBeNull()
    fire('ne:code-open-commit', { hash: '0123abc', subject: 'Add the feed' })
    await act(async () => {})
    expect(result.current.center.commit?.hash).toBe('0123abc')
    expect(result.current.route.query).toEqual({ diff: 'src/a.py' })
    expect(writes).toEqual([])
  })

  it('lets the address win when it moves in the same render as what is shown', async () => {
    // Back lands while the center opens another file: the address opens what it names, and the
    // file is not written over it, so the two settle instead of chasing each other.
    const { result } = mount({ file: 'src/a.py' })
    await waitFor(() => expect(result.current.center.active?.path).toBe(`${WS}/src/a.py`))
    go({ diff: 'src/b.py' }, () => {
      window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name: 'c.py', path: `${WS}/src/c.py`, is_dir: false } }))
    })
    await waitFor(() => expect(result.current.center.diff?.path).toBe(`${WS}/src/b.py`))
    await act(async () => {})
    expect(result.current.route.query).toEqual({ diff: 'src/b.py' })
    expect(writes).toEqual([])
  })

  it('opens what the address names once the workspace is known, over a tab held from before', async () => {
    // Before the workspace is known the tab strip holds the tabs of no workspace, so a tab from
    // an earlier visit is shown; once it is known, the address wins and nothing is written.
    localStorage.setItem('files-open-tabs', JSON.stringify([{ name: 'old.py', path: `${WS}/old.py` }]))
    localStorage.setItem('files-open-tabs-active', `${WS}/old.py`)
    const { result, rerender } = renderHook(({ ws }) => {
      const route = useMemoryRoute({ diff: 'src/a.py' })
      return { route, center: useCenterEditor({ ws, showTerm: false, onCloseTerm: () => {}, ...route }) }
    }, { initialProps: { ws: '' } })
    expect(result.current.center.active?.path).toBe(`${WS}/old.py`)
    rerender({ ws: WS })
    await waitFor(() => expect(result.current.center.diff?.path).toBe(`${WS}/src/a.py`))
    await act(async () => {})
    expect(result.current.route.query).toEqual({ diff: 'src/a.py' })
    expect(writes).toEqual([])
  })

  it('writes no tab held from before into an empty address once the workspace is known', async () => {
    // As on a mount where the workspace is known at once: a restored tab is shown, not written.
    localStorage.setItem('files-open-tabs', JSON.stringify([{ name: 'old.py', path: `${WS}/old.py` }]))
    localStorage.setItem('files-open-tabs-active', `${WS}/old.py`)
    const { result, rerender } = renderHook(({ ws }) => {
      const route = useMemoryRoute({})
      return { route, center: useCenterEditor({ ws, showTerm: false, onCloseTerm: () => {}, ...route }) }
    }, { initialProps: { ws: '' } })
    rerender({ ws: WS })
    await act(async () => {})
    expect(result.current.center.active?.path).toBe(`${WS}/old.py`)
    expect(result.current.route.query).toEqual({})
    expect(writes).toEqual([])
  })
})
