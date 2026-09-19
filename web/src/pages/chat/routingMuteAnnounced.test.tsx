import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── The dismissal that mutes an agent forever must not look like the two before it (issue 414) ────
//
// `record_dismiss` bumps a counter and, at the third dismissal, appends the agent to `muted`. There
// is NO expiry — the only reads of `muted` are the membership test in `is_suppressed` and the removal
// in `unmute`, and `is_suppressed` returns on the mute BEFORE it reads `cooldown_hours`, so neither
// the "Dismiss cooldown" field nor the section's master switch clears it. Measured live:
//
//     dismiss 1 → {"count":1,"muted":false}
//     dismiss 2 → {"count":2,"muted":false}
//     dismiss 3 → {"count":3,"muted":true}      ← permanent from here, and nothing was said
//     PATCH agents_routing.cooldown_hours = 0   → 200, muted still ["zz414-probe-agent"]
//     PATCH agents_routing.enabled false→true   → 200, muted still ["zz414-probe-agent"]
//
// The response already carried `{count, muted}` and the chip discarded it, so the third ✕ silently
// ended all future suggestions for that agent with no notice anywhere in the product. The user's
// symptom — "that agent stopped being offered" — arrives days later, detached from the click.
//
// 🪤 THE FIRST TWO DISMISSALS MUST STAY QUIET. A toast on every ✕ turns the chip's own dismissal into
// a nag, which is the thing the cooldown exists to prevent; the message is for the state CHANGE only.
// And the chip still hides on all three (a dismissal is a request to get something out of the way) —
// `dismissalFailureReported.test.ts` owns that ruling and this rail must not contradict it.

const notified: { msg: string; level?: string }[] = []
let dismissResponse: { ok: boolean; count: number; muted: boolean } = { ok: true, count: 1, muted: false }

function mockApi() {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        routingDismiss: () => Promise.resolve(dismissResponse),
        recordFeedback: () => Promise.resolve({ ok: true }),
        setSessionAgent: () => Promise.resolve({ ok: true }),
      },
    }
  })
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string, level?: string) => { notified.push({ msg, level }) },
  }))
}

beforeEach(() => { vi.resetModules(); notified.length = 0 })

async function dismissOnce(response: typeof dismissResponse) {
  dismissResponse = response
  mockApi()
  const { RoutingChip } = await import('./RoutingChip')
  const onDismiss = vi.fn()
  render(
    <RoutingChip
      suggestion={{ session: 's1', agent: 'zz414-probe-agent', specialty: 'probes', score: 0.9, method: 'keyword' }}
      defaultAgent="PersonalClaw" onRoute={vi.fn()} onDismiss={onDismiss} />,
  )
  fireEvent.click(screen.getByRole('button', { name: /Not now/i }))
  return onDismiss
}

describe('crossing the mute threshold is announced, and names the undo', () => {
  it('the third dismissal says the agent will not be suggested again', async () => {
    await dismissOnce({ ok: true, count: 3, muted: true })
    await waitFor(() => expect(notified.length).toBe(1))
    expect(notified[0].msg, 'names the agent that went quiet').toContain('zz414-probe-agent')
    expect(notified[0].msg, 'and where to undo it — the promise the panel makes')
      .toMatch(/Settings › Chat › Agent routing › Muted agents/)
    expect(notified[0].level, 'a state change, not a failure').toBe('info')
  })

  it('the first two dismissals stay silent — the message is for the state change', async () => {
    await dismissOnce({ ok: true, count: 1, muted: false })
    await new Promise((r) => setTimeout(r, 50))
    expect(notified, 'a toast on every dismissal would be the nag the cooldown prevents').toEqual([])
  })

  it('the chip still hides on the muting dismissal — the pinned opposite ruling', async () => {
    const onDismiss = await dismissOnce({ ok: true, count: 3, muted: true })
    expect(onDismiss, 'a dismissal is a request to get something out of the way').toHaveBeenCalled()
  })
})
