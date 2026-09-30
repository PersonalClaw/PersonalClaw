import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── An image or PDF opened from Files is saved as a versioned artifact too ────────────────────
//
// The viewer offered "Save as a versioned artifact" for text only (an image showed its name and
// Download), while Artifacts keeps images and PDFs as versioned artifacts of their own. It now
// offers the action for both. A text file's artifact live-points at the file; an image's or a PDF's
// is a COPY of its bytes, which the gateway reads and checks itself — so the dialog says which, and
// the save sends the file's path and kind, never a body.
//
// 🪤 Driven through ContentSurface's prop seam, as `fileViewerStaleWrite.test.tsx` is: a bundled
// preview does not mount under jsdom, and what is under test is the host chrome (the action, the
// dialog, the call), not the preview.

async function mount(path: string) {
  vi.resetModules()
  const saveFileCopyAsArtifact = vi.fn(async (body: { name: string; kind: string; source_path: string }) =>
    ({ slug: 'receipt', name: body.name, kind: body.kind, version: 1 }))
  const saveFileAsArtifact = vi.fn(async () => ({ slug: 'notes', version: 1 }))
  const fileRead = vi.fn(async () => ({ content: '# Notes\n', truncated: false, binary: false, revision: 'r1' }))
  vi.doMock('../../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../../lib/api')>()
    return {
      ...real,
      api: {
        ...real.api, fileRead, saveFileCopyAsArtifact, saveFileAsArtifact,
        fileWatchUrl: () => '/watch', fileRawUrl: () => '/raw', revealPath: async () => ({}),
      },
    }
  })
  vi.doMock('../../../ui/content/ContentSurface', async () => {
    const React = await import('react')
    return {
      ContentSurface: React.forwardRef((props: Record<string, any>, ref) => {
        React.useImperativeHandle(ref, () => ({ save: () => {}, replaceDraft: () => {} }))
        return <div data-testid="surface">{props.headerExtras}{props.banner}</div>
      }),
    }
  })
  const { registerBuiltinContentTypes } = await import('../../../ui/content/registerBuiltins')
  registerBuiltinContentTypes()
  const { ChatFilePanel } = await import('../../chat/ChatFilePanel')
  await act(async () => { render(<ChatFilePanel path={path} onClose={() => {}} />) })
  await waitFor(() => expect(screen.queryByTestId('surface')).not.toBeNull())
  return { saveFileCopyAsArtifact, saveFileAsArtifact }
}

describe('Save as a versioned artifact', () => {
  it('is offered for an image, says it saves a copy, and sends the file, not a body', async () => {
    const { saveFileCopyAsArtifact, saveFileAsArtifact } = await mount('/ws/talk/receipt.png')

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save a copy as a versioned artifact' })) })
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toMatch(/Saves a copy of receipt\.png as a versioned artifact\./)
    expect(dialog.textContent).not.toMatch(/live-points/)

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save artifact' })) })
    expect(saveFileCopyAsArtifact).toHaveBeenCalledWith({ name: 'receipt.png', kind: 'image', source_path: '/ws/talk/receipt.png' })
    expect(saveFileAsArtifact).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('saves a PDF as a PDF artifact', async () => {
    const { saveFileCopyAsArtifact } = await mount('/ws/talk/slides.pdf')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save a copy as a versioned artifact' })) })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save artifact' })) })
    expect(saveFileCopyAsArtifact).toHaveBeenCalledWith({ name: 'slides.pdf', kind: 'pdf', source_path: '/ws/talk/slides.pdf' })
  })

  it('keeps a text file on its live pointer, as before', async () => {
    const { saveFileCopyAsArtifact } = await mount('/ws/notes.md')
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Save as a versioned artifact' })) })
    const dialog = await screen.findByRole('dialog')
    expect(dialog.textContent).toMatch(/live-points at notes\.md/)
    expect(saveFileCopyAsArtifact).not.toHaveBeenCalled()
  })
})
