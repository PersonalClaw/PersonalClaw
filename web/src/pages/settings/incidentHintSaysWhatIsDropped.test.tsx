import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// ── The incident kill switch says what becomes of the work it stops ─────────────────────────────
//
// 🔴 Its hint said "Suspend ALL unattended work … at once", and the incident contract under it
// said nothing is lost: the skipped work is still due afterwards. A hook or event fire that came
// while it was on was dropped, and a scheduled automation ran at its next time, not at Resume. The
// hint now says both (`guardrails/incident.py`, seam by seam).

const incident = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    incident: () => incident(),
    incidentOn: vi.fn(),
    incidentResume: vi.fn(),
  },
}))

const { IncidentSection } = await import('./GuardrailsPanel')
const { resetDataStore } = await import('../../lib/data/store')

beforeEach(() => {
  resetDataStore(); sessionStorage.clear()
  vi.clearAllMocks()
  incident.mockResolvedValue({ active: false, reason: '', started_at: '' })
})

describe('Settings › Guardrails › Incident kill switch', () => {
  it('says a hook or event fire during it is dropped, and recurring work runs at its next time', async () => {
    render(<IncidentSection />)
    const hint = await screen.findByText(/a hook or event fire that happens while it is on is dropped/)
    expect(hint.textContent).toContain('Recurring scheduled work runs at its next time')
    expect(hint.textContent).toContain('a one-shot waits for you to run it')
    expect(hint.textContent).not.toMatch(/Suspend ALL unattended work/)
  })
})
