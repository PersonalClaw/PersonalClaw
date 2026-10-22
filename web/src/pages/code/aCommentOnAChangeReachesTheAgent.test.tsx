/** A comment on a change in the review pane reaches the agent as guidance for its next step (#3821).
 *
 *  Under a change's diff the review pane holds a comment field: a labelled, native text field. The
 *  lines selected in the diff are quoted with it. Nothing is sent until Send. While the loop is at
 *  work the comment goes to its next cycle (`POST /api/loops/{id}/nudge`), with the task whose work
 *  touched that file, so the guidance lands on that task. Once the loop has ended there is no next
 *  step to guide, so the comment opens a new chat about it, as a comment on a page with no live
 *  conversation does. An unsent comment is kept for that change, so it comes back after a reload.
 *  This renders the whole cockpit on a route held in memory.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useCallback, useRef, useState } from 'react'

const h = vi.hoisted(() => {
  const WS = '/repo/feed'
  const loop = (status: string) => ({
    id: 'loop-1', name: 'Feed refetch fix', kind: 'code', status, workspace_dir: WS, files_dir: '/pc/loops/loop-1',
    plan: [{ stage: 'build', title: 'Build' }], phase_status: { build: 'active' }, task_list_ids: { build: 'l-1' },
    kind_config: { project_kind: 'brownfield' },
    findings: [{ cycle: 1, task_id: 't-7', files_touched: [`${WS}/src/a.py`] }],
  })
  const state = { status: 'running' }
  const calls: { name: string; args: unknown[] }[] = []
  const reads: Record<string, (...a: unknown[]) => Promise<unknown>> = {
    uLoop: () => Promise.resolve(loop(state.status)),
    allTasks: () => Promise.resolve({ tasks: [] }),
    agentProviders: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
    fileList: () => Promise.resolve({ entries: [] }),
    fileGitStatus: () => Promise.resolve({ branch: 'main', statuses: { [`${WS}/src/a.py`]: ' M' }, repoRoot: WS }),
    fileGitLog: () => Promise.resolve({ repoRoot: WS, commits: [] }),
    fileRead: () => Promise.resolve({ content: 'line one\nline two\nline three\n' }),
    fileGitOriginal: () => Promise.resolve({ content: 'line one\n' }),
    uLoopNudge: () => Promise.resolve({ ok: true }),
    createChatSession: () => Promise.resolve({ key: 'c-9' }),
    renameSession: () => Promise.resolve({}),
    sendChat: () => Promise.resolve({}),
  }
  /** The diff's modified side: what a selection in it reports. */
  const diff = { select: (_s: { startLineNumber: number; endLineNumber: number } | null) => {} }
  return { calls, reads, state, diff }
})

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  const api = new Proxy(real.api as unknown as Record<string, unknown>, {
    get: (_target, prop: string) => (...args: unknown[]) => {
      h.calls.push({ name: prop, args })
      return prop in h.reads ? h.reads[prop](...args) : Promise.resolve({})
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
// Monaco draws nothing in jsdom: a diff editor that mounts with a modified side whose selection the
// test sets, as a person dragging over lines would.
vi.mock('@monaco-editor/react', async () => {
  const { useEffect } = await import('react')
  return {
    default: () => null,
    DiffEditor: ({ modified, onMount }: { modified: string; onMount?: (e: unknown, m: unknown) => void }) => {
      useEffect(() => {
        const lines = modified.split('\n')
        const listeners: ((e: unknown) => void)[] = []
        const model = { getLineContent: (n: number) => lines[n - 1] ?? '', getLineCount: () => lines.length }
        const side = { getModel: () => model, onDidChangeCursorSelection: (cb: (e: unknown) => void) => { listeners.push(cb); return { dispose: () => {} } } }
        h.diff.select = (s) => listeners.forEach((cb) => cb({
          selection: s ? { ...s, startColumn: 1, endColumn: 4, isEmpty: () => false } : { startLineNumber: 1, endLineNumber: 1, startColumn: 1, endColumn: 1, isEmpty: () => true },
        }))
        onMount?.({ getModifiedEditor: () => side, setModel: () => {} }, {})
      }, [modified, onMount])
      return <pre>{modified}</pre>
    },
  }
})

const { CodeCockpitPage } = await import('./CodeCockpitPage')

type Patch = Record<string, string | null | undefined>
const navigate = vi.fn()
function Cockpit({ initial }: { initial: Record<string, string> }) {
  const [query, setQ] = useState(initial)
  const live = useRef(query)
  live.current = query
  const setQuery = useCallback((patch: Patch) => {
    const next = { ...live.current }
    for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
    live.current = next
    setQ(next)
  }, [])
  return <CodeCockpitPage id="loop-1" onBack={() => {}} onDeleted={() => {}} navigate={navigate} query={query} setQuery={setQuery} />
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  h.calls.length = 0
  h.state.status = 'running'
  navigate.mockReset()
})
afterEach(cleanup)

const field = (rel = 'src/a.py') => screen.findByRole('textbox', { name: `Comment on ${rel}` })
const sent = (name: string) => h.calls.filter((c) => c.name === name).map((c) => c.args)

describe('a comment on a change', () => {
  it('is a labelled field, is sent only on Send, and reaches the next cycle with the lines and the task', async () => {
    render(<Cockpit initial={{ diff: 'src/a.py' }} />)
    const box = await field()
    expect(box.tagName).toBe('TEXTAREA')
    fireEvent.change(box, { target: { value: 'Keep the retry, drop the sleep.' } })
    act(() => h.diff.select({ startLineNumber: 2, endLineNumber: 3 }))
    expect(await screen.findByText('On lines 2–3')).toBeInTheDocument()
    expect(sent('uLoopNudge'), 'nothing is sent while it is written').toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Send to the agent' }))
    await waitFor(() => expect(sent('uLoopNudge')).toEqual([[
      'loop-1', 'On `src/a.py`, lines 2–3:\n> line two\n> line three\n\nKeep the retry, drop the sleep.', 't-7',
    ]]))
    await waitFor(() => expect(box).toHaveValue(''))
  })

  it('goes to the loop without a task when no task touched the file, and with no lines when none are selected', async () => {
    render(<Cockpit initial={{ diff: 'src/b.py' }} />)
    fireEvent.change(await field('src/b.py'), { target: { value: 'Name this after what it fetches.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send to the agent' }))
    await waitFor(() => expect(sent('uLoopNudge')).toEqual([['loop-1', 'On `src/b.py`:\n\nName this after what it fetches.', undefined]]))
  })

  it('comes back unsent after a reload, and is gone once sent', async () => {
    render(<Cockpit initial={{ diff: 'src/a.py' }} />)
    fireEvent.change(await field(), { target: { value: 'Half a thought' } })
    cleanup()
    render(<Cockpit initial={{ diff: 'src/a.py' }} />)
    const box = await field()
    expect(box).toHaveValue('Half a thought')
    expect(sent('uLoopNudge')).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Send to the agent' }))
    await waitFor(() => expect(sent('uLoopNudge')).toHaveLength(1))
    cleanup()
    render(<Cockpit initial={{ diff: 'src/a.py' }} />)
    expect(await field()).toHaveValue('')
  })

  it('opens a new chat about it once the loop has ended', async () => {
    h.state.status = 'complete'
    render(<Cockpit initial={{ diff: 'src/a.py' }} />)
    fireEvent.change(await field(), { target: { value: 'Why a sleep here?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send to a new chat' }))
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('chat/c-9'))
    expect(sent('sendChat')).toEqual([['On `src/a.py`:\n\nWhy a sleep here?', 'c-9', undefined]])
    expect(sent('uLoopNudge')).toEqual([])
  })
})
