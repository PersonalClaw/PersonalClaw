import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'

// ── The cockpit terminal's controller ───────────────────────────────────────────────────────────
//
// `useBottomTerminal` owns the workspace shell under the cockpit's editor: one PTY per open drawer,
// closed when the drawer goes (the one it is following, after a Restart), a failure said and retried,
// and a build or test command run on its own session.

const h = vi.hoisted(() => ({ createTerminal: vi.fn(), deleteTerminal: vi.fn(), runInTerminalWhenReady: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, createTerminal: h.createTerminal, deleteTerminal: h.deleteTerminal } }
})
vi.mock('../../terminal/terminalBridge', () => ({ runInTerminalWhenReady: h.runInTerminalWhenReady }))

let useBottomTerminal: typeof import('./useBottomTerminal')['useBottomTerminal']

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.createTerminal.mockResolvedValue({ session_id: 'pty-1', cwd: '/repo/feed', shell: 'zsh' })
  h.deleteTerminal.mockResolvedValue({})
  h.runInTerminalWhenReady.mockReturnValue(() => {})
  ;({ useBottomTerminal } = await import('./useBottomTerminal'))
})
afterEach(() => cleanup())

describe('useBottomTerminal', () => {
  it('opens a shell in the workspace, and closes it when the drawer goes', async () => {
    const { result, unmount } = renderHook(() => useBottomTerminal({ ws: '/repo/feed' }))
    await waitFor(() => expect(result.current.tab).toEqual({ id: 'pty-1', label: 'Terminal', cwd: '/repo/feed', shell: 'zsh' }))
    expect(h.createTerminal).toHaveBeenCalledWith('/repo/feed')
    unmount()
    expect(h.deleteTerminal).toHaveBeenCalledWith('pty-1')
  })

  it('closes the shell a Restart replaced it with, not the dead one', async () => {
    const { result, unmount } = renderHook(() => useBottomTerminal({ ws: '/repo/feed' }))
    await waitFor(() => expect(result.current.tab).not.toBeNull())
    result.current.liveSessionRef.current = 'pty-2'
    unmount()
    expect(h.deleteTerminal).toHaveBeenCalledWith('pty-2')
    expect(h.deleteTerminal).not.toHaveBeenCalledWith('pty-1')
  })

  it('closes a shell that arrives after the drawer went', async () => {
    let resolve: (r: { session_id: string; cwd: string; shell: string }) => void = () => {}
    h.createTerminal.mockReturnValue(new Promise((r) => { resolve = r }))
    const { unmount } = renderHook(() => useBottomTerminal({ ws: '/repo/feed' }))
    unmount()
    await act(async () => { resolve({ session_id: 'pty-late', cwd: '/repo/feed', shell: 'zsh' }) })
    expect(h.deleteTerminal).toHaveBeenCalledWith('pty-late')
  })

  it('says why a shell could not start, and tries again', async () => {
    h.createTerminal.mockRejectedValueOnce(new Error('The workspace folder is gone.'))
    const { result } = renderHook(() => useBottomTerminal({ ws: '/repo/feed' }))
    await waitFor(() => expect(result.current.err).toBe('The workspace folder is gone.'))
    act(() => result.current.setAttempt((n) => n + 1))
    await waitFor(() => expect(result.current.tab?.id).toBe('pty-1'))
    expect(result.current.err).toBeNull()
    expect(h.createTerminal).toHaveBeenCalledTimes(2)
  })

  it('runs a requested command on its own live session, once per request', async () => {
    const { result, rerender } = renderHook(({ runCmd }) => useBottomTerminal({ ws: '/repo/feed', runCmd }), {
      initialProps: { runCmd: { cmd: 'npm test', n: 1 } as { cmd: string; n: number } | null },
    })
    await waitFor(() => expect(h.runInTerminalWhenReady).toHaveBeenCalledTimes(1))
    const [cmd, target] = h.runInTerminalWhenReady.mock.calls[0]
    expect(cmd).toBe('npm test')
    result.current.liveSessionRef.current = 'pty-2'
    expect(target()).toBe('pty-2')
    rerender({ runCmd: { cmd: 'npm test', n: 1 } })
    expect(h.runInTerminalWhenReady).toHaveBeenCalledTimes(1)
    rerender({ runCmd: { cmd: 'npm test', n: 2 } })
    expect(h.runInTerminalWhenReady).toHaveBeenCalledTimes(2)
  })
})
