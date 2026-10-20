import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Settings › System is where the machine's telemetry lives ─────────────────────────────────────
//
// Home used to dock the system health strip along its bottom edge and carry a second band of it,
// "On this machine", further down. Both left Home (`dashboard/homeShowsOnlyWhatWaits.test.tsx`), and
// nothing they offered may be lost on the way, so this file holds the new home to every part:
//
//   • the live strip: uptime, version, cpu with its sparkline, memory, network, disk, load, triggers
//     and subagents, the Update action and the YOLO notice, and the Doctor rollup that opens Doctor;
//   • what is holding memory: the resident local models with their Unload, and the pressure meter;
//   • a card on the Settings hub, so the section is reached by a click and found by search, and a
//     place in the axe manifest, so it is scanned.

vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

const STATUS = {
  uptime: '5m 46s', version: '0.3.0', platform: 'darwin', triggers: 4, subagents: 1,
  update_available: true, yolo: true,
}
const SYSTEM = {
  platform: 'darwin', hostname: 'mb', os: 'Darwin', python: '3.12', arch: 'arm64', pid: 1, cwd: '/',
  cpu_count: 8, cpu_pct: 22, mem_total_gb: 16, mem_used_gb: 6.5, mem_free_gb: 9.5, proc_mem_mb: 120,
  load_1m: 1.25, load_5m: 1, load_15m: 1, disk_total_gb: 500, disk_free_gb: 300,
  net_rx_kbs: 120, net_tx_kbs: 40,
}
const LOADED = {
  loaded: [{ provider: 'sentence-transformers', model: 'all-MiniLM-L6-v2', kind: 'sidecar', rss_mb: 812, is_active: false, generation: 1 }],
  providers: [],
  pressure: { total_mb: 16384, used_mb: 6656, available_mb: 9728, used_pct: 41, warn_pct: 90, warn: false, source: 'meminfo' },
}

/** How many times each api read was made. Hoisted so the mock factory can reach it. */
const calls: Record<string, number> = vi.hoisted(() => ({}))
let statusRead: () => Promise<unknown> = () => Promise.resolve(STATUS)
let systemRead: () => Promise<unknown> = () => Promise.resolve(SYSTEM)
let doctor: () => Promise<unknown> = () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} })

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const reads: Record<string, () => Promise<unknown>> = {
    status: () => statusRead(),
    system: () => systemRead(),
    doctor: () => doctor(),
    modelsLoaded: () => Promise.resolve(LOADED),
    notifications: () => Promise.resolve({ notifications: [] }),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    triggersHistory: () => Promise.resolve({ runs: [], did_ids: [] }),
    discover: () => Promise.resolve({ enabled: false, visible_count: 0, areas: [], tips: [] }),
  }
  const api = new Proxy({}, {
    get: (_t, prop: string) => () => {
      calls[prop] = (calls[prop] ?? 0) + 1
      return prop in reads ? reads[prop]() : Promise.resolve([])
    },
  })
  return { ...real, api }
})

const navigate = vi.fn()

beforeEach(() => {
  navigate.mockReset()
  for (const k of Object.keys(calls)) delete calls[k]
  statusRead = () => Promise.resolve(STATUS)
  systemRead = () => Promise.resolve(SYSTEM)
  doctor = () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} })
  sessionStorage.clear()
})
afterEach(cleanup)

async function mountSystem() {
  const { SettingsPage } = await import('./SettingsPage')
  render(<SettingsPage sub="system" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)
}

