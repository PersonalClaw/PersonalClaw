import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import { useRef, useState } from 'react'
import type { RouteProps } from '../../../app/useQueryState'
import { userTurn, assistantTurn, type ChatTurn, type SubagentCard } from '../chatTypes'
import { requestToolResultFull } from '../toolResultBridge'

// ── The chat's docked panes ─────────────────────────────────────────────────────────────────────
//
// `useChatPanes` keeps the panels beside the chat: which are open (each on the URL, so Back closes
// it), what the Activity rail lists, a tool's full result fetched when its card asks, the subagents'
// one-click cancel and the side chat. Driven here against a URL and a stream it is handed, the way
// ChatSession hands it them.

const SESSION = 'chat-7-panes'

const h = vi.hoisted(() => ({ cancelFanout: vi.fn(), sideOpen: vi.fn(), sideTurn: vi.fn(), notify: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, cancelFanout: h.cancelFanout, sideOpen: h.sideOpen, sideTurn: h.sideTurn } }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

let useChatPanes: typeof import('./useChatPanes')['useChatPanes']

type SideMsg = { q: string; a: string; runId: string; done: boolean }
const TURNS: ChatTurn[] = [
  userTurn('Fix the feed'),
  assistantTurn('Edited `src/fetch.py`; see https://docs.example.com/etag for the header.'),
]
const CARDS: SubagentCard[] = [
  { id: 'a', task: 'scan', agent: 'researcher', done: false },
  { id: 'b', task: 'test', agent: 'coder', done: true },
  { id: 'c', task: 'step', agent: 'coder', run: 'run-1', done: false },
]

/** The page around the controller: a URL that holds the flags, and the stream's state it reads. */
function mount() {
  const hook = renderHook(() => {
    const [query, setQ] = useState<Record<string, string>>({})
    const setQuery: RouteProps['setQuery'] = (patch) => setQ((prev) => {
      const next = { ...prev }
      for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
      return next
    })
    const [subagents, setSubagents] = useState<SubagentCard[]>(CARDS)
    const [sideMsgs, setSideMsgs] = useState<SideMsg[]>([])
    const [sideBusy, setSideBusy] = useState(false)
    const sideOpenedRef = useRef(false)
    const p = useChatPanes({
      query, setQuery,
      stream: { sessionRef: { current: SESSION }, turns: TURNS, setSubagents, sideBusy, setSideBusy, setSideMsgs, sideOpenedRef },
    })
    return { p, query, subagents, sideMsgs, sideBusy }
  })
  return hook
}

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.cancelFanout.mockResolvedValue({})
  h.sideOpen.mockResolvedValue({})
  h.sideTurn.mockResolvedValue({ run_id: 'side-1' })
  ;({ useChatPanes } = await import('./useChatPanes'))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('useChatPanes keeps each panel on the URL', () => {
  it('opens and closes the Activity rail', () => {
    const { result } = mount()
    act(() => result.current.p.setActivityOpen(true))
    expect(result.current.query.activity).toBe('1')
    expect(result.current.p.activityOpen).toBe(true)
    act(() => result.current.p.setActivityOpen(false))
    expect(result.current.query.activity).toBeUndefined()
  })

  it('opens a file beside the chat, and closes it', () => {
    const { result } = mount()
    act(() => result.current.p.setOpenFile('src/fetch.py'))
    expect(result.current.query.file).toBe('src/fetch.py')
    expect(result.current.p.openFile).toBe('src/fetch.py')
    act(() => result.current.p.setOpenFile(null))
    expect(result.current.query.file).toBeUndefined()
    expect(result.current.p.openFile).toBeNull()
  })

  it('lists the files and links the transcript touched', () => {
    const { result } = mount()
    expect(result.current.p.activity.files.map((f) => f.path)).toEqual(['src/fetch.py'])
    expect(result.current.p.activity.links.map((l) => l.label)).toEqual(['docs.example.com'])
  })
})

describe("useChatPanes shows a tool's full result", () => {
  it('opens on the card asking, and fetches the body from the session', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ content: 'line 1\nline 2', length: 13 }))))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = mount()
    act(() => requestToolResultFull('raw-9', 'Terminal'))
    expect(result.current.query.result).toBe('raw-9')
    expect(result.current.p.resultToolRef.current).toBe('Terminal')
    await waitFor(() => expect(result.current.p.resultBody).toEqual({ content: 'line 1\nline 2', length: 13 }))
    expect(fetchMock).toHaveBeenCalledWith(`/api/chat/sessions/${SESSION}/tool-result/raw-9`, expect.anything())
  })

  it("says the gateway's reason when the body cannot be read", async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ error: 'evicted' }), { status: 410 }))))
    const { result } = mount()
    act(() => requestToolResultFull('raw-9', 'Terminal'))
    await waitFor(() => expect(result.current.p.resultBody?.content).toBe("(couldn't load the full result: evicted)"))
  })
})

describe('useChatPanes acts for the Activity rail', () => {
  it("cancels this chat's running subagents, marking them first and leaving a batch's alone", async () => {
    const { result } = mount()
    await act(async () => { await result.current.p.killFanout() })
    expect(h.cancelFanout).toHaveBeenCalledWith(SESSION)
    expect(result.current.subagents.map((c) => [c.id, c.done, c.error ?? null])).toEqual([
      ['a', true, 'cancelled'], ['b', true, null], ['c', false, null],
    ])
  })

  it('says so when the cancel fails', async () => {
    h.cancelFanout.mockRejectedValue(new Error('gateway restarted'))
    const { result } = mount()
    await act(async () => { await result.current.p.killFanout() })
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining('cancel the subagents'), 'error')
  })

  it('opens the side chat once and shows a question at once', async () => {
    const { result } = mount()
    await act(async () => { await result.current.p.askSide('  what changed?  ') })
    expect(h.sideOpen).toHaveBeenCalledTimes(1)
    expect(h.sideTurn).toHaveBeenCalledWith(SESSION, 'what changed?')
    expect(result.current.sideMsgs).toEqual([{ q: 'what changed?', a: '', runId: 'side-1', done: false }])
    expect(result.current.sideBusy).toBe(true)
    await act(async () => { await result.current.p.openSide() })
    expect(h.sideOpen).toHaveBeenCalledTimes(1)
  })

  it('keeps a side question that failed, with the reason as its answer', async () => {
    h.sideTurn.mockRejectedValue(new Error('no model chosen'))
    const { result } = mount()
    await act(async () => { await result.current.p.askSide('what changed?') })
    expect(result.current.sideMsgs).toEqual([{ q: 'what changed?', a: '⚠️ Couldn’t answer: no model chosen', runId: '', done: true }])
    expect(result.current.sideBusy).toBe(false)
  })
})
