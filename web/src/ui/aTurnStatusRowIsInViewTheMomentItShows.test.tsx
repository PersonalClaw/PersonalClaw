import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { useEffect } from 'react'

// ── The turn's status row tells the transcript in the commit that shows it ──────────────────────
//
// The chat keeps its transcript's end in view above the row (`onShown`, ChatPage's `followEnd`).
// It was called from a passive effect, which React runs after the commit that paints the row: the
// row could sit over the transcript's end for a frame before the transcript followed. It is called
// in the commit now, before any passive effect of that commit: here, before the orb's own, which
// this file stands in for so the order is the one thing read.

const order: string[] = []

vi.mock('./AgentOrb', () => ({
  AgentOrb: ({ phase }: { phase: string }) => {
    useEffect(() => { order.push('a passive effect of the same commit') }, [])
    return <span data-agent-orb data-orb-phase={phase} />
  },
}))

const { TurnStatusRow, SHOW_DELAY_MS } = await import('./TurnStatusRow')

beforeEach(() => { order.length = 0; vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

it('calls onShown in the commit that shows the row, before that commit’s passive effects', () => {
  render(<TurnStatusRow running phase="thinking" line="" startedAt={null} usage={null} onStop={vi.fn()}
    onShown={() => order.push('onShown')} />)
  expect(order).toEqual([])
  act(() => { vi.advanceTimersByTime(SHOW_DELAY_MS) })
  expect(document.querySelector('[data-turn-status]')).not.toBeNull()
  expect(order).toEqual(['onShown', 'a passive effect of the same commit'])
})
