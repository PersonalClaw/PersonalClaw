/** "The workspace folder no longer exists" is decided by browse-dirs' 404 STATUS, never its text.
 *
 *  The code cockpit decided it with `message.toLowerCase().includes('no such directory')`. That is
 *  the pattern `hasApiCode` exists to retire: the message is human copy, so a rewording of the 404
 *  would have silenced the warning, and any other failure that happened to carry those words — a
 *  403 whose sentence quoted a path, a proxy's error page — would have raised it about a folder
 *  that exists. Both surfaces that ask now go through this one hook: the project page (which asked
 *  nothing at all and kept showing a dead path) and the cockpit.
 *
 *  The two discriminating arms are the reworded 404 (must warn) and a non-404 whose text says "no
 *  such directory" (must NOT warn): a text matcher gets both wrong, a status check gets both right.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { api, ApiError } from './api'
import { isWorkspaceGone, useWorkspaceMissing } from './useWorkspaceMissing'

const DEAD = '/home/personalclaw/garden-planner'

afterEach(() => { vi.restoreAllMocks() })

/** Mount the hook against a browse-dirs that answers with `outcome`, and let the probe settle. */
async function probe(outcome: () => Promise<never> | Promise<unknown>, path: string | undefined = DEAD) {
  const browseDirs = vi.spyOn(api, 'browseDirs').mockImplementation(outcome as () => ReturnType<typeof api.browseDirs>)
  const hook = renderHook(() => useWorkspaceMissing(path))
  if (path) await waitFor(() => expect(browseDirs).toHaveBeenCalledWith(path))
  // One more turn so the rejection's handler has run before the assertion reads the state.
  await new Promise((resolve) => setTimeout(resolve, 0))
  return { hook, browseDirs }
}

describe('useWorkspaceMissing', () => {
  it('a 404 means the folder is gone', async () => {
    const { hook } = await probe(() => Promise.reject(new ApiError('No such directory', 404)))
    await waitFor(() => expect(hook.result.current).toBe(true))
  })

  it('🔑 a REWORDED 404 still means gone — the status is the fact, the sentence is copy', async () => {
    const { hook } = await probe(() => Promise.reject(new ApiError('That folder is not there any more', 404)))
    await waitFor(() => expect(hook.result.current).toBe(true))
  })

  it('🔑 a non-404 that SAYS "no such directory" is not a missing folder', async () => {
    const { hook } = await probe(() => Promise.reject(new ApiError('No such directory — permission denied', 403)))
    expect(hook.result.current).toBe(false)
  })

  it.each([
    ['a protected location (403)', new ApiError("Can't open /data — it's a protected system location.", 403)],
    ['a permission failure (400)', new ApiError("Can't access that directory (permission denied)", 400)],
    ['a server error (500)', new ApiError('Internal Server Error', 500)],
    ['a network failure', new TypeError('Failed to fetch')],
  ])('%s does not claim the folder is gone', async (_label, error) => {
    const { hook } = await probe(() => Promise.reject(error))
    expect(hook.result.current).toBe(false)
  })

  it('a folder that is there reads as present', async () => {
    const { hook } = await probe(() => Promise.resolve({ path: DEAD, parent: '/home/personalclaw', dirs: [] }))
    expect(hook.result.current).toBe(false)
  })

  it('no path, or a disabled probe, asks the disk nothing', async () => {
    const browseDirs = vi.spyOn(api, 'browseDirs')
    const empty = renderHook(() => useWorkspaceMissing(''))
    const off = renderHook(() => useWorkspaceMissing(DEAD, { enabled: false }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(browseDirs).not.toHaveBeenCalled()
    expect(empty.result.current).toBe(false)
    expect(off.result.current).toBe(false)
  })

  it('isWorkspaceGone is the predicate both surfaces share', () => {
    expect(isWorkspaceGone(new ApiError('x', 404))).toBe(true)
    expect(isWorkspaceGone(new ApiError('No such directory', 403))).toBe(false)
    expect(isWorkspaceGone(new Error('No such directory'))).toBe(false)
  })
})
