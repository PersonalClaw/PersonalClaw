// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { chatRunMark } from './chatRunMark'

// ── A chat's own run says whose it is on your Runs list ────────────────────────────────────────
//
// A batch is a chat's subagents, and every run a Temporary or Incognito chat starts is that chat's
// own: only that chat's agent and you read it, and a private chat's goes when the chat does
// (`workflows/chat_runs.py`, `workflows/private_runs.py`). The list showed such a run as any other,
// under its machine name, with nothing to say it was a chat's or that it would go with the chat.

const workflowRuns = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    workflowDefs: () => Promise.resolve({ defs: [], total: 0 }),
    workflowRuns: (...a: unknown[]) => workflowRuns(...a),
    workflowSurfacing: () => Promise.resolve({ defs: [], total: 0, findings: [] }),
  },
}))

vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

import { WorkflowsListPage } from './WorkflowsListPage'

const run = (id: string, name: string, chat: unknown = null) => ({
  id, workflow_name: name, status: 'complete', spec_version: 1, created_at: '2026-10-04T09:00:00Z', chat,
})

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  workflowRuns.mockResolvedValue({
    runs: [
      run('a1', 'subagent-batch-1', { session: 'dashboard:chat-t', mode: 'temporary', batch: true }),
      run('a2', 'two-steps', { session: 'dashboard:chat-i', mode: 'incognito', batch: false }),
      run('a3', 'subagent-batch-2', { session: 'dashboard:chat-o', mode: '', batch: true }),
      run('a4', 'nightly-digest'),
    ],
    total: 4,
  })
})

describe('your Runs list', () => {
  it("🔑 marks each chat's own run as that chat's, and says what that means", async () => {
    render(<WorkflowsListPage sub="" navEpoch={0} query={{}} setQuery={() => {}} navigate={() => {}} />)

    const temporary = await screen.findByText("Temporary chat's batch")
    expect(temporary.getAttribute('title')).toBe("Only that chat's agent and you see this batch. It is deleted when the chat ends.")
    const incognito = screen.getByText("Incognito chat's run")
    expect(incognito.getAttribute('title')).toBe("Only that chat's agent and you see this run. It is deleted when you delete the chat.")
    expect(screen.getByText("A chat's batch").getAttribute('title')).toBe(
      "Only that chat's agent and you see this batch: its tasks are that chat's subagents.",
    )
  })

  it('marks no run of yours', async () => {
    render(<WorkflowsListPage sub="" navEpoch={0} query={{}} setQuery={() => {}} navigate={() => {}} />)
    const yours = (await screen.findByRole('button', { name: 'nightly-digest — run a4' })).parentElement
    expect(yours?.textContent ?? '').toContain('nightly-digest')
    expect(yours?.textContent ?? '').not.toMatch(/chat's/)
  })
})

describe('chatRunMark', () => {
  it('says nothing for a run of yours', () => {
    expect(chatRunMark(null)).toBeNull()
    expect(chatRunMark(undefined)).toBeNull()
  })

  it('names a chat whose memory setting nothing could say as a chat, and says so', () => {
    expect(chatRunMark({ session: 'dashboard:chat-u', mode: 'unreadable', batch: false })).toEqual({
      label: "A chat's run",
      hint: "Only that chat's agent and you see this run: that chat's memory setting could not be read.",
    })
  })
})
