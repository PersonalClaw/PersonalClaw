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
// writer: add sends the FULL next array, remove likewise, the repaint comes from the
// reload (pessimistic — same doctrine as every other write in this file: a refused
// write must never leave an optimistic value on screen), and a refusal notifies.

const SLUG = 'tagged-artifact'

// Mutable server state — the repaint must come from a refetch, not local mutation.
let serverTags: string[] = []
const patches: Array<Record<string, unknown>> = []
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
      updateArtifact: async (_s: string, body: Record<string, unknown>) => {
        patches.push(body)
        if (patchFail) throw new Error(patchFail)
        if (Array.isArray(body.tags)) serverTags = body.tags as string[]
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
  it('adding a tag PATCHes the full next array and repaints from the reload', async () => {
    mount()
    await addTag('reports')
    await waitFor(() => expect(patches).toEqual([{ tags: ['reports'] }]))
    // The pill on screen is the refetched server state, not a local echo.
    await screen.findByText('reports')
    expect(serverTags).toEqual(['reports'])
  })

  it('removing a tag PATCHes the array without it', async () => {
    serverTags = ['reports', 'loop:abc123']
    mount()
    fireEvent.click(await screen.findByLabelText('Remove reports'))
    await waitFor(() => expect(patches).toEqual([{ tags: ['loop:abc123'] }]))
    await waitFor(() => expect(screen.queryByText('reports')).toBeNull())
    await screen.findByText('loop:abc123')
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
