import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor, render, screen, fireEvent } from '@testing-library/react'
import { useState, type ReactNode } from 'react'
import type { ChatSessionSummary } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'

// ── A chat's Delete asks whether the workflow runs it started itself stop with it ────────────────
//
// Deleting a chat ends every helper it started, and the runs those helpers started; the workflow
// runs the chat started itself went on, and the dialog said nothing of them. It now reads how many
// are still going (`GET /api/chat/sessions/{session}/runs-going`), names them, and offers a tick that
// stops them with the chat (`DELETE …?runs=stop`). Left unticked, or with a count the gateway did
// not answer, they keep running, and the dialog says so.

const h = vi.hoisted(() => ({
  chatSessions: vi.fn(),
  deleteChatSession: vi.fn(),
  chatRunsGoing: vi.fn(),
  confirm: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const api = new Proxy({
    chatSessions: h.chatSessions, deleteChatSession: h.deleteChatSession, chatRunsGoing: h.chatRunsGoing,
    chatFolders: () => Promise.resolve([]), chatTags: () => Promise.resolve([]),
    rooms: () => Promise.resolve({ enabled: false }),
  } as Record<string, unknown>, {
    get(t, p: string) { return p in t ? t[p] : (t[p] = () => Promise.resolve({})) },
  })
  return { ...real, api }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))
vi.mock('../../../ui/dialog', () => ({ confirm: h.confirm, promptInput: vi.fn() }))
vi.mock('../../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))

let useChatHistory: typeof import('./useChatHistory')['useChatHistory']

const CHAT = { key: 'chat-1-feed', title: 'Why the feed refetches', origin: 'manual', last_activity_ts: '2026-10-01T09:00:00Z' } as ChatSessionSummary

function mount() {
  return renderHook(() => {
    const [query, setQ] = useState<Record<string, string>>({})
    const setQuery: RouteProps['setQuery'] = (patch) => setQ((prev) => {
      const next = { ...prev }
      for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
      return next
    })
    return useChatHistory({ navigate: vi.fn(), query, setQuery })
  })
}

/** The dialog as the owner meets it: it opens, its body shown, the tick ticked when *tick*, then
 *  Delete. Shown outside the delete's own `act`, which would hold its render back. */
async function deleteIt({ tick }: { tick: boolean }) {
  let shown: ReactNode = null
  let decide: ((yes: boolean) => void) | null = null
  h.confirm.mockImplementation((o: { body?: ReactNode }) => new Promise<boolean>((resolve) => { shown = o.body ?? null; decide = resolve }))
  const { result } = mount()
  await waitFor(() => expect(result.current.filtered.map((s) => s.key)).toContain(CHAT.key))
  let deleting: Promise<void> = Promise.resolve()
  act(() => { deleting = result.current.del(CHAT) })
  await waitFor(() => expect(decide).not.toBeNull())
  render(<div role="dialog" aria-label="Delete chat?">{shown}</div>)
  if (tick) fireEvent.click(screen.getByRole('checkbox', { name: /^Also stop / }))
  await act(async () => { decide!(true); await deleting })
}

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.chatSessions.mockResolvedValue([CHAT])
  h.deleteChatSession.mockResolvedValue({ ok: true })
  ;({ useChatHistory } = await import('./useChatHistory'))
})
afterEach(() => cleanup())

describe("a chat's Delete and the runs it started", () => {
  it('🔴 names how many are still going, and stops them with it when ticked', async () => {
    h.chatRunsGoing.mockResolvedValue({ runs: 2 })

    await deleteIt({ tick: true })

    expect(h.chatRunsGoing).toHaveBeenCalledWith(CHAT.key)
    expect(screen.getByRole('checkbox', { name: 'Also stop the 2 workflow runs it started, still going' })).toBeChecked()
    expect(h.deleteChatSession).toHaveBeenCalledWith(CHAT.key, { stopRuns: true })
  })

  it('🔴 left unticked, deletes the chat and says they keep running', async () => {
    h.chatRunsGoing.mockResolvedValue({ runs: 1 })

    await deleteIt({ tick: false })

    expect(screen.getByText(/Left unticked, it keeps running\.$/)).toBeTruthy()
    expect(h.deleteChatSession).toHaveBeenCalledWith(CHAT.key, { stopRuns: false })
  })

  it('🔴 says so when the gateway did not count them, and they keep running', async () => {
    h.chatRunsGoing.mockRejectedValue(new Error('the gateway is restarting'))

    await deleteIt({ tick: false })

    expect(screen.getByText(/couldn’t tell whether it started workflow runs that are still going/)).toBeTruthy()
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(h.deleteChatSession).toHaveBeenCalledWith(CHAT.key, { stopRuns: false })
  })

  it('asks nothing of runs when it has none going', async () => {
    h.chatRunsGoing.mockResolvedValue({ runs: 0 })

    await deleteIt({ tick: false })

    expect(screen.getByRole('dialog').textContent).toMatch(/will be permanently removed/)
    expect(screen.getByRole('dialog').textContent).not.toMatch(/workflow run/)
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(h.deleteChatSession).toHaveBeenCalledWith(CHAT.key, { stopRuns: false })
  })

  it('a cancelled dialog deletes nothing', async () => {
    h.chatRunsGoing.mockResolvedValue({ runs: 2 })
    h.confirm.mockResolvedValue(false)
    const { result } = mount()
    await waitFor(() => expect(result.current.filtered.map((s) => s.key)).toContain(CHAT.key))

    await act(async () => { await result.current.del(CHAT) })

    expect(h.deleteChatSession).not.toHaveBeenCalled()
  })
})
