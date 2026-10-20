import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── An upload the gateway is finishing offers no cancel ──────────────────────────────────────
//
// Once every byte of a file is in, the gateway completes it into the folder whatever the page does
// (`lib/chunkedUpload`): a cancel then would be a button that stops nothing, and the file would land
// in the workspace after the row had gone quietly. So the row's "Cancel upload" is there while the
// file is sending, and gone once it is finishing.

const h = vi.hoisted(() => ({ fileUpload: vi.fn() }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const base: Record<string, unknown> = { fileUpload: h.fileUpload, artifacts: () => Promise.resolve([]) }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  return { ...real, api }
})
vi.mock('../../lib/chunkedUpload', async (orig) => ({
  ...(await orig<typeof import('../../lib/chunkedUpload')>()),
  precheck: () => Promise.resolve(null),
}))
const ROOT = '/home/user/workspace'
vi.mock('./filesData', () => ({
  useFileRoots: () => ({ roots: [{ path: ROOT, label: 'Workspace' }], loading: false, error: null, refresh: () => {} }),
  useDirCache: () => ({ cache: {}, errors: {}, load: async () => [], invalidate: () => {}, invalidateSubtree: () => {} }),
  useGitStatus: () => ({ branch: '', statuses: {} }),
}))
// The tree's own drop and picker hand files to the section exactly like this.
vi.mock('./browse/FileTree', () => ({
  FileTree: ({ rootPath, onUpload }: { rootPath: string; onUpload: (entry: { path: string }, files: File[]) => void }) => (
    <button type="button" onClick={() => onUpload({ path: rootPath }, [new File(['0123456789ab'], 'walkthrough.mov', { type: 'video/quicktime' })])}>
      Upload here
    </button>
  ),
}))

const { FilesSection } = await import('./FilesSection')

afterEach(() => { h.fileUpload.mockReset() })

describe('a file uploading into the workspace', () => {
  it('offers "Cancel upload" while it sends, and none once the gateway is finishing it', async () => {
    h.fileUpload.mockImplementation(() => new Promise(() => {}))
    render(<FilesSection sub="" navigate={() => {}} query={{}} setQuery={() => {}} navEpoch={0} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Upload here' }))
    await waitFor(() => expect(h.fileUpload).toHaveBeenCalledTimes(1))
    const [dir, , onProgress] = h.fileUpload.mock.calls[0] as [string, File[], (i: number, p: object) => void]
    expect(dir).toBe(ROOT)

    act(() => { onProgress(0, { loaded: 8, total: 12, pct: 67 }) })
    expect(screen.getByRole('button', { name: 'Cancel upload' })).toBeInTheDocument()

    act(() => { onProgress(0, { loaded: 12, total: 12, pct: 100, finishing: true }) })
    expect(screen.queryByRole('button', { name: 'Cancel upload' })).toBeNull()
    expect(screen.getByRole('progressbar', { name: 'Uploading walkthrough.mov' })).toBeInTheDocument()
  })
})
