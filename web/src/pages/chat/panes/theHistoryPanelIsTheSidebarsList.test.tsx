import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import type { RouteProps } from '../../../app/useQueryState'
import { runState } from '../../../test/runState'
import type { SubagentCard } from '../chatTypes'
import { useChatPanes } from './useChatPanes'
import { ChatPanes } from './ChatPanes'

// ── The new chat's Chat history panel is the sidebar's chat list ──────────────────────────────────
//
// 🔴 Red before: the panel was a list of its own: the 20 latest of your own chats with their times,
// a View all chats row, an expand control that only widened it ("Expand to full width"), no search,
// and none of the goal loops, code sessions and channel chats the sidebar lists. Now it draws the
// sidebar's list (`ThreadRail`, `useChatList`): the same rows, its own search and its own Sort and
// filter view, opening at the defaults whatever the sidebar keeps and kept nowhere; no New chat; and
// its expand control opens the Chat history page. A failed archive read says so in the list, beside
// its menu, and is read once, not again and again.

vi.mock('../../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (await import('../../../test/quietSocket')).useChatSocket,
}))

const SESSIONS = [
  { key: 'chat-1', title: 'Plan the garden', messages: 2, run_state: runState({ session: 'chat-1' }), origin: 'manual', project_id: '', last_activity_ts: '2026-10-20T11:00:00+00:00' },
  { key: 'tg-1', title: 'Shopping list', messages: 2, run_state: runState({ session: 'tg-1' }), origin: 'channel', source_id: 'C1', source_label: 'Telegram', project_id: '', last_activity_ts: '2026-10-20T09:00:00+00:00' },
]
const LOOPS = [
  { id: 'l1', kind: 'goal', name: 'Find a seed supplier', task: 'find one', status: 'running', total_cycles: 1, max_cycles: 5, started_at: 1_000, project_id: '' },
  { id: 'c1', kind: 'code', name: 'Fence gate', task: 'fix it', status: 'failed', total_cycles: 1, max_cycles: 5, started_at: 900, project_id: '' },
]
const h = vi.hoisted(() => ({ failing: new Set<string>(), reads: {} as Record<string, unknown> }))
vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      if (h.failing.has(prop)) return Promise.reject(new Error(`${prop} failed`))
      const read = h.reads[prop]
      return Promise.resolve(typeof read === 'function' ? read(...args) : prop in h.reads ? read : [])
    },
  })
  return { ...real, api: stub }
})

const { ShellThreadRail } = await import('../../../app/threadRail')
const { resetDataStore } = await import('../../../lib/data/store')
const { GatewayConnectionProvider } = await import('../../../app/gatewayConnection')
const { writeRailView, DEFAULT_VIEW } = await import('../../../app/chatListView')

const TRANSCRIPT = {
  mapOpen: false, isMobile: false, setMapOpen: () => {}, sessionEntries: [],
  pendingMapJump: { current: null }, runPendingMapJump: () => {},
} as unknown as Parameters<typeof ChatPanes>[0]['transcript']

/** The New chat page with its history panel open (`?history=1`), and the sidebar's list beside it. */
function NewChat({ navigate }: { navigate: (route: string) => void }) {
  const [query, setQ] = useState<Record<string, string>>({ history: '1' })
  const setQuery: RouteProps['setQuery'] = (patch) => setQ((prev) => {
    const next = { ...prev }
    for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
    return next
  })
  const [, setSubagents] = useState<SubagentCard[]>([])
  const [, setSideMsgs] = useState<{ q: string; a: string; runId: string; done: boolean }[]>([])
  const [sideBusy, setSideBusy] = useState(false)
  const sideOpenedRef = useRef(false)
  const panes = useChatPanes({
    query, setQuery, focus: false,
    stream: { sessionRef: { current: '' }, turns: [], setSubagents, sideBusy, setSideBusy, setSideMsgs, sideOpenedRef },
  })
  return (
    <>
      <nav aria-label="Sidebar">
        <ShellThreadRail route="chat" sub="new" onOpen={navigate} onLit={() => {}} search="" onSearch={() => {}} />
      </nav>
      <ChatPanes panes={panes} stream={{ subagents: [], sideMsgs: [], sideBusy, workingDir: '', streaming: false, sessionRef: { current: '' } }}
        transcript={TRANSCRIPT} started={false} setQuery={setQuery} navigate={navigate} send={() => {}} />
    </>
  )
}

const panel = () => screen.getByRole('region', { name: 'Chat history' })
const panelList = () => within(panel()).getByRole('group', { name: 'Chats' })
const rows = (el: HTMLElement) => [...el.querySelectorAll('[data-thread-key]')].map((r) => r.getAttribute('aria-label'))
/** Choose *name* in the panel's Sort and filter menu, then close it. */
async function choose(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(within(panel()).getByRole('button', { name: /^Sort and filter/ }))
  await user.click(await screen.findByRole('button', { name: new RegExp(`^${name}`) }))
  await user.click(screen.getByRole('button', { name: 'Done' }))
}

beforeEach(() => {
  h.failing.clear()
  h.reads.chatSessions = SESSIONS
  h.reads.uLoops = LOOPS
  localStorage.clear()
  sessionStorage.clear()
  resetDataStore()
})
afterEach(cleanup)

