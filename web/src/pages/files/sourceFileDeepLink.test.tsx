import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

const SOURCE = '/workspace/reports/q3 review.md'
const mocks = vi.hoisted(() => ({ fileRead: vi.fn() }))

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
        roots: [{ label: 'Workspace', path: '/workspace', name: 'workspace', is_dir: true }],
        entries: [],
        path: '',
      }),
      fileList: async (path: string) => ({ roots: [], entries: [], path }),
      fileGitStatus: async () => ({ repoRoot: '', branch: '', statuses: {} }),
      artifacts: async () => [],
      fileRead: mocks.fileRead,
    },
  }
})

const { FilesSection } = await import('./FilesSection')
const { registerBuiltinContentTypes } = await import('../../ui/content/registerBuiltins')
registerBuiltinContentTypes()

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  mocks.fileRead.mockReset()
  mocks.fileRead.mockResolvedValue({ content: '# Q3 review', truncated: false, binary: false })
})

describe('Files source-file deep link (#351)', () => {
  it('opens the file named by ?file as the active tab', async () => {
    render(
      <FilesSection
        sub=""
        navigate={() => {}}
        navEpoch={0}
        query={{ dir: '/workspace/reports', file: SOURCE }}
        setQuery={() => {}}
      />,
    )

    const tab = await screen.findByRole('tab', { name: /q3 review\.md/i })
    expect(tab).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(mocks.fileRead).toHaveBeenCalledWith(SOURCE, true))
  })
})
