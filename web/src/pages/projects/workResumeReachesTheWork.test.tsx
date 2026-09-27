/** The Work board's Resume resumes the work and opens it (F-30).
 *
 *  It navigated to `#/loop/<id>` for every row. `#/loop` is the loop COMPOSER, which ignores the
 *  id, so Resume on a suspended run or a paused loop opened an empty "new loop" form and resumed
 *  nothing. A row now says what it is (`source`, and a loop's `kind`); Resume performs the SAME
 *  resume the item's own page offers, then opens that page.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api, type ProjectItem, type WorkBoard, type WorkRow } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { ProjectsSection } from './ProjectsSection'

const PROJECT_ID = 'p-work'

function row(over: Partial<WorkRow>): WorkRow {
  return {
    run_id: 'x', title: 'x', state: 'suspended', source: 'run', kind: '', origin: 'manual', project_id: PROJECT_ID,
    claim: null, collapsed: false, attention: false, resumable: true, outcome: '', ...over,
  }
}

const BOARD: WorkBoard = {
  board: [{
    state: 'suspended', count: 3, attention: 0, rows: [
      row({ run_id: 'run-42', title: 'Nightly digest', source: 'run' }),
      row({ run_id: 'loop-7', title: 'Refactor the parser', source: 'loop', kind: 'code' }),
      row({ run_id: 'loop-9', title: 'Grow the newsletter', source: 'loop', kind: 'goal' }),
    ],
  }],
  sections: [{ name: 'runs', items: [], status: 'ok', error: '', loadedAt: 0 }],
  completeness: 'complete', attention: 0, loadedAt: 0,
}

const navigate = vi.fn()

function mount() {
  const project: ProjectItem = { id: PROJECT_ID, name: 'Work', status: 'active', brief: '', context_dir: '/tmp/ctx' }
  vi.spyOn(api, 'project').mockResolvedValue(project)
  vi.spyOn(api, 'taskLists').mockResolvedValue([])
  vi.spyOn(api, 'projectWork').mockResolvedValue(BOARD)
  vi.spyOn(api, 'personalclawConfig').mockResolvedValue({ legibility: { context_adapters: false } } as Awaited<ReturnType<typeof api.personalclawConfig>>)
  vi.spyOn(api, 'projectSettings').mockResolvedValue({ default_project_id: '' })
  render(<ProjectsSection sub={PROJECT_ID} navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)
}

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { vi.restoreAllMocks(); resetDataStore() })

async function resumeRow(title: string) {
  const label = await screen.findByText(title)
  const card = label.closest('div')!
  await userEvent.click(within(card).getByRole('button', { name: /Resume/ }))
}

describe('Resume on the Work board', () => {
  it('🔑 a suspended run is resumed through the run API and opens on its run page', async () => {
    const resume = vi.spyOn(api, 'resumeWorkflowRun').mockResolvedValue({ resumed: true })
    mount()
    await resumeRow('Nightly digest')
    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(resume).toHaveBeenCalledWith('run-42', {})
    expect(navigate).toHaveBeenCalledWith('workflows/runs/run-42')
    expect(navigate).not.toHaveBeenCalledWith(expect.stringMatching(/^loop\//))
  })

  it('🔑 a paused code loop is resumed through the loop API and opens in Code', async () => {
    const action = vi.spyOn(api, 'uLoopAction').mockResolvedValue({} as Awaited<ReturnType<typeof api.uLoopAction>>)
    mount()
    await resumeRow('Refactor the parser')
    await waitFor(() => expect(navigate).toHaveBeenCalled())
    expect(action).toHaveBeenCalledWith('loop-7', 'resume')
    expect(navigate).toHaveBeenCalledWith('code/loop-7')
  })

  it('a paused loop of another kind opens in the loop cockpit', async () => {
    vi.spyOn(api, 'uLoopAction').mockResolvedValue({} as Awaited<ReturnType<typeof api.uLoopAction>>)
    mount()
    await resumeRow('Grow the newsletter')
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('loops/loop-9'))
  })

  it('a resume that fails still opens the work, so the reason is one look away', async () => {
    vi.spyOn(api, 'resumeWorkflowRun').mockRejectedValue(new Error('the run is not paused'))
    mount()
    await resumeRow('Nightly digest')
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('workflows/runs/run-42'))
  })
})
