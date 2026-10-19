import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { CodeProject } from '../../../lib/api'

// ── The code cockpit's actions ────────────────────────────────────────────────────────────────
//
// `cockpitActions` derives what the cockpit shows about a project it has, and runs its actions:
// start, pause, resume and stop behind one guard, the build or test command in the terminal, and a
// delete only once asked. Each failure is said through the toast.

const h = vi.hoisted(() => ({ uLoopAction: vi.fn(), uLoopKeptWork: vi.fn(), deleteULoop: vi.fn(), confirmDelete: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, uLoopAction: h.uLoopAction, uLoopKeptWork: h.uLoopKeptWork, deleteULoop: h.deleteULoop } }
})
vi.mock('../../../ui/dialog', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../ui/dialog')>()
  return { ...real, confirmDelete: h.confirmDelete }
})

let cockpitActions: typeof import('./cockpitActions')['cockpitActions']
const project = (over: Record<string, unknown> = {}) => ({
  id: 'loop-1', name: 'Feed refetch fix', status: 'ready', project_kind: 'greenfield', workspace_dir: '/repo/feed', ...over,
} as unknown as CodeProject)
const deps = (over: Record<string, unknown> = {}) => ({
  id: 'loop-1', project: project(), onDeleted: vi.fn(), acting: false, setActing: vi.fn(), setProject: vi.fn(), setToast: vi.fn(),
  setTasksNonce: vi.fn(), load: vi.fn(), setPickWs: vi.fn(), setShowTerm: vi.fn(), setPendingRunCmd: vi.fn(),
  pendingRunNonce: { current: 0 }, runtimes: [], runtimesLoaded: true, ...over,
})

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.uLoopAction.mockResolvedValue({ id: 'loop-1', status: 'running', kind_config: {} })
  h.uLoopKeptWork.mockResolvedValue({ kept: [] })
  h.deleteULoop.mockResolvedValue({})
  ;({ cockpitActions } = await import('./cockpitActions'))
})

describe('cockpitActions', () => {
  it('derives the workspace, the file root and the missing commands', () => {
    const a = cockpitActions(deps({ project: project({ workspace_dir: '', files_dir: '/loop/files', command_runnability: {
      verify_command: { command: 'npm run build', runnable: false, binary: 'npm', reason: 'binary_not_on_path' },
    } }) }))
    expect(a.ws).toBe('')
    expect(a.fileRoot).toBe('/loop/files')
    expect(a.missingBuild).toMatchObject({ label: 'build', binary: 'npm' })
    expect(a.missingTest).toBeUndefined()
    expect(a.runtimeEditable).toBe(true)
  })

  it('opens the folder picker for a brownfield draft with no workspace instead of starting it', async () => {
    const d = deps({ project: project({ project_kind: 'brownfield', workspace_dir: '' }) })
    await cockpitActions(d).act('start')
    expect(d.setPickWs).toHaveBeenCalledWith(true)
    expect(h.uLoopAction).not.toHaveBeenCalled()
  })

  it('runs an action once, takes its answer and re-reads the tasks, or says why it failed', async () => {
    const d = deps()
    await cockpitActions(d).act('start')
    expect(h.uLoopAction).toHaveBeenCalledWith('loop-1', 'start')
    expect(d.setProject.mock.calls[0][0]).toMatchObject({ status: 'running' })
    expect(d.setToast).toHaveBeenCalledWith(null)
    expect(d.setTasksNonce).toHaveBeenCalledTimes(1)
    expect(d.setActing.mock.calls).toEqual([[true], [false]])
    h.uLoopAction.mockRejectedValueOnce(new Error('the workspace is gone'))
    const e = deps()
    await cockpitActions(e).act('resume')
    expect(e.setToast).toHaveBeenCalledWith({ kind: 'error', text: "Couldn't resume this project: the workspace is gone" })
    expect(e.load).toHaveBeenCalledTimes(1)
    await cockpitActions(deps({ acting: true })).act('stop')
    expect(h.uLoopAction).toHaveBeenCalledTimes(2)
  })

  it('runs a command in the terminal, once per click', () => {
    const d = deps()
    const a = cockpitActions(d)
    a.runInWorkspaceTerminal(' npm test ')
    a.runInWorkspaceTerminal('npm test')
    a.runInWorkspaceTerminal('   ')
    expect(d.setShowTerm).toHaveBeenCalledTimes(2)
    expect(d.setPendingRunCmd.mock.calls).toEqual([[{ cmd: 'npm test', n: 0 }], [{ cmd: 'npm test', n: 1 }]])
  })

  it('deletes only once asked, naming what the run kept, and stays put if the delete fails', async () => {
    h.confirmDelete.mockResolvedValueOnce(false)
    const d = deps()
    await cockpitActions(d).del()
    expect(h.uLoopKeptWork).toHaveBeenCalledWith('loop-1')
    expect(h.deleteULoop).not.toHaveBeenCalled()
    h.confirmDelete.mockResolvedValueOnce(true)
    await cockpitActions(d).del()
    expect(h.deleteULoop).toHaveBeenCalledWith('loop-1')
    expect(d.onDeleted).toHaveBeenCalledTimes(1)
    h.confirmDelete.mockResolvedValueOnce(true)
    h.deleteULoop.mockRejectedValueOnce(new Error('teardown failed'))
    const e = deps()
    await cockpitActions(e).del()
    expect(e.onDeleted).not.toHaveBeenCalled()
    expect(e.setToast).toHaveBeenCalledWith({ kind: 'error', text: "Couldn't delete this project: teardown failed" })
  })
})
