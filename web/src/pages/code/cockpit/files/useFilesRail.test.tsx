import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'

// ── The cockpit files rail's controller ───────────────────────────────────────────────────────
//
// `useFilesRail` keeps which of the rail's tabs is up, the Changes count shown on the Files tab (read
// only while that tab is up, polled while the worker runs and re-read on a save), and ⌘P landing on
// the Files tab so the finder is there to focus.

const h = vi.hoisted(() => ({ useGitStatus: vi.fn(), useVisiblePoll: vi.fn() }))
vi.mock('../../../files/filesData', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../files/filesData')>()
  return { ...real, useGitStatus: h.useGitStatus }
})
vi.mock('../../../../lib/useVisiblePoll', () => ({ useVisiblePoll: h.useVisiblePoll }))

let useFilesRail: typeof import('./useFilesRail')['useFilesRail']
const WS = '/repo/feed'
const lastGit = () => h.useGitStatus.mock.calls.at(-1) as [string | null, number]
const lastPoll = () => h.useVisiblePoll.mock.calls.at(-1) as [() => void, number | null]

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.useGitStatus.mockReturnValue({ statuses: { 'src/fetch.py': 'M', 'src/etag.py': 'A' } })
  ;({ useFilesRail } = await import('./useFilesRail'))
})
afterEach(() => { cleanup(); vi.useRealTimers() })

describe('useFilesRail', () => {
  it('starts on Files and counts the workspace changes for the Changes tab', () => {
    const { result } = renderHook(() => useFilesRail({ ws: WS, isProjectDir: false, running: false }))
    expect(result.current.tab).toBe('files')
    expect(result.current.changeCount).toBe(2)
    expect(lastGit()).toEqual([WS, 0])
  })

  it('reads no status of its own on the Changes tab, or for a project with no workspace', () => {
    const { result } = renderHook(() => useFilesRail({ ws: WS, isProjectDir: false, running: true }))
    act(() => result.current.setTab('changes'))
    expect(lastGit()[0]).toBeNull()
    expect(lastPoll()[1]).toBeNull()
    renderHook(() => useFilesRail({ ws: WS, isProjectDir: true, running: true }))
    expect(lastGit()[0]).toBeNull()
  })

  it('polls the count while the worker runs, and re-reads it on a save', () => {
    const { result, rerender } = renderHook(({ running }) => useFilesRail({ ws: WS, isProjectDir: false, running }), { initialProps: { running: false } })
    expect(lastPoll()[1]).toBeNull()
    rerender({ running: true })
    expect(lastPoll()[1]).toBe(8000)
    act(() => lastPoll()[0]())
    expect(lastGit()).toEqual([WS, 1])
    act(() => { window.dispatchEvent(new CustomEvent('ne:code-file-saved')) })
    expect(lastGit()).toEqual([WS, 2])
    expect(result.current.tab).toBe('files')
  })

  it('lands ⌘P on the Files tab and passes it on to the finder once there', () => {
    vi.useFakeTimers()
    const { result } = renderHook(() => useFilesRail({ ws: WS, isProjectDir: false, running: false }))
    act(() => result.current.setTab('changes'))
    const seen = vi.fn()
    window.addEventListener('ne:code-focus-find', seen)
    act(() => { window.dispatchEvent(new CustomEvent('ne:code-focus-find')) })
    expect(result.current.tab).toBe('files')
    expect(seen).toHaveBeenCalledTimes(1)
    act(() => { vi.runAllTimers() })
    expect(seen).toHaveBeenCalledTimes(2)
    act(() => { vi.runAllTimers() })
    window.removeEventListener('ne:code-focus-find', seen)
    expect(seen, 'already on Files, so it is not passed on again').toHaveBeenCalledTimes(2)
  })
})
