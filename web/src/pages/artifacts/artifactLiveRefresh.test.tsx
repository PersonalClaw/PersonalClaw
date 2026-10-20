import { describe, it, expect, beforeEach, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact, ArtifactEvent } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import { registerContentType } from '../../ui/content/contentTypes'
import { ArtifactViewer } from './ArtifactViewer'

// ── The detail view re-reads an artifact once the store has written it ───────────────
//
// The iterate panel is a `ChatEmbed` — a sandboxed iframe, a SEPARATE document with no
// postMessage bridge back to the page. So when the agent writes a new version from inside
// it, nothing in the host DOM knows.
//
// The view used to learn it from the chat's `tool_call` frame, which is sent when the model
// ASKS for the call: before the call's approval and before the write. So it re-read the
// version it already showed and never read again — measured on a real drive: the panel said
// "→ version 2" and the pane beside it kept "v1 · 1 event" until a reload. An image edit, made
// by `image_generate` rather than `artifact_update`, never matched at all.
//
// The trigger is now the gateway's `artifacts` refresh hint, which the store's own change
// signal sends AFTER the write, whoever wrote (`DashboardState.announce_artifact_change`).
// These tests deliver that frame, with the server state already moved, because that is the
// order the gateway guarantees.
//
// 🪤 THE FAKE VERSION OF THIS TEST asserts that the viewer "can render v3" by mounting it
// with v3 already in the fixture. That proves nothing: it passes with the socket
// subscription deleted. So the frame is delivered to a MOUNTED, already-painted viewer and
// the assertions are differential — rail N→N+1, preview text before→after, and the preview
// DOM node's identity (a remount would be a reload by another name).
//
// 🪤 AN EMPTY RAIL WOULD MAKE "N→N+1" PASS FOR THE WRONG REASON — 0→1 is also N+1. Every
// growth assertion below sits behind a vacuity floor that pins the rail's contents before
// the frame arrives.
//
// 🪤 A `lazy()` PREVIEW CANNOT MOUNT UNDER JSDOM (every bundled renderer pulls Monaco, and
// there are no web workers). The registry accepts a plain component too, so a probe type is
// registered with one that renders its `content` prop verbatim. That is precisely the fact
// under test — did the new body reach the render surface — with no stub of ContentSurface
// itself, so ContentSurface's real "baseline moved under an unedited view" adoption is what
// carries the repaint.

const SLUG = 'revenue-widget'
const V2_BODY = 'chart: revenue only'
const V3_BODY = 'chart: revenue AND margin'

// Every subscriber's handler, so a test can deliver a frame the way the one socket does: to all.
const handlers = new Set<{ current: (m: WsMessage) => void }>()
vi.mock('../../lib/useChatSocket', async (orig) => {
  const real = await orig<typeof import('../../lib/useChatSocket')>()
  const React = await import('react')
  return {
    ...real,
    useChatSocket: (cb: (m: WsMessage) => void) => {
      const ref = React.useRef(cb)
      ref.current = cb
      React.useEffect(() => {
        handlers.add(ref)
        return () => { handlers.delete(ref) }
      }, [])
    },
  }
})
function deliver(m: WsMessage) { for (const h of [...handlers]) h.current(m) }

// Mutable server state — the point is that a refetch AFTER the frame sees something new.
let current = { version: 2, content: V2_BODY }
let versions: number[] = [1, 2]
let events: ArtifactEvent[] = []
let versionsFail = ''
let eventsFail = ''
const fetches = { artifact: 0 }

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Revenue widget', kind: 'ae10probe', source: 'chat',
    description: '', tags: [], version: current.version, content: current.content,
    created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifact: async () => { fetches.artifact++; return fixture() },
      artifactVersions: async () => {
        if (versionsFail) throw new Error(versionsFail)
        return { slug: SLUG, versions }
      },
      artifactEvents: async () => {
        if (eventsFail) throw new Error(eventsFail)
        return { slug: SLUG, events }
      },
      artifactVersion: async (_s: string, v: number) => ({ ...fixture(), version: v, content: `body of v${v}` }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
    },
  }
})

/** A PLAIN preview component (see the jsdom trap above) that renders its content
 *  prop verbatim. */
function ProbePreview({ content }: { content: string }) {
  return <div data-testid="preview">{content}</div>
}

registerContentType({
  id: 'ae10probe', label: 'Probe', icon: FileText, tone: '#888888',
  kinds: ['ae10probe'],
  preview: { render: ProbePreview },
  commentable: false,
})

beforeEach(() => {
  handlers.clear()
  current = { version: 2, content: V2_BODY }
  versions = [1, 2]
  events = [{ type: 'created', version: 1, ts: '2026-08-16T00:00:00Z', by: 'agent' } as unknown as ArtifactEvent]
  versionsFail = ''
  eventsFail = ''
  fetches.artifact = 0
})

/** The version rail's option labels, newest first. */
function railLabels(): string[] {
  const sel = screen.queryByRole('combobox', { name: 'Version' })
  if (!sel) return []
  return [...sel.querySelectorAll('option')].map((o) => o.textContent ?? '')
}

/** The gateway's hint that an artifact was written: what the store's change signal sends. */
const WRITTEN: WsMessage = { type: 'refresh', data: { kinds: ['artifacts'] } }

async function mountViewer() {
  render(<ArtifactViewer slug={SLUG} defaultDetailsOpen onChanged={vi.fn()}
    onDeleted={() => {}} onOpenSourceFile={() => {}} />)
  await waitFor(() => expect(screen.queryByTestId('preview')).not.toBeNull())
}

