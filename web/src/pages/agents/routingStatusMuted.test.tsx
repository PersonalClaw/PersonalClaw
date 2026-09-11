import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

/**
 * AR2-8 — the muted-agent affordance on AgentDetail.
 *
 * A muted agent is otherwise invisible on this page, so `RoutingStatusView` is the one place a
 * user can SEE that the auto-router stopped suggesting an agent and REVERSE it. The backend
 * round-trip (`routingStatus` ↔ `routingUnmute`) is covered by pytest; what had no test was the
 * FE half of the done_when — "a muted agent shows its muted state … and an Unmute control restores
 * it; state reflected via routingStatus". This renders the real component against a mocked api and
 * asserts exactly that, plus the refused-write discipline the component was written for (a failed
 * Unmute must surface an error and NOT claim success). A non-muted contrast keeps it non-vacuous.
 */

const { routingStatus, routingUnmute } = vi.hoisted(() => ({
  routingStatus: vi.fn(),
  routingUnmute: vi.fn(),
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const orig = await importOriginal<Record<string, unknown>>()
  return { ...orig, api: { ...(orig.api as object), routingStatus, routingUnmute } }
})

import { RoutingStatusView } from './AgentDetail'

beforeEach(() => {
  routingStatus.mockReset()
  routingUnmute.mockReset()
})
afterEach(() => cleanup())

describe('AR2-8 routing status: muted state + Unmute (AgentDetail)', () => {
  it('a muted agent shows the muted row + Unmute; clicking it calls routingUnmute and flips to Active', async () => {
    routingStatus.mockResolvedValue({ muted: ['scout'] })
    routingUnmute.mockResolvedValue({ ok: true })
    render(<RoutingStatusView agentName="scout" />)

    // The muted state is shown (reflected via routingStatus), with an Unmute control.
    expect(await screen.findByText(/Muted — the auto-router stopped suggesting/i)).toBeTruthy()
    const unmute = screen.getByRole('button', { name: /Unmute/i })

    await userEvent.click(unmute)

    // Unmute restores it: the API is called for THIS agent and the row flips to Active.
    await waitFor(() => expect(routingUnmute).toHaveBeenCalledWith('scout'))
    expect(await screen.findByText(/Active — eligible for auto-routing/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Unmute/i })).toBeNull()
  })

  it('contrast: an agent that is not muted shows Active and no Unmute (keeps the test honest)', async () => {
    routingStatus.mockResolvedValue({ muted: [] })
    render(<RoutingStatusView agentName="scout" />)

    expect(await screen.findByText(/Active — eligible for auto-routing/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Unmute/i })).toBeNull()
  })

  it('a refused Unmute surfaces the error and stays muted — never a false success', async () => {
    routingStatus.mockResolvedValue({ muted: ['scout'] })
    routingUnmute.mockRejectedValue(new Error('unmute refused'))
    render(<RoutingStatusView agentName="scout" />)

    await screen.findByText(/Muted — the auto-router stopped suggesting/i)
    await userEvent.click(screen.getByRole('button', { name: /Unmute/i }))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/unmute refused/i)
    // The row must still read muted — the backend refused, so claiming Active would be a lie.
    expect(screen.getByText(/Muted — the auto-router stopped suggesting/i)).toBeTruthy()
  })
})
