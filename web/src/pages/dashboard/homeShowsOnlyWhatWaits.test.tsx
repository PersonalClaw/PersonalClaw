import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, within } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── Needs you shows only what waits on its owner ─────────────────────────────────────────────────
//
// Home's first screen used to spend itself on three things that were not the owner's work:
//
//   • five count pills, each drawn at `0` — "0 loops running · 0 approvals waiting · 0 tasks ready
//     · 0 inbox · 0 unread" on a quiet day, five confident statements that nothing is happening;
//   • a row of shortcut chips (Chat, Tasks, Knowledge, Projects, Files, Terminal) that repeated the
//     sidebar beside it, every icon in coral;
//   • the machine's telemetry — CPU, memory, network, disk, load, uptime — docked along the bottom
//     edge, and a second band of it ("On this machine") lower down.
//
// What waits on the owner is now its own page, Needs you, and the rules this file holds are its:
//
//   1. It draws no count pills. What waits is counted once, by the toolbar's needs-you badge
//      (`ui/needsYouBadge.test.tsx`), which shows nothing at 0 and a dash while the count is
//      unknown; on the page the count is in each group's own heading.
//   2. It draws no shortcut row and no chats: each destination keeps its one path, the sidebar, and
//      your chats are the sidebar's.
//   3. It draws no telemetry, and reads none. That lives in Settings › System
//      (`settings/systemPanel.test.tsx`).
//
// (YOLO stays in sight in the shell's corner, on every page: `ui/theCornerSaysWhenYoloIsOn.test.tsx`.)

vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))

/** One approval waiting, as `GET /api/waiting` serves it. */
const approval = (id: string) => ({
  id: `approval:${id}`, kind: 'approval', work: '', session: '', brief: { tool: 'shell', confirms: [] }, consequence: 'changes', risk: '',
  asked_at: null, age_secs: null, expires_at: null, current: true,
  answer: { method: 'POST', path: `/api/approvals/${id}/{answer}`, takes: [], body: {} }, route: '',
})

/** The reads every case starts from: everything read, everything empty, but for a chat in the list
 *  that the page must not draw. */
function baseline(): Record<string, () => Promise<unknown>> {
  return {
    waiting: () => Promise.resolve({ waiting: [], unreadable: [] }),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    running: () => Promise.resolve({ running: [], finished: [] }),
    status: () => Promise.resolve({ uptime: '5m', version: '0.3.0', platform: 'darwin', triggers: 2, subagents: 0 }),
    system: () => Promise.resolve({ cpu_pct: 12, mem_used_gb: 4, mem_total_gb: 16, cpu_count: 8, load_1m: 1.2 }),
    doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
    dashboardViews: () => Promise.resolve([]),
    chatSessions: () => Promise.resolve([
      { key: 'chat-1-x', title: 'Weekly review', updated: '2026-10-17T09:00:00+00:00', run_state: runState({ session: 'chat-1-x' }) },
    ]),
    suggestions: () => Promise.resolve({ suggestions: ['Summarise this week'], refreshing: false, needs_model: '' }),
  }
}

let reads = baseline()
/** How many times each api read was made, so a test can say the page reads nothing it does not show. */
const calls: Record<string, number> = {}

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const api = new Proxy({}, {
    get: (_t, prop: string) => (...args: unknown[]) => {
      calls[prop] = (calls[prop] ?? 0) + 1
      return prop in reads ? reads[prop](...(args as [])) : Promise.resolve([])
    },
  })
  return { ...real, api }
})

beforeEach(() => {
  reads = baseline()
  for (const k of Object.keys(calls)) delete calls[k]
  sessionStorage.clear(); localStorage.clear()
})
afterEach(cleanup)

/** Needs you, mounted and settled: its list read and drawn (the all-clear, on the baseline). */
async function mountNeedsYou(settled: () => Promise<unknown> = () => screen.findByText('Nothing is waiting on you.')) {
  const { NeedsYouHarness } = await import('../../test/needsYouHarness')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(<AppearanceProvider><NeedsYouHarness /></AppearanceProvider>)
  await settled()
}

describe('Needs you draws no count pills', () => {
  it('two approvals waiting are two cards and their group heading’s count, and no pill', async () => {
    reads.waiting = () => Promise.resolve({ waiting: [approval('a1'), approval('a2')], unreadable: [] })
    await mountNeedsYou(() => screen.findByRole('heading', { name: /^Waiting on your answer/ }))
    const group = screen.getByRole('region', { name: /^Waiting on your answer/ })
    expect(within(group).getByText('2', { selector: '[data-group-count]' })).toBeInTheDocument()
    expect(within(group).getAllByRole('button', { name: /^Show the details of / })).toHaveLength(2)
    expect(screen.queryByLabelText(/approvals? waiting/), 'a count pill').toBeNull()
    expect(screen.queryByLabelText(/tasks? ready|inbox$/), 'a count pill').toBeNull()
  })
})

describe('Needs you reads nothing it does not show', () => {
  it('no system metrics or health probe reads: those are Settings › Health › System’s', async () => {
    await mountNeedsYou()
    expect(calls.waiting ?? 0).toBeGreaterThan(0)
    expect(calls.system ?? 0, 'the host metrics poll').toBe(0)
    expect(calls.doctor ?? 0, 'the health probe').toBe(0)
  })

  it('and no read for what runs, your chats, suggestions, or the old bands’ own counts', async () => {
    await mountNeedsYou()
    for (const read of ['running', 'chatSessions', 'suggestions', 'uLoops', 'readyTasks', 'approvals', 'notifications', 'triggersHistory']) {
      expect(calls[read] ?? 0, `Needs you read ${read}`).toBe(0)
    }
  })
})

describe('Needs you draws no second copy of the sidebar', () => {
  it('no shortcut chip to a page the sidebar already reaches', async () => {
    await mountNeedsYou()
    for (const name of ['Tasks', 'Knowledge', 'Projects', 'Files', 'Terminal']) {
      expect(screen.queryByRole('button', { name }), `a "${name}" chip repeats the sidebar`).toBeNull()
    }
  })

  it('and no chats: they are the sidebar’s', async () => {
    await mountNeedsYou()
    expect(screen.queryByRole('list', { name: 'Your chats' })).toBeNull()
    expect(screen.queryByText('Weekly review')).toBeNull()
  })
})

describe('Needs you draws no machine telemetry', () => {
  it('no system rail, in either of its old places', async () => {
    await mountNeedsYou()
    expect(document.querySelectorAll('[data-testid="system-rail-island"]')).toHaveLength(0)
    expect(screen.queryByText('uptime'), 'the rail\'s first metric').toBeNull()
    expect(screen.queryByText('cpu')).toBeNull()
  })

  it('no "On this machine" band', async () => {
    await mountNeedsYou()
    expect(screen.queryByRole('heading', { name: 'On this machine' })).toBeNull()
  })

  it('the groups that are its own are there (the control)', async () => {
    reads.waiting = () => Promise.resolve({ waiting: [approval('a1')], unreadable: [] })
    reads.skillProposals = () => Promise.resolve({ proposals: [{ id: 'p1', slug: 'tidy-notes', description: 'Tidy the notes folder', created_at: '2026-10-17T09:00:00+00:00' }], lastReview: null })
    await mountNeedsYou(() => screen.findByRole('heading', { name: /^To review/ }))
    for (const name of [/^Waiting on your answer/, /^To review/]) {
      expect(screen.getByRole('heading', { name }), `${name} is Needs you's own`).toBeTruthy()
    }
  })
})
