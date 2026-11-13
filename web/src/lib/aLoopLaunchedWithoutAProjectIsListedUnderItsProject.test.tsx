import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { runState } from '../test/runState'

// ── A loop launched into no project is listed under the project made for it ─────────────────────
//
// A code session launched with no project gets one made for its tasks (`tasks_project_id`), and its
// `project_id` stays empty. The thread read filed it by `project_id` alone, so the Projects page's
// Chats by project view listed it under "No project" while its project's page listed it as that
// project's. The read now files every loop by the gateway's one rule (`loopProjectId`): the project
// it was launched into when the project list knows it, else the one made for its tasks, else none.

const wire = { projects: [{ id: 'p-garden', name: 'Garden' }, { id: 'p-homelab', name: 'Homelab' }] }

function mockDeps() {
  vi.doMock('./useChatSocket', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    useChatSocket: () => {},
  }))
  vi.doMock('./api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      chatSessions: () => Promise.resolve([
        { key: 'chat-1', title: 'Plan the garden', messages: 2, run_state: runState({ session: 'chat-1' }), project_id: 'p-garden' },
      ]),
      uLoops: () => Promise.resolve([
        { id: 'e5f6a7b8', kind: 'code', name: 'Fence gate', task: 'fix the gate', status: 'running', total_cycles: 2,
          max_cycles: 10, started_at: 1_000, project_id: '', tasks_project_id: 'p-garden' },
        { id: 'a3b4c5d6', kind: 'goal', name: 'Rack the switch', task: 'rack the switch', status: 'running', total_cycles: 1,
          max_cycles: 10, started_at: 1_000, project_id: 'p-homelab', tasks_project_id: 'p-garden' },
        { id: 'c9d0e1f2', kind: 'goal', name: 'Old idea', task: 'old idea', status: 'running', total_cycles: 0,
          max_cycles: 10, started_at: 1_000, project_id: 'p-gone', tasks_project_id: 'p-garden' },
      ]),
      inboxOpen: () => Promise.resolve([]),
      approvals: () => Promise.resolve([]),
      projects: () => Promise.resolve(wire.projects),
    },
  }))
}

async function mountProbe() {
  const { useThreads } = await import('./useThreads')
  function Probe() {
    const { groups } = useThreads()
    return (
      <ul data-testid="threads">
        {groups.flatMap((g) => g.threads.map((t) => <li key={t.key}>{`${g.projectId || 'no project'}|${t.key}`}</li>))}
      </ul>
    )
  }
  render(<Probe />)
}

const shown = () => [...screen.getByTestId('threads').querySelectorAll('li')].map((li) => li.textContent).sort()

beforeEach(() => {
  vi.resetModules()
  mockDeps()
})

describe('the thread read files a loop under its one project', () => {
  it('a code session whose project was made for it is listed under it, not under "No project"', async () => {
    await mountProbe()
    await waitFor(() => expect(shown()).toEqual([
      'p-garden|chat:chat-1',
      'p-garden|loop:c9d0e1f2',
      'p-garden|loop:e5f6a7b8',
      'p-homelab|loop:a3b4c5d6',
    ]))
  })
})
