import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import type { WsMessage } from './useChatSocket'

// ── The sidebar reads its threads again when a frame says a loop moved (#3814) ───────────────────
//
// A loop is placed among its project's threads by when its newest cycle ran, which its row in
// `GET /api/loops` says (`last_cycle_at`). A loops-table loop's new cycle sends a `refresh` frame
// naming `loops`; a loop run on the workflow engine sends one naming `workflow_runs`, as Home, Mission
// Control and the companion's lane read it. Either is a loop that moved, so the rail reads its lists
// again (once, after the frames of a burst settle); a frame naming neither leaves it be.

let onFrame: ((m: WsMessage) => void) | null = null
const reads = vi.fn()

vi.mock('./useChatSocket', async (orig) => ({
  ...(await orig<typeof import('./useChatSocket')>()),
  useChatSocket: (handler: (m: WsMessage) => void) => { onFrame = handler },
}))
vi.mock('./api', async (orig) => ({
  ...(await orig<typeof import('./api')>()),
  api: {
    chatSessions: () => { reads(); return Promise.resolve([]) },
    uLoops: () => Promise.resolve([]),
    inboxOpen: () => Promise.resolve([]),
    approvals: () => Promise.resolve([]),
  },
}))

const { useThreads } = await import('./useThreads')
const { resetDataStore } = await import('./data/store')

const refresh = (...kinds: string[]): WsMessage => ({ type: 'refresh', data: { kinds } }) as WsMessage

beforeEach(() => {
  resetDataStore()
  reads.mockReset()
  vi.useFakeTimers()
})
afterEach(() => { vi.useRealTimers() })

/** The rail's read, settled: the number of times its lists were read. */
async function mounted() {
  renderHook(() => useThreads())
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  return reads.mock.calls.length
}

async function frame(m: WsMessage) {
  await act(async () => { onFrame?.(m); await vi.advanceTimersByTimeAsync(400) })
}

describe('the sidebar reads its threads again', () => {
  it.each([['loops'], ['workflow_runs']])('when a frame says %s changed', async (kind) => {
    const first = await mounted()
    await frame(refresh(kind))
    expect(reads.mock.calls.length).toBe(first + 1)
  })

  it('once for a burst of frames', async () => {
    const first = await mounted()
    await act(async () => {
      onFrame?.(refresh('loops'))
      onFrame?.(refresh('workflow_runs'))
      await vi.advanceTimersByTimeAsync(400)
    })
    expect(reads.mock.calls.length).toBe(first + 1)
  })

  it('and not for a frame that names neither', async () => {
    const first = await mounted()
    await frame(refresh('tasks', 'knowledge'))
    expect(reads.mock.calls.length).toBe(first)
  })
})