describe('the gateway saying an artifact was written refreshes the open detail view', () => {
  it('grows the version rail N→N+1 and repaints the preview in place', async () => {
    await mountViewer()

    const before = railLabels()
    // VACUITY FLOOR — the rail must already carry the current version AND a prior
    // one. Without this, a rail that failed to render at all (0 options) would make
    // the growth assertion below pass on 0→1.
    expect(before, 'the rail must be non-empty BEFORE the frame').toEqual(['Current · v2', 'v1'])
    const paintedBefore = screen.getByTestId('preview')
    expect(paintedBefore.textContent).toBe(V2_BODY)
    const fetchesBefore = fetches.artifact

    // The agent, inside the embedded panel, wrote a new version; the store then says so.
    current = { version: 3, content: V3_BODY }
    versions = [1, 2, 3]
    await act(async () => { deliver(WRITTEN) })

    const after = railLabels()
    expect(after.length).toBe(before.length + 1)
    expect(after).toEqual(['Current · v3', 'v2', 'v1'])
    // The refetch happened because of the frame, not because something remounted.
    expect(fetches.artifact).toBe(fetchesBefore + 1)

    // "the preview updates without a reload": the SAME DOM node now carries the new
    // body. A remount (which is what a reload looks like from here) would hand back a
    // different element.
    const paintedAfter = screen.getByTestId('preview')
    expect(paintedAfter.textContent).toBe(V3_BODY)
    expect(paintedAfter, 'the preview repainted in place, not by remounting').toBe(paintedBefore)

    // 🪤 A SECOND ITERATION IS WHAT MAKES THIS ASSERTION REAL. The rail's options are
    // "Current · v<art.version>" plus the version LIST minus the current one — so after
    // ONE update a rail that never refetched the list still reads correctly (v3 simply
    // moves out of "Current"). Only the second update separates them: a stale list
    // would drop v3 entirely and show Current · v4 / v2 / v1.
    current = { version: 4, content: 'chart: revenue, margin AND headcount' }
    versions = [1, 2, 3, 4]
    await act(async () => { deliver(WRITTEN) })
    expect(railLabels()).toEqual(['Current · v4', 'v3', 'v2', 'v1'])
  })

  it('does not read on the tool call that ASKS for the write, which comes before it', async () => {
    await mountViewer()
    expect(railLabels()).toEqual(['Current · v2', 'v1'])  // vacuity floor
    const fetchesBefore = fetches.artifact

    // The model asks for `artifact_update`; nothing is written until it is allowed and runs.
    await act(async () => {
      deliver({ type: 'tool_call', data: { session: 'sess-1', tool: 'artifact_update', input: { slug: SLUG, content: V3_BODY } } })
    })
    expect(fetches.artifact, 'a read now could only see the old version').toBe(fetchesBefore)

    // …and when the write lands, the store's hint is what brings it.
    current = { version: 3, content: V3_BODY }
    versions = [1, 2, 3]
    await act(async () => { deliver(WRITTEN) })
    expect(railLabels()).toEqual(['Current · v3', 'v2', 'v1'])
    expect(screen.getByTestId('preview').textContent).toBe(V3_BODY)
  })

  it('ignores a refresh hint for something other than artifacts', async () => {
    await mountViewer()
    expect(railLabels()).toEqual(['Current · v2', 'v1'])  // vacuity floor

    current = { version: 3, content: V3_BODY }
    versions = [1, 2, 3]
    await act(async () => { deliver({ type: 'refresh', data: { kinds: ['crons', 'workflow_runs'] } }) })

    expect(railLabels()).toEqual(['Current · v2', 'v1'])
    expect(screen.getByTestId('preview').textContent).toBe(V2_BODY)
  })
})

describe('a failed side-fetch is an error, never an empty state', () => {
  // This surface shipped both of these as `.catch(() => [])`, so an unreachable rail
  // rendered as "this artifact has no history" — a false statement about the user's
  // own data. Both halves are asserted: the error state, and the genuinely-empty
  // state it must NOT be confused with.
  it('says the version history could not be LOADED (and offers no picker)', async () => {
    versionsFail = 'HTTP 503 versions'
    await mountViewer()

    expect(screen.getByText(/Couldn't load version history/)).toBeTruthy()
    expect(screen.getByText(/HTTP 503 versions/)).toBeTruthy()
    // No picker: a lone "Current · v2" would read as a healthy one-version artifact.
    expect(screen.queryByRole('combobox', { name: 'Version' })).toBeNull()
    expect(screen.queryByText('No version history.')).toBeNull()
  })

  it('says there is NO version history when the fetch succeeds and is empty', async () => {
    versions = []
    await mountViewer()

    expect(screen.getByText('No version history.')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load version history/)).toBeNull()
  })

  it('says the timeline could not be LOADED', async () => {
    eventsFail = 'HTTP 503 events'
    await mountViewer()
    expect(screen.getByText(/Couldn't load the timeline/)).toBeTruthy()
    expect(screen.queryByText('No events.')).toBeNull()
  })

  it('says there are NO events when the fetch succeeds and is empty', async () => {
    events = []
    await mountViewer()
    expect(screen.getByText('No events.')).toBeTruthy()
    expect(screen.queryByText(/Couldn't load the timeline/)).toBeNull()
  })

  it('clears the error once a retry succeeds', async () => {
    versionsFail = 'HTTP 503 versions'
    await mountViewer()
    expect(screen.getByText(/Couldn't load version history/)).toBeTruthy()

    versionsFail = ''
    await act(async () => { deliver(WRITTEN) })
    expect(screen.queryByText(/Couldn't load version history/)).toBeNull()
    expect(railLabels()).toEqual(['Current · v2', 'v1'])
  })
})
