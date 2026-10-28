import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { useEffect } from 'react'

// ── A turn's head is brought into view in the commit that shows it ──────────────────────────────
//
// The history page's peek scrolls its tail into view when the head of a running reply appears
// (`TurnHead`'s `onShown`). It was called from a passive effect, which React runs after the commit
// that paints the head: the head could be painted out of view for a frame, and the peek's own test
// saw it unscrolled about 1 send in 100 under load (`expected 2 to be greater than 2`). It is
// called in the commit now, before any passive effect of that commit: here, before the orb's own,
// which this file stands in for so the order is the one thing read.

const order: string[] = []

vi.mock('../../../ui/AgentOrb', () => ({
  AgentOrb: ({ phase }: { phase: string }) => {
    useEffect(() => { order.push('a passive effect of the same commit') }, [])
    return <span data-agent-orb data-orb-phase={phase} />
  },
}))

const { TurnHead, SHOW_DELAY_MS } = await import('./TurnHead')

beforeEach(() => { order.length = 0; vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

it('calls onShown in the commit that shows the head, before that commit’s passive effects', () => {
  render(<TurnHead running phase="thinking" line="" ending={null} onShown={() => order.push('onShown')} />)
  expect(order).toEqual([])
  act(() => { vi.advanceTimersByTime(SHOW_DELAY_MS) })
  expect(document.querySelector('[data-turn-head]')).not.toBeNull()
  expect(order).toEqual(['onShown', 'a passive effect of the same commit'])
})
