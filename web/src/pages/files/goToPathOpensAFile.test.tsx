/**
 * "Go to path" goes to what the path names: a folder lists, a file opens, and a path typed relative
 * to the folder the box shows is read against that folder.
 *
 * Measured on Files › Explorer: the file's own path, which opened from the tree, read "That path
 * does not exist." (the listing answered a 404 for a file, and the page read every 404 one way), and
 * the same file typed workspace-relative over "/data/workspace" read "That path is outside the
 * folders PersonalClaw can browse." (the raw relative text went to the gateway, which has no base
 * for it). The box now resolves a relative path first, and learns whether the path is a file or a
 * folder from its folder's entries, so going somewhere that exists raises no error at all.
 *
 * Driven through the real page with the api mocked at its module boundary.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'

const ROOT = '/data/workspace'
const FOLDER = `${ROOT}/memory/instructions`
const FILE = `${FOLDER}/NOTES.md`

const mocks = vi.hoisted(() => ({ fileList: vi.fn(), fileRead: vi.fn(), fileComplete: vi.fn() }))

class FakeEventSource {
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  close(): void {}
}
vi.stubGlobal('EventSource', FakeEventSource)

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      fileRoots: async () => ({
        roots: [{ label: 'Workspace', path: ROOT, name: 'Workspace', is_dir: true }],
        entries: [], path: '',
      }),
      fileList: mocks.fileList,
      fileComplete: mocks.fileComplete,
      fileGitStatus: async () => ({ repoRoot: '', branch: '', statuses: {} }),
      artifacts: async () => [],
      fileRead: mocks.fileRead,
    },
  }
})

const { ApiError } = await import('../../lib/api')
const { FilesSection } = await import('./FilesSection')
const { registerBuiltinContentTypes } = await import('../../ui/content/registerBuiltins')
registerBuiltinContentTypes()

/** The tree the gateway would read, folder → its entries. */
const TREE: Record<string, { name: string; is_dir: boolean }[]> = {
  [ROOT]: [{ name: 'memory', is_dir: true }],
  [`${ROOT}/memory`]: [{ name: 'instructions', is_dir: true }],
  [FOLDER]: [{ name: 'NOTES.md', is_dir: false }],
}
const entriesOf = (folder: string) =>
  (TREE[folder] ?? []).map((e) => ({ ...e, path: `${folder}/${e.name}`, size: 1, mtime: 1 }))

/** The listing as the gateway answers it: a folder lists, a file is named as one, the rest is not there. */
function listing(path: string) {
  if (TREE[path]) return Promise.resolve({ roots: [], entries: entriesOf(path), path })
  if (path === FILE) return Promise.reject(new ApiError('That path is a file, not a folder.', 404, 'not_a_directory'))
  return Promise.reject(new ApiError('That path does not exist.', 404, 'not_found'))
}

/** The autocomplete read: the entries of the path's folder whose names start with its last part. */
function completion(path: string) {
  const cut = path.lastIndexOf('/')
  const folder = path.slice(0, cut)
  const prefix = path.slice(cut + 1)
  return Promise.resolve({ suggestions: entriesOf(folder).filter((e) => e.name.startsWith(prefix)) })
}

let lastQuery: Record<string, string> = {}

function Page() {
  const [query, setQueryState] = useState<Record<string, string>>({})
  lastQuery = query
  const setQuery = (patch: Record<string, string | null | undefined>) => setQueryState((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(patch)) { if (v === null || v === undefined) delete next[k]; else next[k] = v }
    return next
  })
  return <FilesSection sub="" navigate={() => {}} navEpoch={0} query={query} setQuery={setQuery} />
}

async function goTo(text: string) {
  const box = await screen.findByRole('textbox', { name: 'Go to path' })
  await waitFor(() => expect((box as HTMLInputElement).value).toBe(lastQuery.dir || ROOT))
  fireEvent.change(box, { target: { value: text } })
  fireEvent.keyDown(box, { key: 'Enter' })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  lastQuery = {}
  mocks.fileList.mockReset().mockImplementation(listing)
  mocks.fileComplete.mockReset().mockImplementation(completion)
  mocks.fileRead.mockReset().mockResolvedValue({ content: '# notes', truncated: false, binary: false })
})

describe('Go to path', () => {
  it('🔴 opens a file given its own path, on the folder that holds it', async () => {
    render(<Page />)
    await goTo(FILE)
    const tab = await screen.findByRole('tab', { name: /NOTES\.md/ })
    expect(tab).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(lastQuery.dir).toBe(FOLDER))
    expect(screen.queryByText('That path does not exist.')).toBeNull()
    // Nothing was asked to list a file, so nothing answered an error.
    expect(mocks.fileList).not.toHaveBeenCalledWith(FILE)
  })

  it('🔴 reads a relative path against the folder the box shows', async () => {
    render(<Page />)
    await goTo('memory/instructions/NOTES.md')
    expect(await screen.findByRole('tab', { name: /NOTES\.md/ })).toBeTruthy()
    expect(mocks.fileComplete).toHaveBeenCalledWith(FILE)
    expect(screen.queryByText(/outside the folders PersonalClaw can browse/)).toBeNull()
  })

  it('lists a folder, relative or not', async () => {
    render(<Page />)
    await goTo('./memory/instructions/')
    await waitFor(() => expect(lastQuery.dir).toBe(FOLDER))
    expect(await screen.findByText('NOTES.md')).toBeTruthy()
    expect(screen.queryByRole('tab', { name: /NOTES\.md/ })).toBeNull()
  })

  it('still says a path with nothing there does not exist', async () => {
    render(<Page />)
    await goTo(`${ROOT}/nothing-here`)
    expect(await screen.findByText('That path does not exist.')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
  })

  it('says a ?dir link to a file is a file, not that nothing is there', async () => {
    render(<FilesSection sub="" navigate={() => {}} navEpoch={0} query={{ dir: FILE }} setQuery={() => {}} />)
    expect(await screen.findByText('That path is a file, not a folder.')).toBeTruthy()
  })

  it('offers files as well as folders, read against the folder shown', async () => {
    render(<Page />)
    const box = await screen.findByRole('textbox', { name: 'Go to path' })
    await waitFor(() => expect((box as HTMLInputElement).value).toBe(ROOT))
    fireEvent.focus(box)
    fireEvent.change(box, { target: { value: 'memory/instructions/NO' } })
    await waitFor(() => expect(mocks.fileComplete).toHaveBeenCalledWith(`${FOLDER}/NO`))
    fireEvent.click(await screen.findByRole('button', { name: 'NOTES.md' }))
    expect(await screen.findByRole('tab', { name: /NOTES\.md/ })).toBeTruthy()
  })
})
