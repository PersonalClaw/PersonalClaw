/** Up next's Resume resumes the work and opens it.
 *
 *  It navigated to `#/loop/<id>` for every row. `#/loop` is the loop COMPOSER, which ignores the
 *  id, so Resume on a suspended run or a paused loop opened an empty "new loop" form and resumed
 *  nothing. A row says what it is (`source`, and a loop's `kind`); Resume performs the SAME resume
 *  the item's own page offers, then opens that page. (The old work board's Resume moved to a
 *  project page's Up next, PROJ PR 4.)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { board, mountPage, navigate, workRow } from '../../test/projectPageHarness'

const BOARD = board([
  workRow('run-42', 'Nightly digest', { state: 'suspended', source: 'run', kind: '' }),
  workRow('loop-7', 'Refactor the parser', { state: 'suspended', source: 'loop', kind: 'code' }),
  workRow('loop-9', 'Grow the newsletter', { state: 'suspended', source: 'loop', kind: 'goal' }),
], [{ name: 'runs', items: [], status: 'ok', error: '', loadedAt: 0 }])

const mount = () => mountPage({ board: BOARD })

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

async function resumeRow(title: string) {
  await userEvent.click(await screen.findByRole('button', { name: `Resume: ${title}` }))
}

describe("Resume in a project's Up next", () => {
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
