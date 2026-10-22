import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { CompanionPage } from './CompanionPage'
import { invalidateKeys, resetDataStore } from '../../lib/data'
import type { PendingApproval, RunningList, RunningRow, WaitingList } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { HomeHarness } from '../../test/homeHarness'
import { waitingApproval, waitingItem, waitingList } from '../../test/waiting'

// ── View two: one piece of work owning the screen (#3898) ────────────────────────────────────
//
// A tap on a row opens the buddy's expanded card for that one piece of work: its orb large, its
// exact phase, its alert with the app's own prompt and answer controls, and Open in PersonalClaw
// for its own route. An approval answered there goes to its own route once, and leaves every other
// surface on the next change frame. Back returns to everything. Under reduced motion the switch is
// instant: the shared orb is dropped.

const frames: Array<(m: WsMessage) => void> = []
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { frames.push(onMessage) },
}))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('../dashboard/PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))
vi.mock('../dashboard/widgets/Suggestions', () => ({ Suggestions: () => null }))

let reduced = false
vi.mock('../../design/motion', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  prefersReducedMotion: () => reduced,
}))

const waiting = vi.fn<() => Promise<WaitingList>>()
const running = vi.fn<() => Promise<RunningList>>()
const resolveApproval = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      waiting: () => waiting(),
      running: () => running(),
      resolveApproval: (id: string, action: string) => resolveApproval(id, action),
      pushStatus: () => Promise.reject(new Error('no push in this test')),
      uLoops: () => Promise.resolve([]),
      tasks: () => Promise.resolve({ tasks: [], total: 0 }),
      inboxOpen: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      notifications: () => Promise.resolve({ notifications: [], unread: 0 }),
      chatSessions: () => Promise.resolve([]),
      status: () => Promise.resolve({}),
    },
  }
})

const AP: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'chat', tool: 'Bash', tool_input: 'ls', tool_purpose: 'List the folder',
  session: 'c1', ts: Math.round(Date.now() / 1000) - 30, session_title: 'Release notes', agent: '', risk: 'caution',
  grant_agent: '', source_label: 'chat “Release notes”', confirms: [], work: 'chat:c1',
}
const LOOP = waitingItem({
  kind: 'loop_question', id: 'loop_question:l1', work: 'loop:l1', consequence: 'asks',
  brief: { name: 'Sort the inbox', question: 'May I archive last year’s mail?' }, route: '#/loops/l1',
})
function row(over: Partial<RunningRow> = {}): RunningRow {
  return {
    work: 'chat:c9', kind: 'chat', session: 'c9', phase: 'tool', line: 'Running the tests', since: null,
    route: '#/chat/c9', blocked: false, waiting: [], name: 'Fix the flaky test', agent: '', started_at: null, ...over,
  } as RunningRow
}

/** The page with its address held as the app holds it. */
function Phone({ initial = {} }: { initial?: Record<string, string> }) {
  const [query, setQ] = useState<Record<string, string>>(initial)
  const setQuery = (patch: Record<string, string | null | undefined>) => setQ((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(patch)) { if (v == null) delete next[k]; else next[k] = v }
    return next
  })
  return <CompanionPage sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={setQuery} />
}

