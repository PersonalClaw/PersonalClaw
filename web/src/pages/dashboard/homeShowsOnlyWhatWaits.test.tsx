import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import { runState } from '../../test/runState'

// ── Home shows only what waits on its owner ──────────────────────────────────────────────────────
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
// The rules this file holds, now that Home is the attention stack (#3813):
//
//   1. Home draws no count pills. What waits on the owner is counted once, by the toolbar's
//      needs-you badge (`ui/needsYouBadge.test.tsx`), which shows nothing at 0 and a dash while
//      the count is unknown; on Home the count is in Needs You's own heading.
//   2. Home draws no shortcut row. Each destination keeps its one path, the sidebar; your recent
//      threads stay, because they are Home's own content, not navigation.
//   3. Home draws no telemetry, and reads none. It lives in Settings › System
//      (`settings/systemPanel.test.tsx`).
//   4. YOLO stays in sight: with every tool call approved without asking, Home says so.

vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))


/** The reads every case starts from: everything read, everything empty. */
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
/** How many times each api read was made, so a test can say Home reads nothing it does not show. */
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

const route = { sub: '', navEpoch: 0, navigate: () => {}, query: {}, setQuery: () => {} }

beforeEach(() => {
  reads = baseline()
  for (const k of Object.keys(calls)) delete calls[k]
  sessionStorage.clear(); localStorage.clear()
})
afterEach(cleanup)

async function mountHome() {
  const { HomeHarness } = await import('../../test/homeHarness')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(<AppearanceProvider><HomeHarness /></AppearanceProvider>)
  // Home has mounted and its threads have arrived.
  await screen.findByRole('list', { name: 'Your threads' })
}

describe('Home draws no count pills', () => {
  it('two approvals waiting are Needs You’s two cards and its heading’s count, and no pill', async () => {
    reads.waiting = () => Promise.resolve({ waiting: [
      { id: 'approval:a1', kind: 'approval', work: '', session: '', brief: { tool: 'shell', confirms: [] }, consequence: 'changes', risk: '', asked_at: null, age_secs: null, expires_at: null, current: true,
        answer: { method: 'POST', path: '/api/approvals/a1/{answer}', takes: [], body: {} }, route: '' },
      { id: 'approval:a2', kind: 'approval', work: '', session: '', brief: { tool: 'shell', confirms: [] }, consequence: 'changes', risk: '', asked_at: null, age_secs: null, expires_at: null, current: true,
        answer: { method: 'POST', path: '/api/approvals/a2/{answer}', takes: [], body: {} }, route: '' },
    ], unreadable: [] })
    await mountHome()
    const group = await screen.findByRole('region', { name: 'Needs You' })
    await waitFor(() => expect(within(group).getByText('2', { selector: '[data-group-count]' })).toBeInTheDocument())
    expect(screen.queryByLabelText(/approvals? waiting/), 'a count pill').toBeNull()
    expect(screen.queryByLabelText(/tasks? ready|inbox$/), 'a count pill').toBeNull()
  })
})

describe('Home reads nothing it does not show', () => {
  it('no system metrics or health probe reads: those are Settings › System\'s', async () => {
    await mountHome()
    await waitFor(() => expect(calls.waiting ?? 0).toBeGreaterThan(0))
    expect(calls.system ?? 0, 'the host metrics poll').toBe(0)
    expect(calls.doctor ?? 0, 'the health probe').toBe(0)
  })

  it('and no read the old Home’s bands made for their own counts', async () => {
    await mountHome()
    await waitFor(() => expect(calls.running ?? 0).toBeGreaterThan(0))
    for (const read of ['uLoops', 'readyTasks', 'approvals', 'notifications', 'triggersHistory']) {
      expect(calls[read] ?? 0, `Home read ${read}`).toBe(0)
    }
  })
})

describe('YOLO stays in sight on Home', () => {
  it('with every tool call auto-approved, Home says so', async () => {
    const base = reads.status
    reads.status = () => base().then((s) => ({ ...(s as object), yolo: true }))
    await mountHome()
    expect(await screen.findByText('YOLO active')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Change it in Agent defaults' })).toHaveAttribute('href', '#/settings/agent')
  })

  it('and says nothing when approvals are asked for (the control)', async () => {
    await mountHome()
    await waitFor(() => expect(calls.status ?? 0).toBeGreaterThan(0))
    expect(screen.queryByText('YOLO active')).toBeNull()
  })
})

describe('Home draws no second copy of the sidebar', () => {
  it('no shortcut chip to a page the sidebar already reaches', async () => {
    await mountHome()
    for (const name of ['Tasks', 'Knowledge', 'Projects', 'Files', 'Terminal']) {
      expect(screen.queryByRole('button', { name }), `a "${name}" chip repeats the sidebar`).toBeNull()
    }
  })

  it('your recent threads stay — they are Home\'s own content (the control)', async () => {
    await mountHome()
    expect(screen.getByRole('button', { name: 'Show the details of Weekly review' })).toBeTruthy()
  })
})

describe('Home draws no machine telemetry', () => {
  it('no system rail, in either of its old places', async () => {
    await mountHome()
    expect(document.querySelectorAll('[data-testid="system-rail-island"]')).toHaveLength(0)
    expect(screen.queryByText('uptime'), 'the rail\'s first metric').toBeNull()
    expect(screen.queryByText('cpu')).toBeNull()
  })

  it('no "On this machine" band', async () => {
    await mountHome()
    expect(screen.queryByRole('heading', { name: 'On this machine' })).toBeNull()
  })

  it('the groups that stay are still there (the control)', async () => {
    await mountHome()
    for (const name of ['Needs You', 'Running Now', 'Recent and completed']) {
      expect(screen.getByRole('heading', { name }), `${name} is Home's own and stays`).toBeTruthy()
    }
  })
})

describe('coral marks the agent, not a list bullet', () => {
  it('a suggestion row\'s icon is in the neutral tone', async () => {
    const { Suggestions } = await import('./widgets/Suggestions')
    render(<Suggestions {...route} />)
    const row = await screen.findByRole('button', { name: /Summarise this week/ })
    const icon = row.querySelector('svg')!
    expect(icon.getAttribute('class') ?? '', 'coral is the agent\'s voice').not.toMatch(/\btext-primary\b/)
    expect(icon.getAttribute('class') ?? '').toMatch(/\btext-on-surface-low\b/)
  })
})
