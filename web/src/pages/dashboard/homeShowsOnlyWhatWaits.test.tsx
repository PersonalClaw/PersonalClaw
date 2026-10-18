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
// The rules this file holds:
//
//   1. A pill is shown only for what waits on the owner: approvals waiting, tasks ready, and inbox.
//      Loops running is not waiting on anyone (it is in the Active work band, and on #/loops), and
//      unread notifications are the shell's bell. Neither is lost; neither is badged here.
//   2. A pill reading 0 is not drawn. A pill whose count could not be read, or has not been read
//      yet, still shows the dash: hiding it would claim "nothing waiting" from no data, which is the
//      mistake `heroCountUnknown.test.tsx` and `zeroBeforeRead.test.tsx` exist to prevent.
//   3. Home draws no shortcut row. Each destination keeps its one path, the sidebar; "Jump back in"
//      stays, because those chips are Home's own content (your recent chats), not navigation.
//   4. Home draws no telemetry. It lives in Settings › System (`settings/systemPanel.test.tsx`).

vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))
vi.mock('../../ui/DotGlow', () => ({ DotGlow: () => null }))

const boom = () => Promise.reject(new Error('gateway down'))

/** The reads every case starts from: everything read, everything empty. */
function baseline(): Record<string, () => Promise<unknown>> {
  return {
    approvals: () => Promise.resolve([]),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    uLoops: () => Promise.resolve([]),
    readyTasks: () => Promise.resolve([]),
    notifications: () => Promise.resolve({ notifications: [] }),
    triggersHistory: () => Promise.resolve({ runs: [], did_ids: [] }),
    status: () => Promise.resolve({ uptime: '5m', version: '0.3.0', platform: 'darwin', triggers: 2, subagents: 0 }),
    system: () => Promise.resolve({ cpu_pct: 12, mem_used_gb: 4, mem_total_gb: 16, cpu_count: 8, load_1m: 1.2 }),
    discover: () => Promise.resolve({ enabled: false, visible_count: 0, areas: [], tips: [] }),
    doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
    agents: () => Promise.resolve({ agents: [] }),
    onboarding: () => Promise.resolve({ needs_model: false, has_model_provider: true, has_chat_binding: true }),
    modelsLoaded: () => Promise.resolve({
      loaded: [], providers: [],
      pressure: { total_mb: 16384, used_mb: 4096, available_mb: 12288, used_pct: 25, warn_pct: 90, warn: false, source: 'meminfo' },
    }),
    chatSessions: () => Promise.resolve([
      { key: 'chat-1-x', title: 'Weekly review', updated: '2026-10-17T09:00:00+00:00', run_state: runState({ session: 'chat-1-x' }) },
    ]),
    suggestions: () => Promise.resolve({ suggestions: ['Summarise this week'], refreshing: false, needs_model: '' }),
  }
}

let reads = baseline()
/** How many times each api read was made, so a test can say a band reads nothing it does not show. */
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

async function mountHero() {
  const { DashboardLiveProvider } = await import('./DashboardLive')
  const { HeroPulse } = await import('./widgets/HeroPulse')
  render(<DashboardLiveProvider><HeroPulse {...route} /></DashboardLiveProvider>)
}

async function mountHome() {
  const { DashboardPage } = await import('./DashboardPage')
  const { AppearanceProvider } = await import('../../app/appearance')
  render(<AppearanceProvider><DashboardPage {...route} /></AppearanceProvider>)
  // Home has mounted and its sessions have arrived: the one row this file keeps.
  await screen.findByText('Jump back in')
}

/** The hero's pills are the buttons whose name is a count reading. */
const pills = () => screen.queryAllByRole('button', {
  name: /approvals? waiting|tasks? ready|inbox|loops? running|unread$/,
})