beforeEach(() => {
  reduced = false
  frames.length = 0
  resetDataStore()
  waiting.mockReset().mockResolvedValue(waitingList(waitingApproval(AP, { route: '#/chat/c1' }), LOOP))
  running.mockReset().mockResolvedValue({ running: [row()], finished: [] })
  resolveApproval.mockReset().mockResolvedValue({ ok: true })
  invalidateKeys('companion:', true)
  for (const k of ['running-companion', 'loops-companion', 'tasks-companion', 'inbox-companion', 'notifications-companion']) invalidateKeys(k)
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const viewTwo = () => document.querySelector('[data-companion-view="one"]') as HTMLElement | null

describe('a tap opens one piece of work', () => {
  it('an approval opens its card large, with its prompt, its phase and Open in PersonalClaw, and Back returns', async () => {
    render(<Phone />)
    await userEvent.click(await screen.findByRole('button', { name: 'Show Release notes needs your OK: Bash' }))
    const two = viewTwo()!
    expect(two).toBeTruthy()
    expect(within(two).getByRole('group', { name: 'Permission needed to run Bash' })).toBeTruthy()
    expect(within(two).getByRole('link', { name: 'Open in PersonalClaw' })).toHaveAttribute('href', '#/chat/c1')
    expect(two.querySelector('[data-buddy-orb][data-buddy-state="approval"] canvas')?.getAttribute('style')).toMatch(/width: 1\d\dpx/)
    // Only this piece of work: the rest of the page is gone while it owns the screen.
    expect(screen.queryByRole('heading', { name: /Waiting on you/ })).toBeNull()
    // Nor does the card claim nothing else runs: the rest is not in sight here, so it says nothing.
    expect(within(two).queryByRole('region', { name: /^In sight/ })).toBeNull()
    await userEvent.click(within(two).getByRole('button', { name: 'Back to everything' }))
    expect(viewTwo()).toBeNull()
    expect(await screen.findByRole('heading', { name: /Waiting on you/ })).toBeTruthy()
  })

  it('running work opens with its own phase and route', async () => {
    render(<Phone />)
    await userEvent.click(await screen.findByRole('button', { name: 'Show Fix the flaky test: Running the tests' }))
    const two = viewTwo()!
    expect(within(two).getByRole('link', { name: 'Open in PersonalClaw' })).toHaveAttribute('href', '#/chat/c9')
    expect(two.querySelector('[data-buddy-orb]')?.getAttribute('data-buddy-state')).toBe('tool')
  })

  it('an ask the card does not answer opens its own page from Open in PersonalClaw, and sends nothing', async () => {
    render(<Phone />)
    await userEvent.click(await screen.findByRole('button', { name: /^Show Loop/ }))
    const two = viewTwo()!
    expect(within(two).getByRole('link', { name: 'Open in PersonalClaw' })).toHaveAttribute('href', '#/loops/l1')
    expect(within(two).queryByRole('button', { name: /^Allow|^Deny/ })).toBeNull()
    expect(resolveApproval).not.toHaveBeenCalled()
  })

  it('a row that is gone says so instead of opening on nothing', async () => {
    render(<Phone initial={{ open: 'approval:gone' }} />)
    expect(await screen.findByText(/isn’t waiting or running anymore/)).toBeTruthy()
    expect(viewTwo()).toBeNull()
  })
})

describe('an approval answered on the phone', () => {
  it('goes to its own route once, and view two returns to everything', async () => {
    render(<Phone initial={{ open: 'approval:ap-1' }} />)
    const two = await vi.waitFor(() => { const v = viewTwo(); if (!v) throw new Error('not open'); return v })
    await userEvent.click(within(two).getByRole('button', { name: 'Allow Bash' }))
    await vi.waitFor(() => expect(resolveApproval).toHaveBeenCalledWith('ap-1', 'approve'))
    expect(resolveApproval).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(viewTwo()).toBeNull())
  })

  it('leaves Home on the next change frame too', async () => {
    render(<><Phone initial={{ open: 'approval:ap-1' }} /><HomeHarness /></>)
    const two = await vi.waitFor(() => { const v = viewTwo(); if (!v) throw new Error('not open'); return v })
    const home = () => screen.getByRole('region', { name: 'Needs You' })
    await vi.waitFor(() => expect(within(home()).getByRole('group', { name: 'Permission needed to run Bash' })).toBeTruthy())
    await userEvent.click(within(two).getByRole('button', { name: 'Allow Bash' }))
    await vi.waitFor(() => expect(resolveApproval).toHaveBeenCalledTimes(1))
    // The gateway ends it, and rings once.
    waiting.mockResolvedValue(waitingList(LOOP))
    act(() => { for (const f of [...frames]) f({ type: 'waiting_changed' } as WsMessage) })
    await vi.waitFor(() => {
      expect(within(home()).queryByRole('group', { name: 'Permission needed to run Bash' })).toBeNull()
      expect(screen.queryAllByRole('group', { name: 'Permission needed to run Bash' })).toHaveLength(0)
    }, { timeout: 2000 })
  })
})

describe('switching views', () => {
  it('moves the one orb from the row into view two', async () => {
    render(<Phone />)
    const head = await screen.findByRole('button', { name: 'Show Release notes needs your OK: Bash' })
    expect(head.closest('[data-work-head]')!.querySelector('[data-morph]')?.getAttribute('data-morph')).toBe('shared')
    await userEvent.click(head)
    expect(viewTwo()!.querySelector('[data-morph]')?.getAttribute('data-morph')).toBe('shared')
  })

  it('is instant under reduced motion: the shared orb is dropped at both ends', async () => {
    reduced = true
    render(<Phone />)
    const head = await screen.findByRole('button', { name: 'Show Release notes needs your OK: Bash' })
    expect(head.closest('[data-work-head]')!.querySelector('[data-morph]')?.getAttribute('data-morph')).toBe('none')
    await userEvent.click(head)
    expect(viewTwo()!.querySelector('[data-morph]')?.getAttribute('data-morph')).toBe('none')
  })
})
