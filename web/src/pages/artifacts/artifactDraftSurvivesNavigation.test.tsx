import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact } from '../../lib/api'
import { registerContentType } from '../../ui/content/contentTypes'
import { ArtifactViewer } from './ArtifactViewer'

// ── Editing an artifact and navigating away discarded the edit (issue 691) ────────────
//
// `ContentSurface` supports draft persistence — it seeds `draft` from a host-owned store on
// mount, mirrors every keystroke into it, and deletes the entry once the draft matches the
// saved content. It is entirely OPT-IN via the `draftStore` prop, and this call site passed
// nothing. Measured through the UI: `Save` went disabled → enabled and the editor's view
// lines went 16 → 20, so the surface knew the buffer was dirty; then navigating to Library
// dropped it with no confirm and nothing restored on return.
//
// The store is owned at MODULE scope here, not in a `useRef`. A ref dies with the component,
// and the component is exactly what unmounts on navigation. (The Code cockpit passes a
// ref-held store because its problem is switching TABS while its page stays mounted.)
//
// 🪤 THE FAKE VERSION OF THIS TEST types into the editor, unmounts, remounts and asserts the
// text came back. It cannot be written here: the built-in editor is Monaco, which does not
// mount under jsdom (no web workers — `artifactLiveRefresh.test.tsx` documents the same
// trap), and registering a CUSTOM editor instead would bypass the draft path altogether
// (`draftEditable = editable && !custom`). So these assertions are at the prop seam, which is
// where the defect actually was: the store was never handed over. The store's own
// seed/mirror/clear behaviour is ContentSurface's and already covered where it can run.
//
// 🪤 AND ASSERTING "a draftStore is passed" ALONE WOULD BE WRONG. Passing it
// unconditionally is a second bug: `renderPreview` feeds the preview `content: draft`, and
// the surface seeds `draft` from the store regardless of read-only — so a pending draft of
// the CURRENT version would render as though it were the historical version being viewed.
// The remount key carries the version but `docId` (the slug) does not, so the store cannot
// tell them apart. Both directions are asserted below.

const SLUG = 'verdant-hollow-design-notes'
const BODY = 'Torque tables, page 4.'

/** Props the viewer handed to ContentSurface on the most recent render. */
let seen: Record<string, unknown> = {}

vi.mock('../../ui/content/ContentSurface', () => ({
  ContentSurface: (props: Record<string, unknown>) => {
    seen = props
    return <div data-testid="surface">{String(props.content ?? '')}</div>
  },
}))

let selectedVersion: number | null = null

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Verdant Hollow design notes', kind: 'draftprobe', source: 'chat',
    description: '', tags: [], version: 3, content: BODY,
    created_at: '2026-08-20T00:00:00Z', updated_at: '2026-08-20T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => fixture(),
      artifactVersions: async () => ({ slug: SLUG, versions: [1, 2, 3] }),
      artifactEvents: async () => ({ slug: SLUG, events: [] }),
      artifactVersion: async (_s: string, v: number) => ({ ...fixture(), version: v, content: `body of v${v}` }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

registerContentType({
  id: 'draftprobe', label: 'Probe', icon: FileText, tone: '#888888',
  kinds: ['draftprobe'],
  preview: { render: ({ content }: { content: string }) => <div>{content}</div> },
  commentable: false,
})

beforeEach(() => {
  seen = {}
  selectedVersion = null
})

async function mountViewer() {
  const view = render(
    <ArtifactViewer slug={SLUG} defaultDetailsOpen onChanged={vi.fn()}
      onDeleted={() => {}} onOpenSourceFile={() => {}} />,
  )
  await waitFor(() => expect(screen.queryByTestId('surface')).not.toBeNull())
  return view
}

describe('the artifact viewer hands ContentSurface a draft store', () => {
  it('passes a store, so an unsaved edit is not dropped on unmount', async () => {
    await mountViewer()

    // The defect was the absence of this prop — nothing else about the surface changed.
    expect(seen.draftStore).toBeInstanceOf(Map)
  })

  it('keys the store by the artifact slug, matching the docId the surface reads', async () => {
    await mountViewer()

    // ContentSurface does `draftStore.get(docId)`, so a store keyed on anything else would be
    // handed over and never consulted — wired and inert.
    expect(seen.docId).toBe(SLUG)
  })

  it('survives the component being unmounted and remounted', async () => {
    const first = await mountViewer()
    const store = seen.draftStore as Map<string, { draft: string; base: string }>
    // Stand in for what the editor's mirror effect does on a keystroke. The real editor cannot
    // run here (see the jsdom trap above); what is under test is that the STORE outlives the
    // component, which a `useRef`-held one would not.
    store.set(SLUG, { draft: 'Torque tables, page 4. Reconciled.', base: BODY })

    first.unmount()
    seen = {}
    await mountViewer()

    const after = seen.draftStore as Map<string, { draft: string; base: string }>
    expect(after.get(SLUG)?.draft).toBe('Torque tables, page 4. Reconciled.')
  })

  it('withholds the store on a read-only view, so a draft cannot leak into it', async () => {
    // The second bug, avoided. A historical version is read-only, and the preview renders
    // `draft` — so seeding from the current version's draft would misreport that version's
    // contents. Driven through the real version rail rather than by setting a prop.
    await mountViewer()
    const select = screen.getByRole('combobox', { name: 'Version' })
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(select, { target: { value: '1' } })

    await waitFor(() => expect(seen.readOnly).toBe(true))
    expect(seen.draftStore).toBeUndefined()
    void selectedVersion
  })
})
