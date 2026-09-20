import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, renderHook, screen, waitFor } from '@testing-library/react'
import { ApiError } from '../../../lib/api'
import { useDirCache } from '../filesData'
import { FileTree } from './FileTree'
import type { FsEntry } from '../../../lib/api'

/** #298 — a refused or missing path must not render as an empty folder.
 *
 *  The backend already computes the distinction (400 = outside the browsable roots,
 *  403 = not allowed, 404 = no such path) and `useDirCache`'s loader used to throw it
 *  away with a bare `catch { return [] }`, so "you are not allowed here", "that path
 *  does not exist" and "this folder is empty" were the SAME screen. The go-to-path box
 *  leaves the rejected path in the URL, so the empty tree read as a real location.
 *
 *  Two halves, one per file: the loader must KEEP the reason, and the tree must SHOW it
 *  instead of `emptyLabel`. */

const fileList = vi.fn()
vi.mock('../../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, fileList: (p: string) => fileList(p) } }
})

const ROOT = '/ws'
const empty: FsEntry[] = []

beforeEach(() => {
  fileList.mockReset()
  sessionStorage.clear()
})

function tree(over: Partial<Parameters<typeof FileTree>[0]> = {}) {
  return render(
    <FileTree
      dirs={{ cache: { [ROOT]: empty }, errors: {}, load: vi.fn(async () => empty), invalidate: vi.fn(), invalidateSubtree: vi.fn() } as unknown as Parameters<typeof FileTree>[0]['dirs']}
      rootPath={ROOT} activePath={null} gitStatuses={{}} onOpenFile={vi.fn()}
      artifactPaths={new Set()} onRename={vi.fn()} onDelete={vi.fn()} onUpload={vi.fn()}
      {...over} />,
  )
}

describe('the directory loader keeps WHY a listing failed', () => {
  // Each status gets its own sentence — the whole point is that three different
  // failures stop rendering identically.
  it.each([
    [403, /not allowed/i],
    [404, /does not exist/i],
    [400, /outside the folders/i],
  ])('a %i records its own reason', async (status, expected) => {
    fileList.mockRejectedValue(new ApiError('refused', status as number))
    const { result } = renderHook(() => useDirCache())
    await result.current.load('/etc')
    await waitFor(() => expect(result.current.errors['/etc']).toMatch(expected as RegExp))
    // Still resolves to an array — every caller indexes the result.
    expect(result.current.cache['/etc']).toBeUndefined()
  })

  it('a 200 with zero entries records NO error — a real empty folder is not a failure', async () => {
    fileList.mockResolvedValue({ entries: [] })
    const { result } = renderHook(() => useDirCache())
    await result.current.load('/ws/outbox')
    await waitFor(() => expect(result.current.cache['/ws/outbox']).toEqual([]))
    expect(result.current.errors['/ws/outbox']).toBeUndefined()
  })

  it('a path that lists after failing clears its stale refusal', async () => {
    fileList.mockRejectedValueOnce(new ApiError('refused', 403))
    const { result } = renderHook(() => useDirCache())
    await result.current.load('/ws/later')
    await waitFor(() => expect(result.current.errors['/ws/later']).toMatch(/not allowed/i))
    fileList.mockResolvedValue({ entries: [] })
    await result.current.load('/ws/later', true)
    await waitFor(() => expect(result.current.errors['/ws/later']).toBeUndefined())
  })
})

describe('the tree distinguishes a refusal from an empty folder', () => {
  it('shows the refusal, not the empty label', async () => {
    tree({
      dirs: { cache: { [ROOT]: empty }, errors: { [ROOT]: 'You are not allowed to browse that location.' }, load: vi.fn(async () => empty), invalidate: vi.fn(), invalidateSubtree: vi.fn() } as unknown as Parameters<typeof FileTree>[0]['dirs'],
      emptyLabel: 'Empty',
    })
    expect(await screen.findByText(/not allowed to browse/i)).toBeTruthy()
    // The regression this pins: BOTH used to render "Empty".
    expect(screen.queryByText('Empty')).toBeNull()
  })

  it('an allowed-but-empty directory still says Empty — the control', async () => {
    tree({ emptyLabel: 'Empty' })
    expect(await screen.findByText('Empty')).toBeTruthy()
  })

  it('the refusal is announced, not just coloured', async () => {
    tree({
      dirs: { cache: { [ROOT]: empty }, errors: { [ROOT]: 'That path does not exist.' }, load: vi.fn(async () => empty), invalidate: vi.fn(), invalidateSubtree: vi.fn() } as unknown as Parameters<typeof FileTree>[0]['dirs'],
    })
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/does not exist/i)
  })
})
