import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FileText } from 'lucide-react'
import type { Artifact, ArtifactEvent } from '../../lib/api'
import { registerContentType, type DocumentEditorProps } from '../../ui/content/contentTypes'
import { ArtifactViewer } from './ArtifactViewer'

// ── issue 2753: a document save has to change something on screen ────────────────────────
//
// A custom editor (`type.edit.render` — the office documents) owns its own persistence, so the
// host never learned a version had been cut: the write landed, the header kept reading
// `· v1 · 1 event`, and only a page reload told the truth. For a BINARY document that header is
// the entire save receipt — the preview is extracted text that looks the same either way, and a
// LOSSLESS document is not even shown a save-confirmation dialog (`DocumentEditor` gates it on
// `!loaded.loss.lossless`, correctly). So a user who saved and saw `v1 · 1 event` had three
// readings available — it didn't save, it saved over v1, the page is stale — and no way to pick
// one without reloading.
//
// 🪤 THE FAKE VERSION OF THIS TEST asserts on the SAVE CALL: "the editor posted the model, so the
// save works". That is exactly what was already true and exactly what the defect survived. The
// assertions below are on the RENDERED header, differentially, with the same viewer mounted
// across the save — a reload would be the bug, not the fix.
//
// 🪤 A HEADER THAT NEVER RENDERED AT ALL would make "now says v2" pass for the wrong reason, so
// each assertion sits behind a vacuity floor that pins the pre-save text first.
//
// 🪤 THE PROBE EDITOR IS A REAL CUSTOM EDITOR, not a stub of ContentSurface: it is registered
// through the same `edit.render` slot the office types use and it is handed the same props, so
// the whole chain under test — `onSaved` → ContentSurface's `onDocumentSaved` → the viewer's
// quiet refetch — is the production one. That the office editors CALL `onSaved` after a real
// write is pinned separately, in `ui/content/documentEditorContract.test.tsx`.

const SLUG = 'q3-field-report'

// Mutable server state: the point is that a refetch AFTER the save sees the new version.
let current = { version: 1, content: 'extracted text, unchanged by a bold' }
let versions: number[] = [1]
let events: ArtifactEvent[] = []
const fetches = { artifact: 0 }

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Q3 field report', kind: 'saveprobe', source: 'chat',
    description: '', tags: [], version: current.version, content: current.content,
    created_at: '2026-09-18T00:00:00Z', updated_at: '2026-09-18T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => { fetches.artifact++; return fixture() },
      artifactVersions: async () => ({ slug: SLUG, versions }),
      artifactEvents: async () => ({ slug: SLUG, events }),
      artifactVersion: async (_s: string, v: number) => ({ ...fixture(), version: v, content: `body of v${v}` }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

/** A custom editor standing in for `DocumentEditor`/`SheetGrid`/`SlideDeck`: it owns its own
 *  write (here, advancing the fake server) and reports the accepted version through `onSaved`,
 *  which is exactly the contract those three implement. */
function ProbeEditor({ slug, onSaved }: DocumentEditorProps) {
  return (
    <button
      type="button"
      onClick={() => {
        // The write the real editors do through `api.saveArtifactModel`: a new version plus its
        // `edited` timeline entry.
        current = { version: 2, content: current.content }
        versions = [1, 2]
        events = [...events, { type: 'edited', version: 2, ts: '2026-09-18T01:00:00Z', by: 'user' } as unknown as ArtifactEvent]
        onSaved?.(2)
      }}
    >
      Save {slug}
    </button>
  )
}

// No `preview`, so <ContentSurface> opens straight in edit view and the probe editor mounts —
// a lazy preview cannot mount under jsdom anyway (see artifactLiveRefresh.test.tsx).
registerContentType({
  id: 'saveprobe', label: 'Save probe', icon: FileText, tone: '#888888',
  kinds: ['saveprobe'],
  edit: { language: 'plaintext', render: ProbeEditor },
  commentable: false,
})

beforeEach(() => {
  current = { version: 1, content: 'extracted text, unchanged by a bold' }
  versions = [1]
  events = [{ type: 'created', version: 1, ts: '2026-09-18T00:00:00Z', by: 'agent' } as unknown as ArtifactEvent]
  fetches.artifact = 0
})

/** The Details header's version/event summary — the artifact's only save receipt. */
function summary(): string {
  return screen.getByRole('button', { expanded: true }).textContent ?? ''
}

async function mountViewer(onChanged = vi.fn()) {
  render(<ArtifactViewer slug={SLUG} defaultDetailsOpen onChanged={onChanged}
    onDeleted={() => {}} onOpenSourceFile={() => {}} />)
  await waitFor(() => expect(screen.queryByRole('button', { name: /^Save / })).not.toBeNull())
  return onChanged
}

describe('a document save updates the artifact header without a reload', () => {
  it('advances the version and the event count in place', async () => {
    await mountViewer()

    // VACUITY FLOOR — the summary must actually be rendering the pre-save numbers, or the
    // post-save assertion would pass on a header that renders nothing.
    expect(summary()).toContain('· v1')
    expect(summary()).toContain('1 event')

    const loadsBefore = fetches.artifact
    await userEvent.click(screen.getByRole('button', { name: /^Save / }))

    await waitFor(() => expect(summary()).toContain('· v2'))
    expect(summary()).toContain('2 events')
    // …and it came from a REFETCH, not from a locally incremented counter that would drift the
    // moment another writer landed a version in between.
    expect(fetches.artifact).toBeGreaterThan(loadsBefore)
  })

  it('keeps the library grid in step, the same pairing the live-refresh path uses', async () => {
    const onChanged = await mountViewer()
    expect(onChanged).not.toHaveBeenCalled()  // vacuity floor
    await userEvent.click(screen.getByRole('button', { name: /^Save / }))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('does not tear the editor down — the refresh is quiet, not a reload', async () => {
    await mountViewer()
    const before = screen.getByRole('button', { name: /^Save / })
    await userEvent.click(before)
    await waitFor(() => expect(summary()).toContain('· v2'))
    // SAME DOM node. A non-quiet reload swaps the body for the spinner and remounts the editor,
    // which would drop the user's place and any in-progress edit — the opposite of a receipt.
    expect(screen.getByRole('button', { name: /^Save / })).toBe(before)
  })
})