describe('the hero badges only what waits on the owner, and never a zero', () => {
  it('a quiet day draws no pill at all — every count read, every count 0', async () => {
    await mountHero()
    // Wait until the reads have landed: before that every pill is a dash, which is the other rule.
    await waitFor(() => expect(screen.queryByText('—'), 'every lane has been read').toBeNull())
    expect(pills().map((p) => p.getAttribute('aria-label')), 'no pill reads 0').toEqual([])
  })

  it('two approvals waiting draw exactly that pill', async () => {
    reads.approvals = () => Promise.resolve([
      { id: 'a1', tool: 'shell', args: {}, session: 'chat-1-x' },
      { id: 'a2', tool: 'shell', args: {}, session: 'chat-1-x' },
    ])
    await mountHero()
    await waitFor(() => expect(screen.getByLabelText('2 approvals waiting')).toBeInTheDocument())
    expect(pills().map((p) => p.getAttribute('aria-label'))).toEqual(['2 approvals waiting'])
  })

  it('a task ready and an inbox message are badged; running loops and unread notifications are not', async () => {
    reads.readyTasks = () => Promise.resolve([{ id: 't1', title: 'Write the note', status: 'ready' }])
    reads.inboxOpen = () => Promise.resolve([{ id: 'i1', item_kind: 'user_note', title: 'Call back' }])
    reads.uLoops = () => Promise.resolve([{ id: 'l1', status: 'running', name: 'Packing note' }])
    reads.notifications = () => Promise.resolve({ notifications: [{ id: 'n1', acked: false, title: 'Done' }] })
    await mountHero()
    await waitFor(() => expect(screen.getByLabelText('1 task ready')).toBeInTheDocument())
    expect(pills().map((p) => p.getAttribute('aria-label'))).toEqual(['1 task ready', '1 inbox'])
    expect(screen.queryByLabelText(/loops? running/), 'Active work shows what runs').toBeNull()
    expect(screen.queryByLabelText(/unread/), 'the bell shows what is unread').toBeNull()
  })

  it('a count that could not be read keeps its dash: hiding it would claim nothing waits', async () => {
    reads.readyTasks = boom
    await mountHero()
    const pill = await waitFor(() => screen.getByLabelText(/tasks ready — couldn’t be read/))
    expect(pill.textContent).toContain('—')
  })

  it('the first frame shows dashes for the three, not nothing', async () => {
    const never = () => new Promise<never>(() => {})
    reads.approvals = never
    reads.readyTasks = never
    reads.inboxOpen = never
    await mountHero()
    // Synchronous: this is the frame before any read lands.
    expect(screen.getAllByText('—')).toHaveLength(3)
    expect(screen.getByLabelText('Loading approvals waiting…')).toBeInTheDocument()
  })
})

describe('a quiet day leaves no trace of the strip below lg', () => {
  it('the body strip\'s region collapses when the strip renders nothing', async () => {
    await mountHome()
    await waitFor(() => expect(screen.queryByText('—'), 'every lane has been read').toBeNull())
    // The body copy of the strip is the column's one `lg:hidden` region. On a quiet day it holds
    // nothing, and a flex item holding nothing still spends a `gap-2xl` of blank space unless it
    // collapses: jsdom applies no stylesheet, so the class that collapses it is what is held here.
    const regions = [...document.querySelectorAll('[data-entrance-region]')].filter((el) => el.classList.contains('lg:hidden'))
    expect(regions, 'the body strip region').toHaveLength(1)
    expect(regions[0].childElementCount, 'nothing waits, so the strip draws nothing').toBe(0)
    expect(regions[0].classList.contains('empty:hidden'), 'an empty region collapses').toBe(true)
  })
})

describe('Home reads nothing it does not show', () => {
  it('no system metrics or health probe reads: those are Settings › System\'s', async () => {
    await mountHome()
    await waitFor(() => expect(calls.uLoops ?? 0).toBeGreaterThan(0))
    expect(calls.system ?? 0, 'the host metrics poll').toBe(0)
    expect(calls.doctor ?? 0, 'the health probe').toBe(0)
  })
})

describe('a failed loops read is said out loud', () => {
  it('Active work shows the failure with Retry, not "No active work"', async () => {
    let fail = true
    reads.uLoops = () => (fail ? boom() : Promise.resolve([{ id: 'l1', status: 'running', name: 'Packing note', total_cycles: 0, max_cycles: 0, created_at: 1 }]))
    await mountHome()
    const err = await screen.findByText(/Couldn.t load active work/)
    expect(screen.queryByText(/No active work/), 'an unread lane is not an empty one').toBeNull()
    fail = false
    const retry = within(err.closest('[role="alert"]') ?? err.parentElement!).getByRole('button', { name: /retry/i })
    retry.click()
    expect(await screen.findByText('Packing note')).toBeInTheDocument()
    expect(screen.queryByText(/Couldn.t load active work/)).toBeNull()
  })

  it('a read that worked and found nothing still says so (the control)', async () => {
    await mountHome()
    expect(await screen.findByText(/No active work/)).toBeInTheDocument()
  })
})

describe('YOLO stays in sight on Home', () => {
  it('with every tool call auto-approved, Home says so', async () => {
    const base = reads.status
    reads.status = () => base().then((s) => ({ ...(s as object), yolo: true }))
    await mountHome()
    // Home draws the strip twice, in the header from lg up and in the body below it; jsdom applies
    // no breakpoint, so both copies are in the DOM here and each must carry the warning.
    expect(await screen.findAllByText('YOLO active')).toHaveLength(2)
  })

  it('and says nothing when approvals are asked for (the control)', async () => {
    await mountHome()
    await waitFor(() => expect(calls.status ?? 0).toBeGreaterThan(0))
    await waitFor(() => expect(screen.queryByText('—')).toBeNull())
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

  it('"Jump back in" stays — your recent chats are Home\'s own content (the control)', async () => {
    await mountHome()
    expect(screen.getByRole('button', { name: /Weekly review/ })).toBeTruthy()
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

  it('the bands that stay are still there (the control)', async () => {
    await mountHome()
    for (const name of ['To triage', 'Active work', 'Tasks', 'Suggestions', 'Recent activity']) {
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
