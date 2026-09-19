import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'

// ── A health verdict states when it was taken ─────────────────────────────────────────
//
// Found by censusing the WHOLE doctor payload rather than the field the issue named.
// `/api/doctor` ships `generated_at` (epoch seconds, set at `resilience/doctor.py:243`) and it had
// **0 readers anywhere in `web/src`** — so `All systems healthy` was a claim with no as-of. On a
// panel left open, or reopened against a report fetched minutes ago, that green line describes a
// moment the reader cannot see.
//
// Same shape as the deficit list this cycle fixed: measured, shipped over the wire, dropped at the
// last step. Different payload, so it is pinned here rather than in the remediation rail.
//
// Left deliberately unread, and this is the record of the decision: `DoctorProbe.capability`. The
// probe rows are already GROUPED by capability — the card's own title IS that key — so repeating it
// on every row inside the card is redundant, not evidence.

const REPORT = (over: Record<string, unknown> = {}) => ({
  ok: true,
  core_ok: true,
  worst: '',
  restart_suggested: false,
  capabilities: {},
  skipped_capabilities: [],
  generated_at: Math.floor(Date.now() / 1000) - 3 * 3600,
  ...over,
})

async function mount(over: Record<string, unknown> = {}) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({
    api: {
      doctor: () => Promise.resolve(REPORT(over)),
      doctorRemediation: () => Promise.resolve({ score: 100, target_score: 90, deficits: [], plan: [], recent_runs: [] }),
      doctorRemediationRun: () => Promise.resolve({}),
      doctorSimulateSurfacing: () => Promise.resolve({ candidates: [] }),
      doctorSimulateAutomation: () => Promise.resolve({}),
      triggers: () => Promise.resolve({ triggers: [] }),
    },
  }))
  const { DoctorPanel } = await import('./DoctorPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<DoctorPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('the doctor verdict carries its own timestamp', () => {
  it('stamps a healthy verdict', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('All systems healthy')
    expect(text, 'a verdict with no as-of is a claim about an unseen moment').toContain('checked 3h ago')
  })

  it('stamps a core failure too, restart hint and all', async () => {
    const text = (await mount({ ok: false, core_ok: false, restart_suggested: true })).container.textContent ?? ''
    expect(text).toContain('a restart may be required')
    expect(text).toContain('checked 3h ago')
  })

  it('stamps a degraded capability', async () => {
    const text = (await mount({ ok: false, worst: 'model-providers' })).container.textContent ?? ''
    expect(text).toContain('Model providers degraded')
    expect(text).toContain('checked 3h ago')
  })
})
