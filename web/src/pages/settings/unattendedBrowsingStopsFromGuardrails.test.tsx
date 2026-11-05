import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { BrowseStatus } from '../../lib/api'

// ── The switch that stops unattended browsing is in Settings › Guardrails ───────────────────────
//
// Home's Browse live view carried a Stop button and the expired sign-in banners; it now shows only
// while a browse runs, so they moved where they do not hide: Settings › Security › Guardrails ›
// Unattended browsing, beside the incident kill switch. Stopping is one click; letting it run
// again is asked first. The sites whose saved sign-in expired are listed under it.

const browseStatus = vi.fn<() => Promise<BrowseStatus>>()
const browseKill = vi.fn()
const browseKillRelease = vi.fn()
const confirm = vi.fn<() => Promise<boolean>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    browseStatus: () => browseStatus(),
    browseKill: (reason: string) => browseKill(reason),
    browseKillRelease: () => browseKillRelease(),
  },
}))
vi.mock('../../ui/dialog', async (orig) => ({ ...(await orig<Record<string, unknown>>()), confirm: () => confirm() }))

const { BrowseKillSection } = await import('./GuardrailsPanel')
const { resetDataStore } = await import('../../lib/data/store')

const RUNNING: BrowseStatus = { live: false, kill: { active: false, reason: '', started_at: '' }, expired: [], grants: [] }
const STOPPED: BrowseStatus = { ...RUNNING, kill: { active: true, reason: 'Stopped from Settings', started_at: '2026-11-04T06:00:00Z' } }

beforeEach(() => {
  resetDataStore(); sessionStorage.clear()
  vi.clearAllMocks()
  browseStatus.mockResolvedValue(RUNNING)
  browseKill.mockResolvedValue({ kill: STOPPED.kill })
  browseKillRelease.mockResolvedValue({ kill: RUNNING.kill })
})

describe('Settings › Guardrails › Unattended browsing', () => {
  it('stops every unattended browse in one click', async () => {
    render(<BrowseKillSection />)
    const sw = await screen.findByRole('switch', { name: 'Unattended browsing' })
    expect(sw).toHaveAttribute('aria-checked', 'true')

    browseStatus.mockResolvedValue(STOPPED)
    await userEvent.click(sw)

    expect(browseKill).toHaveBeenCalledWith('Stopped from Settings')
    expect(confirm).not.toHaveBeenCalled()
    expect(await screen.findByText(/Unattended browsing is STOPPED/)).toBeTruthy()
  })

  it('asks before it lets browsing run again, and does nothing on a no', async () => {
    browseStatus.mockResolvedValue(STOPPED)
    confirm.mockResolvedValue(false)
    render(<BrowseKillSection />)
    const sw = await screen.findByRole('switch', { name: 'Unattended browsing' })

    await userEvent.click(sw)

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(browseKillRelease).not.toHaveBeenCalled()

    confirm.mockResolvedValue(true)
    await userEvent.click(sw)
    expect(browseKillRelease).toHaveBeenCalledTimes(1)
  })

  it('lists each site whose saved sign-in expired', async () => {
    browseStatus.mockResolvedValue({ ...RUNNING, expired: [{ site: 'billing.example.com' }] })
    render(<BrowseKillSection />)

    expect(await screen.findByText('Sign-in needed for billing.example.com')).toBeTruthy()
  })
})
