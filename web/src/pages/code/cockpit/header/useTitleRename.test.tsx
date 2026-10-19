import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import type { CodeProject } from '../../../../lib/api'

// ── The cockpit title's controller ────────────────────────────────────────────────────────────
//
// `useTitleRename` renames the project from its title: Escape cancels without a save, the Enter and
// the blur that follows it save once, an unchanged name saves nothing, and a failed rename is said.

const h = vi.hoisted(() => ({ updateULoop: vi.fn() }))
vi.mock('../../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../../lib/api')>()
  return { ...real, api: { ...real.api, updateULoop: h.updateULoop } }
})

let useTitleRename: typeof import('./useTitleRename')['useTitleRename']
const PROJECT = { id: 'loop-1', name: 'Feed refetch fix', status: 'running' } as unknown as CodeProject
const mount = () => {
  const setProject = vi.fn()
  const setToast = vi.fn()
  const r = renderHook(() => useTitleRename({ id: 'loop-1', project: PROJECT, setProject, setToast }))
  return { ...r, setProject, setToast }
}

beforeEach(async () => {
  h.updateULoop.mockReset()
  h.updateULoop.mockResolvedValue({ id: 'loop-1', name: 'Honour ETags', kind_config: {} })
  ;({ useTitleRename } = await import('./useTitleRename'))
})
afterEach(() => cleanup())

describe('useTitleRename', () => {
  it('starts from the current name and saves a new one once, for the Enter and the blur after it', async () => {
    const { result, setProject } = mount()
    act(() => result.current.startRename())
    expect(result.current.editingTitle).toBe(true)
    expect(result.current.titleDraft).toBe('Feed refetch fix')
    act(() => result.current.setTitleDraft('  Honour ETags '))
    await act(async () => { await Promise.all([result.current.commitRename(), result.current.commitRename()]) })
    expect(h.updateULoop).toHaveBeenCalledTimes(1)
    expect(h.updateULoop).toHaveBeenCalledWith('loop-1', { name: 'Honour ETags' })
    expect(setProject.mock.calls[0][0]).toMatchObject({ name: 'Honour ETags' })
    expect(result.current.editingTitle).toBe(false)
  })

  it('cancels on Escape without a save, and saves nothing for an unchanged or blank name', async () => {
    const { result } = mount()
    act(() => result.current.startRename())
    act(() => result.current.setTitleDraft('Something else'))
    act(() => result.current.abortRename())
    await act(() => result.current.commitRename())
    act(() => result.current.startRename())
    await act(() => result.current.commitRename())
    act(() => result.current.startRename())
    act(() => result.current.setTitleDraft('   '))
    await act(() => result.current.commitRename())
    expect(h.updateULoop).not.toHaveBeenCalled()
  })

  it('says a rename failed', async () => {
    h.updateULoop.mockRejectedValueOnce(new Error('the name is taken'))
    const { result, setToast, setProject } = mount()
    act(() => result.current.startRename())
    act(() => result.current.setTitleDraft('Honour ETags'))
    await act(() => result.current.commitRename())
    expect(setToast).toHaveBeenCalledWith({ kind: 'error', text: "Couldn't rename: the name is taken" })
    expect(setProject).not.toHaveBeenCalled()
  })
})
