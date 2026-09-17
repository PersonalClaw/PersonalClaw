import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { RoutingSuggestion } from './RoutingChip'

// ── AGENT-ROUTING S2 (AR2-5): the routing chip's FEEDBACK-SIGNAL double-write ──
//
// The chip's headline contract is not that it renders — it is that BOTH the accept
// AND the dismiss become training data, each as a dual-attributed feedback record
// (`routing_suggestion` target + `routing_pair` producer). The design reason lives
// in the atom: a suggester that learns only from accepts sees a biased sample —
// every dismissal is the MORE informative half, because it is the case the
// classifier got wrong. So a dropped dismiss-signal is not a cosmetic miss; it
// silently poisons the training set toward false confidence.
//
// This mounts the real component and drives Route / dismiss, asserting each side of
// the double-write independently — so deleting EITHER `api.recordFeedback(...)` call
// reddens a test (the accept path AND the dismiss path are each pinned). It also
// pins the two things the double-write rides on: Route re-targets through the
// EXISTING agent-switch path (`api.setSessionAgent`), and dismiss suppresses through
// the mute endpoint (`api.routingDismiss`) — so a refactor cannot keep the feedback
// write while quietly dropping the action it is feedback ABOUT.

const setSessionAgent = vi.fn(() => Promise.resolve())
const recordFeedback = vi.fn(() => Promise.resolve())
const routingDismiss = vi.fn(() => Promise.resolve())

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: { setSessionAgent, recordFeedback, routingDismiss },
}))
// `notify` fires on the success toast and on a reportingWrite failure; stub it so the
// test observes calls, not DOM toasts. RoutingChip and reportingWrite both import it
// from this one module, so a single mock covers both.
vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  notify: vi.fn(),
}))

const { RoutingChip } = await import('./RoutingChip')

const sugg = (over: Partial<RoutingSuggestion> = {}): RoutingSuggestion => ({
  session: 'sess-1', agent: 'code-reviewer', specialty: 'reviews code', score: 0.82,
  method: 'embedding', ...over,
})

// The dual-attribution shared by both verdicts — the pair that makes it a *double*-write.
const DUAL = {
  target_kind: 'routing_suggestion', target_id: 'sess-1:code-reviewer',
  producer_kind: 'routing_pair', producer_id: 'general->code-reviewer',
}

beforeEach(() => {
  setSessionAgent.mockClear()
  recordFeedback.mockClear()
  routingDismiss.mockClear()
})

describe('RoutingChip — WS-driven pill + FEEDBACK-SIGNAL double-write', () => {
  it('renders as a non-blocking status pill naming the suggested agent', () => {
    render(<RoutingChip suggestion={sugg()} defaultAgent="general" onRoute={() => {}} onDismiss={() => {}} />)
    const pill = screen.getByRole('status') // status, not alert/dialog — it does not trap or block
    expect(pill.textContent).toContain('code-reviewer')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(screen.getByRole('button', { name: 'Route' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Not now/i })).toBeInTheDocument()
  })

  it('Route re-targets via setSessionAgent AND double-writes POSITIVE feedback on the routing pair', async () => {
    const onRoute = vi.fn()
    render(<RoutingChip suggestion={sugg()} defaultAgent="general" onRoute={onRoute} onDismiss={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Route' }))
    await waitFor(() => expect(onRoute).toHaveBeenCalledTimes(1))
    // the existing agent-switch path — the chip re-uses it rather than minting a new one
    expect(setSessionAgent).toHaveBeenCalledWith('sess-1', 'code-reviewer')
    // the double-write: exactly one dual-attributed record, verdict `up`
    expect(recordFeedback).toHaveBeenCalledTimes(1)
    expect(recordFeedback).toHaveBeenCalledWith(expect.objectContaining({ ...DUAL, verdict: 'up' }))
  })

  it('dismiss suppresses via routingDismiss AND double-writes NEGATIVE feedback on the routing pair', async () => {
    const onDismiss = vi.fn()
    render(<RoutingChip suggestion={sugg()} defaultAgent="general" onRoute={() => {}} onDismiss={onDismiss} />)
    fireEvent.click(screen.getByRole('button', { name: /Not now/i }))
    await waitFor(() => expect(onDismiss).toHaveBeenCalledTimes(1))
    // the mute endpoint — the informative-half rejection is what the pair learns from
    expect(routingDismiss).toHaveBeenCalledWith('code-reviewer')
    expect(recordFeedback).toHaveBeenCalledTimes(1)
    expect(recordFeedback).toHaveBeenCalledWith(expect.objectContaining({ ...DUAL, verdict: 'down' }))
  })

  it('an absent default agent attributes the pair to `default`, not to undefined', async () => {
    const onDismiss = vi.fn()
    render(<RoutingChip suggestion={sugg()} defaultAgent="" onRoute={() => {}} onDismiss={onDismiss} />)
    fireEvent.click(screen.getByRole('button', { name: /Not now/i }))
    await waitFor(() => expect(onDismiss).toHaveBeenCalledTimes(1))
    expect(recordFeedback).toHaveBeenCalledWith(
      expect.objectContaining({ producer_id: 'default->code-reviewer', verdict: 'down' }),
    )
  })
})
