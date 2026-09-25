import { lazy, Suspense, useState } from 'react'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ContentType, PreviewProps } from './contentTypes'
import { ContentSurface } from './ContentSurface'

// ── A first preview suspends the PREVIEW, not the page around it ───────────────────────────────
//
// Every registry preview renderer is `lazy()` (registerBuiltins), so the first file of each type
// in a session suspends while its chunk downloads. ContentSurface gave that suspension no boundary
// of its own, so it climbed to the ROUTE's boundary in App.tsx — which hid the whole Files page,
// header and explorer included, to show one preview's spinner (and, through the route fade, left
// it blank: `app/routeFadeSurvivesASuspension.test.tsx` has that half).
//
// So the host here has ALREADY painted when the preview mounts, inside a boundary standing in for
// the route's: the Files page with the explorer up, one click away from its first `.md`. On the
// old tree the click put "route fallback" on screen and hid the page (`display: none`).
//
// Stubbed for the environment only: jsdom cannot run Monaco (the split view mounts it beside the
// preview), and the renderer is a probe rather than a registry one, whose chunk pulls in Monaco.
// The laziness is the real mechanism — a `lazy()` whose chunk arrives when the test says so.

vi.mock('@monaco-editor/react', () => ({ default: () => <div data-testid="monaco" />, DiffEditor: () => null }))
vi.mock('../../app/theme', () => ({ useMode: () => ({ mode: 'dark' }) }))

/** A preview renderer shaped like the registry's: `lazy()`, so its first render suspends until its
 *  chunk arrives — here, when the test calls `arrive`. */
function lazyPreview() {
  let arrive!: () => void
  const chunk = new Promise<void>((resolve) => { arrive = resolve })
  const Preview = lazy(async () => {
    await chunk
    return { default: ({ content }: PreviewProps) => <p>rendered: {content}</p> }
  })
  return { Preview, arrive }
}

const probeType = (Preview: ReturnType<typeof lazyPreview>['Preview']): ContentType => ({
  id: 'probe', label: 'Probe', icon: (() => null) as unknown as ContentType['icon'], tone: '',
  preview: { render: Preview },
  edit: { language: 'markdown', split: true },
})

function FilesLikeHost({ type, view }: { type: ContentType; view: 'preview' | 'split' }) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid="page">
      <h1>Files</h1>
      <button type="button" onClick={() => setOpen(true)}>Open notes.md</button>
      {open && (
        <ContentSurface type={type} content="# notes" title="notes.md" docId="notes.md"
          onSave={() => {}} initialView={view} />
      )}
    </div>
  )
}

describe.each(['preview', 'split'] as const)('a first lazy preview in the %s view', (view) => {
  it('waits inside the preview pane while the page stays on screen', async () => {
    const { Preview, arrive } = lazyPreview()
    render(
      <Suspense fallback={<p>route fallback</p>}>
        <FilesLikeHost type={probeType(Preview)} view={view} />
      </Suspense>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Open notes.md' }))

    // The chunk is still in flight. The page is the page: nothing reached the outer boundary.
    expect(screen.queryByText('route fallback'), 'the preview suspended the whole page').toBeNull()
    expect(screen.getByRole('heading', { name: 'Files' })).toBeVisible()
    // The wait is shown — and announced — where it is happening, in the preview pane.
    const pane = screen.getByRole('group', { name: 'notes.md preview' })
    expect(within(pane).getByRole('status')).toHaveTextContent('Loading…')

    await act(async () => { arrive() })

    expect(await within(pane).findByText('rendered: # notes')).toBeVisible()
    expect(within(pane).queryByRole('status')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Files' })).toBeVisible()
  })
})
