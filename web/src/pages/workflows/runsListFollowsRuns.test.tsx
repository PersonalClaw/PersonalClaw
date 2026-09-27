// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { WsMessage } from '../../lib/useChatSocket'

// ── The Workflows list's Runs tab follows a run while it is open ───────────────────────────────
//
// It read its runs once. A run finishing, failing or parking kept its old status on this list
// until a reload — the same stale read Mission Control's Working lane had. A run changing status
// reaches the socket as the gateway's listing hint (`refresh` naming `workflow_runs`,
// `workflows/watchdog._raw_publish`), and the list re-reads on it.

const workflowRuns = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    workflowDefs: () => Promise.resolve({ defs: [], total: 0 }),
    workflowRuns: (...a: unknown[]) => workflowRuns(...a),
    workflowSurfacing: () => Promise.resolve({ defs: [], total: 0, findings: [] }),
  },
}))

let frame: ((m: WsMessage) => void) | null = null
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: (onMessage: (m: WsMessage) => void) => { frame = onMessage },
}))

import { WorkflowsListPage } from './WorkflowsListPage'

const run = (id: string, name: string, status = 'running') => ({
  id, workflow_name: name, status, spec_version: 1, started_at: '2026-09-26T09:00:00Z', created_at: '2026-09-26T09:00:00Z',
})

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  frame = null
  workflowRuns.mockResolvedValue({ runs: [run('r1', 'code-project')], total: 1 })
})

describe('the Runs tab, while a run changes', () => {
  it('🔑 re-reads the runs on the run list’s refresh hint', async () => {
    render(<WorkflowsListPage sub="" navEpoch={0} query={{}} setQuery={() => {}} navigate={() => {}} />)
    expect(await screen.findByText('code-project')).toBeTruthy()

    workflowRuns.mockResolvedValue({ runs: [run('r2', 'nightly-digest'), run('r1', 'code-project', 'complete')], total: 2 })
    act(() => { frame?.({ type: 'refresh', data: { kinds: ['workflow_runs'] } }) })

    expect(await screen.findByText('nightly-digest')).toBeTruthy()
  })

  it('another listing’s hint reads nothing', async () => {
    render(<WorkflowsListPage sub="" navEpoch={0} query={{}} setQuery={() => {}} navigate={() => {}} />)
    await screen.findByText('code-project')
    const before = workflowRuns.mock.calls.length

    act(() => { frame?.({ type: 'refresh', data: { kinds: ['crons'] } }) })
    await new Promise((resolve) => setTimeout(resolve, 100))

    await waitFor(() => expect(workflowRuns.mock.calls.length).toBe(before))
  })
})
