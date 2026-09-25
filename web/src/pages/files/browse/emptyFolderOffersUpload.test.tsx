import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { FsEntry } from '../../../lib/api'
import { FileTree } from './FileTree'

// ── An empty folder shows how to fill it ───────────────────────────────────────────────────────
//
// Measured on `#/files` with an empty folder open: the explorer said "Empty" and nothing else. The
// only way in was dragging files onto it, with no hint that it takes a drop, and "Upload here"
// existed only in a SUB-folder's row menu — so the folder you were standing in could not be
// uploaded to by clicking at all.
//
// The button must be the tree's EXISTING upload path, not a second one: it has to reach the same
// `onUpload(folder, files)` a row's "Upload here" does, with the current folder as the target, so the
// host's precheck, progress rows, cancel and error banner all apply unchanged.

const ROOT = '/ws/q4-launch'
const empty: FsEntry[] = []
type Props = Parameters<typeof FileTree>[0]

function tree(over: Partial<Props> = {}) {
  const onUpload = vi.fn()
  render(
    <FileTree
      dirs={{ cache: { [ROOT]: empty }, errors: {}, load: vi.fn(async () => empty), invalidate: vi.fn(), invalidateSubtree: vi.fn() } as unknown as Props['dirs']}
      rootPath={ROOT} activePath={null} gitStatuses={{}} onOpenFile={vi.fn()}
      artifactPaths={new Set()} onRename={vi.fn()} onDelete={vi.fn()} onUpload={onUpload}
      {...over} />,
  )
  return { onUpload }
}

describe('an empty folder offers upload', () => {
  it('shows a visible upload control and says it takes a drop', async () => {
    tree({ emptyUpload: true, emptyLabel: 'This folder is empty' })
    expect(await screen.findByText('This folder is empty')).toBeVisible()
    expect(screen.getByRole('button', { name: /upload files/i })).toBeVisible()
    expect(screen.getByText(/drop files here/i)).toBeVisible()
  })

  it('uploads what is picked into THIS folder, through the tree’s own upload callback', async () => {
    const { onUpload } = tree({ emptyUpload: true })
    const brief = new File(['# Launch brief'], 'launch-brief.md', { type: 'text/markdown' })
    // The button opens this picker; jsdom has no file dialog, so the pick is made on it directly.
    await userEvent.upload(screen.getByLabelText('Upload files to q4-launch'), brief)
    expect(onUpload).toHaveBeenCalledTimes(1)
    const [folder, files] = onUpload.mock.calls[0] as [FsEntry, File[]]
    expect(folder).toMatchObject({ path: ROOT, name: 'q4-launch', is_dir: true })
    expect(files.map((f) => f.name)).toEqual(['launch-brief.md'])
  })

  it('the button opens that picker', async () => {
    tree({ emptyUpload: true })
    const picker = screen.getByLabelText('Upload files to q4-launch') as HTMLInputElement
    const opened = vi.spyOn(picker, 'click')
    await userEvent.click(screen.getByRole('button', { name: /upload files/i }))
    expect(opened).toHaveBeenCalledTimes(1)
  })

  it('a host that does not opt in keeps the bare label — a Code project folder is the worker’s', async () => {
    tree({ emptyLabel: 'No files yet — the worker will create them here.' })
    expect(await screen.findByText('No files yet — the worker will create them here.')).toBeVisible()
    expect(screen.queryByRole('button', { name: /upload files/i })).toBeNull()
  })
})
