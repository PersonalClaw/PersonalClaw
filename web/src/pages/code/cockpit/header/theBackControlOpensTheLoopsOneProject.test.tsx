import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { CodeProject } from '../../../../lib/api'

// ── The code cockpit's back control opens the one project the loop is in ────────────────────────
//
// It read `tasks_project_id || project_id`, the two the other way round from the gateway's rule
// (`loopProjectId`): a session launched into Garden whose tasks were made in another project went
// back to the other one. Now it opens the project the loop was launched into while the list knows
// it, else the one made for its tasks.

vi.mock('../../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../../lib/api')>()
  return {
    ...real,
    api: { ...real.api, projects: () => Promise.resolve([{ id: 'p-0', name: 'Garden' }, { id: 'p-1', name: 'Fence gate' }]) },
  }
})
vi.mock('../files/useWorkspaceChanges', () => ({ useWorkspaceChanges: () => ({ statuses: {} }) }))
vi.mock('./useCommit', () => ({ useCommit: () => async () => {} }))

const { CockpitHeader } = await import('./CockpitHeader')

function mount(over: Partial<CodeProject>, onOpenProject: (id: string) => void) {
  const project = { id: 'loop-1', name: 'Fence gate', kind: 'code', status: 'paused', ...over } as unknown as CodeProject
  render(
    <CockpitHeader id="loop-1" project={project} setProject={() => {}} setToast={() => {}} onOpenProject={onOpenProject}
      active={false} connected acting={false} act={async () => {}} ws="" showTerm={false} setShowTerm={() => {}}
      runInWorkspaceTerminal={() => {}} onBack={() => {}} del={async () => {}} />,
  )
}

afterEach(() => { cleanup() })

describe("the code cockpit's back control", () => {
  it('opens the project the loop was launched into while the list knows it', async () => {
    const onOpenProject = vi.fn()
    mount({ project_id: 'p-0', tasks_project_id: 'p-1' }, onOpenProject)
    await userEvent.click(await screen.findByRole('button', { name: 'Back to the project this loop belongs to' }))
    expect(onOpenProject).toHaveBeenCalledWith('p-0')
  })

  it('opens the project made for its tasks when the one it was launched into is gone', async () => {
    const onOpenProject = vi.fn()
    mount({ project_id: 'p-gone', tasks_project_id: 'p-1' }, onOpenProject)
    await userEvent.click(await screen.findByRole('button', { name: 'Back to the project this loop belongs to' }))
    expect(onOpenProject).toHaveBeenCalledWith('p-1')
  })
})
