import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { TRIGGER_PRESETS } from './triggerPresets'

// ── The presets are reachable on a home that already has triggers ─────────────────────
//
// The Triggers list offers its presets only when it is genuinely empty, and the triggers
// PersonalClaw registers for itself (the heartbeat pass, the digests, the reports) mean a real
// home's list never is. Measured on a fresh install: six system rows, no gallery, and "New
// trigger" opened a blank form — the Morning briefing preset could not be reached at all, so it
// was built by hand. The blank form now offers the same presets at its top.
//
// Driven through the real `TriggersSection` with a router stand-in that does what the app's does
// (navigate → the create route, `setQuery` → a new query), from a list holding only a system row,
// so every hop a user takes is the page's own.

const HEARTBEAT_ROW = {
  kind: 'schedule',
  id: 'schedule:system:heartbeat-tasks',
  raw_id: 'system:heartbeat-tasks',
  name: 'Heartbeat tasks',
  enabled: true,
  schedule: 'every 60s',
  action: { provider: 'heartbeat-tasks', config: {} },
  last_run_ts: null,
  last_run_status: '',
  last_status: 'ok',
  run_count: 0,
  next_run_ts: null,
  is_running: false,
  created_by: 'system',
  author: '',
  read_only: false,
  broken: [],
  warnings: [],
}

const { PROVIDERS } = vi.hoisted(() => ({
  PROVIDERS: [
    {
      name: 'invoke-agent', display_name: 'Invoke Agent', supports_blocking: false,
      settingsSchema: {
        type: 'object', required: ['task_template'],
        properties: { task_template: { type: 'string', 'x-meta': { label: 'Task' } } },
      },
    },
    {
      name: 'notify', display_name: 'Notification', supports_blocking: false,
      settingsSchema: {
        type: 'object', required: ['title_template'],
        properties: { title_template: { type: 'string', 'x-meta': { label: 'Title' } } },
      },
    },
  ],
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    schedules: () => Promise.resolve({ jobs: [HEARTBEAT_ROW] }),
    hooks: () => Promise.resolve([]),
    storeTriggers: () => Promise.resolve([]),
    callbacks: () => Promise.resolve([]),
    triggerReview: () => Promise.resolve([]),
    actionProviders: () => Promise.resolve(PROVIDERS),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: ['$NOW'], event: [] }),
    savedAgents: () => Promise.resolve([]),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve({}),
    prompts: () => Promise.resolve([]),
    createSchedule: vi.fn(),
    createHook: vi.fn(),
    createEvent: vi.fn(),
  },
}))

const { TriggersSection } = await import('./TriggersSection')

/** The app's router, reduced to what this section reads: a sub-path and a query. */
function App() {
  const [route, setRoute] = useState<{ sub: string; query: Record<string, string> }>({ sub: '', query: {} })
  const navigate = (to: string) => {
    const [path, qs = ''] = to.split('?')
    setRoute({ sub: path.replace(/^triggers\/?/, ''), query: Object.fromEntries(new URLSearchParams(qs)) })
  }
  const setQuery = (patch: Record<string, string | null>) => setRoute((r) => {
    const query = { ...r.query }
    for (const [k, v] of Object.entries(patch)) { if (v === null) delete query[k]; else query[k] = v }
    return { ...r, query }
  })
  return <TriggersSection sub={route.sub} navigate={navigate} navEpoch={0} query={route.query} setQuery={setQuery as never} />
}

beforeEach(() => { sessionStorage.clear() })

describe('a home whose list holds only the system triggers', () => {
  it('offers no gallery on the list — the premise', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getAllByText('Heartbeat tasks').length).toBeGreaterThan(0))
    expect(screen.queryByRole('heading', { name: 'No triggers' })).not.toBeInTheDocument()
  })

  it('reaches every preset from "New trigger", and a pick fills the form', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getAllByText('Heartbeat tasks').length).toBeGreaterThan(0))
    await userEvent.click(screen.getAllByRole('button', { name: /New trigger/ })[0])

    const presets = await waitFor(() => screen.getByRole('group', { name: 'Start from a preset' }))
    for (const p of TRIGGER_PRESETS)
      expect(within(presets).getByRole('button', { name: p.title }), p.id).toBeInTheDocument()
    expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toBe('')

    await userEvent.click(within(presets).getByRole('button', { name: 'Morning briefing' }))

    // Every field took the seed — the name and the cadence as well as the action — which is what
    // mounting a fresh page on the new `?preset=` buys over re-rendering the old one.
    await waitFor(() => expect((screen.getByRole('textbox', { name: /^Name/ }) as HTMLInputElement).value).toBe('Morning briefing'))
    const cron = await waitFor(() => screen.getByRole('textbox', { name: /Cron expression/i }) as HTMLInputElement)
    expect(cron.value).toBe('0 8 * * *')
    await waitFor(() => expect(screen.getByText('Invoke Agent')).toBeInTheDocument())
    expect(screen.getByText(/Filled in from the/)).toBeInTheDocument()
    // A seeded form names its preset instead of offering the others over what it now holds.
    expect(screen.queryByRole('group', { name: 'Start from a preset' })).not.toBeInTheDocument()
  })
})
