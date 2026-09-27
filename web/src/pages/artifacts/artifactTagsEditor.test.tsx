import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact } from '../../lib/api'
import { registerContentType } from '../../ui/content/contentTypes'
import { ArtifactViewer } from './ArtifactViewer'

// ── #669: artifact tags get their first UI writer ────────────────────────────────────
//
// `PATCH /api/artifacts/{slug}` accepted and persisted `tags` all along, and the list
// endpoint's tag filter is load-bearing (loop cockpits find their deliverables by it) —
// but none of the frontend's updateArtifact call sites ever sent `tags`, so the details
// rail rendered static pills a user could read and never change. These tests pin the
// writer: an add sends that ONE name as an add and a remove that one name as a remove —
// never the page's copy of the whole list, which dropped any tag added elsewhere since
// (the server applies each name to the tags stored when it lands) — the repaint comes
// from the reload (pessimistic — same doctrine as every other write in this file: a
// refused write must never leave an optimistic value on screen), and a refusal notifies.

const SLUG = 'tagged-artifact'

// Mutable server state — the repaint must come from a refetch, not local mutation.
let serverTags: string[] = []
const patches: Array<{ add?: string[]; remove?: string[] }> = []
let patchFail = ''
const notified: string[] = []
let notifyArrived!: () => void
let notifySettled: Promise<void>

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Tagged artifact', kind: 'tagprobe', source: 'chat',
    description: '', tags: [...serverTags], version: 1, content: 'body',
    created_at: '2026-09-04T00:00:00Z', updated_at: '2026-09-04T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  notify: (msg: string) => { notified.push(msg); notifyArrived() },
}))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => fixture(),
      artifactVersions: async () => ({ slug: SLUG, versions: [1] }),
      artifactEvents: async () => ({ slug: SLUG, events: [] }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
      // What the gateway does with a per-name edit: applied to the tags stored NOW.
      editArtifactTags: async (_s: string, edit: { add?: string[]; remove?: string[] }) => {
        patches.push(edit)
        if (patchFail) throw new Error(patchFail)
        const kept = serverTags.filter((t) => !(edit.remove ?? []).includes(t))
        serverTags = [...kept, ...(edit.add ?? []).filter((t) => !kept.includes(t))]
        return fixture()
      },
    },
  }
})

function ProbePreview({ content }: { content: string }) {
  return <div data-testid="preview">{content}</div>
}
registerContentType({
  id: 'tagprobe', label: 'Probe', icon: FileText, tone: '#888888',
  kinds: ['tagprobe'],
  preview: { render: ProbePreview },
  commentable: false,
})

beforeEach(() => {
  serverTags = []
  patches.length = 0
  patchFail = ''
  notified.length = 0
  notifySettled = new Promise((r) => { notifyArrived = r })
})

function mount() {
  return render(
    <ArtifactViewer slug={SLUG} onChanged={() => {}} onDeleted={() => {}} onOpenSourceFile={() => {}} defaultDetailsOpen />,
  )
}

async function addTag(value: string) {
  const input = await screen.findByLabelText('Add a tag')
  fireEvent.change(input, { target: { value } })
  fireEvent.keyDown(input, { key: 'Enter' })
}

describe('artifact tags editor (#669)', () => {
  it('adding a tag sends that one name and repaints from the reload', async () => {
    mount()
    await addTag('reports')
    await waitFor(() => expect(patches).toEqual([{ add: ['reports'], remove: [] }]))
    // The pill on screen is the refetched server state, not a local echo.
    await screen.findByText('reports')
    expect(serverTags).toEqual(['reports'])
  })

  it('removing a tag sends that one name as a remove', async () => {
    serverTags = ['reports', 'loop:abc123']
    mount()
    fireEvent.click(await screen.findByLabelText('Remove reports'))
    await waitFor(() => expect(patches).toEqual([{ add: [], remove: ['reports'] }]))
    await waitFor(() => expect(screen.queryByText('reports')).toBeNull())
    await screen.findByText('loop:abc123')
  })

  it('a tag added elsewhere after this page read the list survives an edit made here', async () => {
    // The loss the per-name edit exists to stop: the page's copy is ['reports'], the agent then
    // tags the artifact `loop:abc123`, and the user adds `q3`. Sending the page's list would have
    // stored ['reports', 'q3'] and dropped the agent's tag without a word.
    serverTags = ['reports']
    mount()
    await screen.findByText('reports')
    serverTags = ['reports', 'loop:abc123']
    await addTag('q3')
    await waitFor(() => expect(patches).toEqual([{ add: ['q3'], remove: [] }]))
    await screen.findByText('loop:abc123')
    expect(serverTags).toEqual(['reports', 'loop:abc123', 'q3'])
  })

  it('a duplicate add is a no-op — no PATCH fires', async () => {
    serverTags = ['reports']
    mount()
    await screen.findByText('reports')
    await addTag('reports')
    // Settle a microtask round; the guard is synchronous.
    await new Promise((r) => setTimeout(r, 20))
    expect(patches).toEqual([])
  })

  it('a refused write notifies and leaves no optimistic tag on screen', async () => {
    patchFail = 'tags must be strings'
    mount()
    await addTag('reports')
    await notifySettled
    expect(notified[0]).toContain('Could not update tags')
    expect(screen.queryByText('reports')).toBeNull()
  })
})