describe('Settings › System', () => {
  it('is a section of its own, titled System', async () => {
    await mountSystem()
    expect(screen.getByRole('heading', { level: 1, name: 'System' })).toBeTruthy()
  })

  it('renders the live health strip, every metric with its word', async () => {
    await mountSystem()
    await waitFor(() => expect(screen.getByText('cpu')).toBeInTheDocument())
    for (const word of ['uptime', 'cpu', 'mem', 'net', 'disk', 'load · 8cpu', 'triggers', 'subagents']) {
      expect(screen.getByText(word), `the strip's "${word}" metric moved with it`).toBeInTheDocument()
    }
    expect(screen.getByText('22%')).toBeInTheDocument()
    expect(screen.getByText('v0.3.0')).toBeInTheDocument()
  })

  it('keeps the Update action and the YOLO notice', async () => {
    await mountSystem()
    expect(await screen.findByRole('button', { name: /Update available/ })).toBeTruthy()
    expect(screen.getByText('YOLO active')).toBeTruthy()
  })

  it('keeps the Doctor rollup, and it opens Doctor', async () => {
    doctor = () => Promise.resolve({ ok: false, core_ok: true, worst: 'model-providers', capabilities: {} })
    await mountSystem()
    const row = await screen.findByRole('button', { name: /Model providers degraded/ })
    row.click()
    expect(navigate).toHaveBeenCalledWith('settings/doctor')
  })

  it('a status read that failed says so, with Retry, instead of loading forever', async () => {
    let fail = true
    statusRead = () => (fail ? Promise.reject(new Error('down')) : Promise.resolve(STATUS))
    await mountSystem()
    const err = await screen.findByText(/Couldn.t read the gateway.s status/)
    fail = false
    err.closest('[role="alert"]')!.querySelector('button')!.click()
    expect(await screen.findByText('uptime')).toBeInTheDocument()
    expect(screen.queryByText(/Couldn.t read the gateway.s status/)).toBeNull()
  })

  it('a metrics read that failed says so, rather than leaving the metrics out', async () => {
    systemRead = () => Promise.reject(new Error('down'))
    await mountSystem()
    expect(await screen.findByText(/Host metrics couldn.t be read/)).toBeInTheDocument()
    expect(screen.getByText('uptime'), 'the status half still renders').toBeInTheDocument()
  })

  it('sends what is holding memory to Settings › Models, its one home', async () => {
    await mountSystem()
    await waitFor(() => expect(screen.getByText('cpu')).toBeInTheDocument())
    // Settings › Models already lists the resident models with Unload and the pressure meter, under
    // this same title; a second, smaller copy here would be two answers to one question.
    expect(screen.queryByRole('heading', { name: 'On this machine' })).toBeNull()
    expect(screen.queryByRole('progressbar', { name: /system memory in use/i })).toBeNull()
    expect(calls.modelsLoaded ?? 0, 'nor is the loaded-models read made here').toBe(0)
    screen.getByRole('button', { name: /Settings › Models/ }).click()
    expect(navigate).toHaveBeenCalledWith('settings/models')
  })

  it('reads only the machine: no Home lanes are polled from here', async () => {
    await mountSystem()
    await waitFor(() => expect(screen.getByText('cpu')).toBeInTheDocument())
    for (const read of ['status', 'system', 'doctor']) {
      expect(calls[read] ?? 0, `${read} is what this page shows`).toBeGreaterThan(0)
    }
    for (const read of ['uLoops', 'readyTasks', 'approvals', 'inboxOpen', 'skillProposals', 'triggersHistory', 'discover']) {
      expect(calls[read] ?? 0, `${read} is Home's, and nothing here shows it`).toBe(0)
    }
  })
})

describe('Settings › System is reached and scanned', () => {
  const WEB = process.cwd()

  it('has a card on the Settings hub, in the System group', () => {
    const src = readFileSync(join(WEB, 'src/pages/settings/settingsWidgets.tsx'), 'utf8')
    expect(src).toMatch(/id: 'system', group: 'System', label: 'System'/)
  })

  it('is in the axe manifest', () => {
    const src = readFileSync(join(WEB, 'e2e/routes.ts'), 'utf8')
    const block = src.match(/export const SETTINGS_PANELS = \[([\s\S]*?)\] as const/)
    expect(block?.[1]).toMatch(/'system'/)
  })
})