describe('the Chat history panel', () => {
  it("lists the sidebar's rows, loops, code sessions and channel chats among them, with no New chat and no View all chats", async () => {
    render(<GatewayConnectionProvider><NewChat navigate={vi.fn()} /></GatewayConnectionProvider>)
    await waitFor(() => expect(rows(panelList())).toHaveLength(4))
    expect(rows(panelList())).toEqual(rows(screen.getByRole('group', { name: 'Chat history' })))
    expect(panelList()).toHaveAttribute('data-chat-list', 'panel')
    expect(within(panel()).queryByRole('button', { name: /^New chat/ })).toBeNull()
    expect(within(panel()).queryByRole('button', { name: /View all chats/ })).toBeNull()
    expect(within(panel()).getByRole('searchbox', { name: 'Search chats' })).toBeInTheDocument()
    expect(within(panel()).getByRole('button', { name: /^Sort and filter/ })).toBeInTheDocument()
  })

  it('opens the Chat history page from its expand control, and a row at its own address', async () => {
    const navigate = vi.fn()
    const user = userEvent.setup()
    render(<GatewayConnectionProvider><NewChat navigate={navigate} /></GatewayConnectionProvider>)
    await waitFor(() => expect(rows(panelList())).toHaveLength(4))
    await user.click(within(panel()).getByRole('button', { name: 'Open full page' }))
    expect(navigate).toHaveBeenCalledWith('chat/history')
    await user.click(within(panelList()).getByRole('button', { name: /^Code session: Fence gate/ }))
    expect(navigate).toHaveBeenCalledWith('code/c1')
  })

  it("opens at the default view whatever the sidebar keeps, and its field's id is its own", async () => {
    writeRailView({ ...DEFAULT_VIEW, sort: 'title' })
    render(<GatewayConnectionProvider><NewChat navigate={vi.fn()} /></GatewayConnectionProvider>)
    await waitFor(() => expect(rows(panelList())).toHaveLength(4))
    expect(within(panel()).getByRole('button', { name: /^Sort and filter/ })).toHaveAttribute('aria-label', 'Sort and filter: Recent activity · Active')
    const sidebar = screen.getByRole('navigation', { name: 'Sidebar' })
    expect(within(sidebar).getByRole('button', { name: /^Sort and filter/ })).toHaveAttribute('aria-label', 'Sort and filter: Title (A to Z) · Active')
    const ids = screen.getAllByRole('searchbox', { name: 'Search chats' }).map((f) => f.id)
    expect(new Set(ids).size).toBe(2)
  })

  it('keeps its view nowhere: a sort chosen there never reaches the sidebar, nor a page opened again', async () => {
    const user = userEvent.setup()
    const first = render(<GatewayConnectionProvider><NewChat navigate={vi.fn()} /></GatewayConnectionProvider>)
    await waitFor(() => expect(rows(panelList())).toHaveLength(4))
    await choose(user, 'Title')
    expect(within(panel()).getByRole('button', { name: /^Sort and filter/ })).toHaveAttribute('aria-label', 'Sort and filter: Title (A to Z) · Active')
    expect(localStorage.getItem('chat-list-view')).toBeNull()
    first.unmount()
    render(<GatewayConnectionProvider><NewChat navigate={vi.fn()} /></GatewayConnectionProvider>)
    await waitFor(() => expect(rows(panelList())).toHaveLength(4))
    const sidebar = screen.getByRole('navigation', { name: 'Sidebar' })
    expect(within(sidebar).getByRole('button', { name: /^Sort and filter/ })).toHaveAttribute('aria-label', 'Sort and filter: Recent activity · Active')
  })

  it('says a failed archive read in its own words beside its menu, and reads it once, not again and again', async () => {
    const chatSessions = vi.fn((archived?: boolean) => (archived ? Promise.reject(new Error('archive failed')) : Promise.resolve([])))
    h.reads.chatSessions = chatSessions
    h.reads.uLoops = []
    const user = userEvent.setup()
    render(<GatewayConnectionProvider><NewChat navigate={vi.fn()} /></GatewayConnectionProvider>)
    expect(await within(panel()).findByText('No chats yet')).toBeInTheDocument()
    await choose(user, 'Archived')
    expect(await within(panelList()).findByText("Couldn't load your archived chats.")).toBeInTheDocument()
    expect(within(panel()).queryByRole('alert')).toBeNull()
    expect(within(panel()).getByRole('button', { name: /^Sort and filter/ })).toBeInTheDocument()
    await new Promise((r) => setTimeout(r, 400))
    expect(chatSessions.mock.calls.filter(([archived]) => archived === true).length).toBeLessThanOrEqual(2)
  })

  it('offline, says so as the sidebar does, and shows no LoadError', async () => {
    for (const read of ['system', 'chatSessions', 'uLoops', 'inboxOpen', 'approvals']) h.failing.add(read)
    render(<GatewayConnectionProvider><NewChat navigate={vi.fn()} /></GatewayConnectionProvider>)
    expect(await within(panel()).findByText(/^Offline\./)).toBeInTheDocument()
    expect(within(screen.getByRole('navigation', { name: 'Sidebar' })).getByText(/^Offline\./)).toBeInTheDocument()
    expect(within(panel()).queryByRole('alert')).toBeNull()
  })
})
