import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// ── Home's system rail on a poll that is missing a reading ──────────────────────────────────────
//
// Same payload, same defect as the shell's System status card (`ui/systemCardMissingReading.test`):
// `/api/system` leaves out a reading its probe could not take under load, and the rail gated its
// memory metric on `mem_total_gb > 0` (the total, read once at startup, survives) and then printed
// `mem_used_gb.toFixed(1)` (the live reading, which did not). That threw on Home. It now prints the
// placeholder for the missing half, and the CPU figure does the same.

const FULL = {
  platform: 'darwin', hostname: 'mb', os: 'Darwin', python: '3.13', arch: 'arm64', pid: 1, cwd: '/',
  cpu_count: 18, cpu_pct: 22, mem_total_gb: 48, mem_used_gb: 31.8, mem_free_gb: 16.2, proc_mem_mb: 120,
  load_1m: 6.07, load_5m: 5, load_15m: 4, disk_total_gb: 926, disk_free_gb: 623,
  net_rx_kbs: 9728, net_tx_kbs: 10956,
}
const { mem_used_gb: _u, mem_free_gb: _f, cpu_pct: _c, load_1m: _l1, load_5m: _l5, load_15m: _l15, ...PARTIAL } = FULL
const STATUS = {
  uptime: '5m 46s', version: '0.2.0', platform: 'darwin', triggers: 4, subagents: 0,
  update_available: false,
}

function mockApi(system: Record<string, unknown>) {
  vi.doMock('../../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve(STATUS),
      system: () => Promise.resolve(system),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ tips: [] }),
      approvals: () => Promise.resolve([]),
      inboxOpen: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ entries: [] }),
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

/** The value a labelled metric shows, by its word-label. */
function valueOf(label: string): string | null | undefined {
  const word = [...document.querySelectorAll('span[data-type="body-m"]')].find((s) => s.textContent === label)
  return word?.parentElement?.querySelector('span[data-type="title-m"]')?.textContent
}

async function mountStrip() {
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const { SystemHealth } = await import('./SystemHealth')
  render(
    <DashboardLiveProvider>
      <SystemHealth navigate={vi.fn()} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
    </DashboardLiveProvider>,
  )
  // `cpu` renders once the live /api/system slice has arrived.
  await waitFor(() => expect(valueOf('cpu')).toBeTruthy())
}

describe('the Home system rail on a poll missing readings', () => {
  it('🔴 renders, with the placeholder for CPU and the missing half of memory', async () => {
    mockApi(PARTIAL)
    await mountStrip()
    expect(valueOf('cpu')).toBe('—')
    expect(valueOf('mem')).toBe('—/48GB')
    // A reading that WAS taken still renders; load is shown only when the host reports it.
    expect(valueOf('disk')).toBe('303/926GB')
    expect(valueOf('load · 18cpu')).toBeUndefined()
    expect(screen.queryByText(/NaN/)).toBeNull()
  })

  it('and the full reading is unchanged', async () => {
    mockApi(FULL)
    await mountStrip()
    expect(valueOf('cpu')).toBe('22%')
    expect(valueOf('mem')).toBe('31.8/48GB')
    expect(valueOf('load · 18cpu')).toBe('6.07')
  })
})
