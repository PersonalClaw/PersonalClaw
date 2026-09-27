import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact } from '../../lib/api'

// ── An artifact body saved from a stale copy is refused, and the change is kept ───────────────
//
// The viewer saves the WHOLE body it edited. The agent's `artifact_update`, a workflow publishing
// a version, a write to the file the artifact points at, or another tab could change the body
// while it was open, and the old viewer's Save (or Snapshot) sent its copy over that change
// without a word. The gateway now refuses a copy that went stale (`409 stale_write`); this pins
// what the viewer does with the refusal. Driven at ContentSurface's prop seam — Monaco does not
// mount under jsdom (`artifactDraftSurvivesNavigation.test.tsx` documents the trap).

const SLUG = 'q3-brief'
// The user changes line 1; the agent changed line 4 in the meantime — apart, so it re-applies.
const READ = 'a\nb\nc\nd\n'
const AGENTS = 'a\nb\nc\nD\n'
const MINE = 'A\nb\nc\nd\n'
const BOTH = 'A\nb\nc\nD\n'

function staleWrite() {
  return Object.assign(new Error(`This write replaces the body of the artifact '${SLUG}', which changed…`), { status: 409, code: 'stale_write' })
}

let seen: Record<string, any> = {}

async function mount({ acceptFirst = false } = {}) {
  vi.resetModules()
  seen = {}
  let stored = { content: READ, revision: 'r1' }
  // A probe kind registered below, so the fixture is cast the way `artifactTagsEditor.test.tsx`
  // casts its own: the kind union names only the shipped kinds.
  const artifact = vi.fn(async (): Promise<Artifact> => ({
    slug: SLUG, name: 'Q3 brief', kind: 'stalebodyprobe', source: 'chat', description: '', tags: [],
    version: 1, created_at: '2026-09-26T00:00:00Z', updated_at: '2026-09-26T00:00:00Z', events: [],
    source_path: '', readonly: false, content: stored.content, content_revision: stored.revision,
  } as unknown as Artifact))
  const saveArtifactBody = vi.fn(async (_s: string, content: string, base: string) => {
    if (base !== stored.revision) throw staleWrite()
    stored = { content, revision: 'r3' }
    return artifact()
  })
  const snapshotArtifactBody = vi.fn(saveArtifactBody)
  const notify = vi.fn()
  vi.doMock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    return {
      ...real,
      api: {
        ...real.api, artifact, saveArtifactBody, snapshotArtifactBody,
        artifactVersions: async () => ({ slug: SLUG, versions: [1] }),
        artifactEvents: async () => ({ slug: SLUG, events: [] }),
        viewRender: async () => ({}),
        deployedArtifacts: async () => [],
      },
    }
  })
  vi.doMock('../../ui/content/ContentSurface', () => ({
    ContentSurface: (props: Record<string, any>) => {
      seen = props
      return <div data-testid="surface">{props.banner}</div>
    },
  }))
  const { registerContentType } = await import('../../ui/content/contentTypes')
  registerContentType({
    id: 'stalebodyprobe', label: 'Probe', icon: FileText, tone: '#888888',
    kinds: ['stalebodyprobe'],
    preview: { render: ({ content }: { content: string }) => <div>{content}</div> },
    commentable: false,
  })
  const { ArtifactViewer } = await import('./ArtifactViewer')
  await act(async () => {
    render(<ArtifactViewer slug={SLUG} onChanged={() => {}} onDeleted={() => {}} onOpenSourceFile={() => {}} />)
  })
  await waitFor(() => expect(seen.onSave).toBeTypeOf('function'))
  // The surface reports the draft it seeded — the body as read — so the viewer's base is set.
  act(() => { seen.onDraftChange(READ, false) })
  await waitFor(() => expect(seen.draftBase).toEqual({ value: READ, revision: 'r1' }))
  // …and then the agent writes the body, while the page is open.
  if (!acceptFirst) stored = { content: AGENTS, revision: 'r2' }
  return { saveArtifactBody, snapshotArtifactBody, notify }
}

describe('an artifact body save from a stale copy', () => {
  it('is refused into the notice: nothing saved, no error toast, the draft held still', async () => {
    const { saveArtifactBody, notify } = await mount()
    await act(async () => { await seen.onSave(MINE) })

    expect(saveArtifactBody.mock.calls[0]).toEqual([SLUG, MINE, 'r1'])
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/This artifact changed elsewhere/)
    expect(alert.textContent).toMatch(/your change\s+wasn’t saved/)
    // A refusal is the notice's, not a failure toast — and the draft is held for it.
    expect(notify).not.toHaveBeenCalled()
    expect(seen.locked).toBe(true)
  })

  it('Reload and reapply saves the change on top of the agent’s, over the new revision', async () => {
    const { saveArtifactBody } = await mount()
    await act(async () => { await seen.onSave(MINE) })
    const reapply = within(await screen.findByRole('alert')).getByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.hasAttribute('disabled')).toBe(false))
    await act(async () => { fireEvent.click(reapply) })

    await waitFor(() => expect(saveArtifactBody).toHaveBeenCalledTimes(2))
    expect(saveArtifactBody.mock.calls[1]).toEqual([SLUG, BOTH, 'r2'])
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  })

  it('a Snapshot is the same save: the same base, the same refusal', async () => {
    const { snapshotArtifactBody } = await mount()
    const snapshot = (seen.actions as Array<{ label: string; run: (d: string) => Promise<void> }>)
      .find((a) => a.label === 'Snapshot')!
    await act(async () => { await snapshot.run(MINE) })

    expect(snapshotArtifactBody.mock.calls[0]).toEqual([SLUG, MINE, 'r1'])
    expect((await screen.findByRole('alert')).textContent).toMatch(/This artifact changed elsewhere/)
  })

  it('a save that lands names the revision the page painted', async () => {
    const { saveArtifactBody } = await mount({ acceptFirst: true })
    await act(async () => { await seen.onSave(MINE) })

    expect(saveArtifactBody.mock.calls[0]).toEqual([SLUG, MINE, 'r1'])
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
