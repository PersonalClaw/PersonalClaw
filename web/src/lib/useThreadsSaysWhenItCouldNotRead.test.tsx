import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { WsMessage } from './useChatSocket'
import { runState, runningState } from '../test/runState'

// ── useThreads reads the four lists together, and a failed read is said, not shown as empty ─────
//
// An empty sidebar says "you have no threads". A read that failed must not say that: the hook
// hands back the error and no groups, so the sidebar can say it could not load (#3814's approved
// update: failed project reads have explicit rows and never pass as an empty project).

let deliver: (m: WsMessage) => void = () => {}
const wire = { fail: false, running: false, reads: 0 }

function mockDeps() {
  vi.doMock('./useChatSocket', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    useChatSocket: (onMessage: (m: WsMessage) => void) => { deliver = onMessage },
  }))
  vi.doMock('./api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      chatSessions: () => {
        wire.reads++
        if (wire.fail) return Promise.reject(new Error('gateway unreachable'))
        const run_state = wire.running ? runningState({ session: 'chat-1' }) : runState({ session: 'chat-1' })
        return Promise.resolve([{ key: 'chat-1', title: 'Plan the garden', messages: 2, run_state, project_id: 'p-garden' }])
      },
      uLoops: () => Promise.resolve([{
        id: 'a1b2c3d4', kind: 'code', name: 'Fence gate', task: 'fix the gate', status: 'failed', total_cycles: 2,
        max_cycles: 10, started_at: 1_000, project_id: 'p-garden',
      }]),
      inboxOpen: () => Promise.resolve([]),
      approvals: () => Promise.resolve([]),
    },
  }))
}

async function mountProbe() {
  const { useThreads } = await import('./useThreads')
  function Probe() {
    const { groups, loading, error } = useThreads()
    return (
      <ul data-testid="threads">
        {loading && <li>loading</li>}
        {error ? <li>error</li> : null}
        {groups.flatMap((g) => g.threads.map((t) => <li key={t.key}>{`${g.projectId}|${t.key}|${t.pip ?? 'none'}`}</li>))}
      </ul>
    )
  }
  render(<Probe />)
}

const shown = () => screen.getByTestId('threads').textContent ?? ''

beforeEach(() => {
  vi.resetModules()
  wire.fail = false
  wire.running = false
  wire.reads = 0
  mockDeps()
})
afterEach(() => { vi.restoreAllMocks() })

describe('useThreads', () => {
  it('folds the chat and loop lists into threads under their project', async () => {
    await mountProbe()
    await waitFor(() => expect(shown()).toContain('p-garden|chat:chat-1|none'))
    expect(shown()).toContain('p-garden|loop:a1b2c3d4|failed')
    expect(shown()).not.toContain('error')
  })

  it('a failed read is an error with no threads, never an empty list', async () => {
    wire.fail = true
    await mountProbe()
    await waitFor(() => expect(shown()).toContain('error'))
    expect(shown()).not.toContain('chat:chat-1')
    expect(shown()).not.toContain('loading')
  })

  it("a chat's phase frame re-reads the lists, so its pip follows", async () => {
    await mountProbe()
    await waitFor(() => expect(shown()).toContain('chat:chat-1|none'))
    const before = wire.reads
    wire.running = true
    deliver({ type: 'work_phase', data: { session: 'chat-1' } })
    await waitFor(() => expect(shown()).toContain('chat:chat-1|running'), { timeout: 3000 })
    expect(wire.reads).toBeGreaterThan(before)
  })
})
