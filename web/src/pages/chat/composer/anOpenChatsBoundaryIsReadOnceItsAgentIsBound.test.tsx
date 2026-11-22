import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

// ── An open chat's sandbox reading follows its agent once the gateway has bound it ──────────────
//
// The Execution Boundary reads an open chat's sandbox and network from the gateway, which reads the
// agent the chat is bound to there (`GET /api/chat/boundary`). Picking another agent read it again
// at once, while the binding write was still on its way, so the reading could show the agent the
// chat was leaving until the next read. It is read again once the binding write answers instead.

const reads = vi.hoisted(() => ({ calls: [] as unknown[] }))

vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  const reading = {
    sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
    reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
  }
  return { ...real, api: { ...real.api, chatBoundary: (q: unknown) => { reads.calls.push(q); return Promise.resolve(reading) } } }
})

const { useBoundaryReading } = await import('./ExecutionBoundary')

const pause = () => act(() => new Promise((r) => setTimeout(r, 30)))

beforeEach(() => { reads.calls.length = 0 })

describe("an open chat's boundary after an agent switch", () => {
  it('🔴 is read again once the binding write answers, not when the pick changes', async () => {
    const { rerender } = renderHook(
      ({ runtime, bound }) => useBoundaryReading('chat-3-boundary', true, runtime, undefined, '', bound),
      { initialProps: { runtime: '', bound: 0 } },
    )
    await waitFor(() => expect(reads.calls).toHaveLength(1))

    rerender({ runtime: 'acp:codex', bound: 0 })
    await pause()
    expect(reads.calls, 'read while the binding was still on its way').toHaveLength(1)

    rerender({ runtime: 'acp:codex', bound: 1 })
    await waitFor(() => expect(reads.calls).toHaveLength(2))
  })

  it('a chat not started yet is read for the agent it is set to, at once, as before', async () => {
    const { rerender } = renderHook(
      ({ runtime }) => useBoundaryReading(null, true, runtime),
      { initialProps: { runtime: '' } },
    )
    await waitFor(() => expect(reads.calls).toHaveLength(1))
    rerender({ runtime: 'acp:codex' })
    await waitFor(() => expect(reads.calls).toHaveLength(2))
    expect(reads.calls[1]).toMatchObject({ runtime: 'acp:codex' })
  })
})
