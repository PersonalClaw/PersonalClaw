import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { FileText } from 'lucide-react'
import type { Artifact, ArtifactEvent } from '../../lib/api'
import type { WsMessage } from '../../lib/useChatSocket'
import type { RouteProps } from '../../app/useQueryState'
import { registerContentType } from '../../ui/content/contentTypes'
import { ArtifactsSection } from './ArtifactsSection'

// ── AE-10: the split view, composed ──────────────────────────────────────────────────
//
// The two halves pinned separately (`artifactLiveRefresh` / `artifactIteratePanel`) are
// only worth anything TOGETHER: the artifact detail view on the left, the ChatEmbed panel
// on the right, and one socket frame that grows the rail and repaints the preview while
// the conversation stays put.
//
// 🪤 THE OBVIOUS WRONG IMPLEMENTATION refreshes by remounting the detail container. The
// rail and the preview would both look correct — and the chat iframe would be torn down
// and re-created on every version the agent writes, losing the thread the user is in the
// middle of. So the iframe's DOM identity is asserted across the refresh too.

const SLUG = 'revenue-widget'
const V2_BODY = 'chart: revenue only'
const V3_BODY = 'chart: revenue AND margin'

let onMessage: ((m: WsMessage) => void) | null = null
vi.mock('../../lib/useChatSocket', () => ({
  useChatSocket: (cb: (m: WsMessage) => void) => { onMessage = cb },
}))

let current = { version: 2, content: V2_BODY }
let versions: number[] = [1, 2]
let readonlyArtifact = false
const investigate = vi.fn()

function fixture(): Artifact {
  return {
    slug: SLUG, name: 'Revenue widget', kind: 'ae10split', source: 'chat',
    description: '', tags: [], version: current.version, content: current.content,
    readonly: readonlyArtifact,
    created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:00Z',
  } as unknown as Artifact
}

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifacts: async () => [fixture()],
      artifact: async () => fixture(),
      artifactVersions: async () => ({ slug: SLUG, versions }),
      artifactEvents: async () => ({ slug: SLUG, events: [] as ArtifactEvent[] }),
      artifactVersion: async (_s: string, v: number) => ({ ...fixture(), version: v, content: `body of v${v}` }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
      investigate: (body: unknown) => investigate(body),
    },
  }
})

function SplitPreview({ content }: { content: string }) {
  return <div data-testid="preview">{content}</div>
}
registerContentType({
  id: 'ae10split', label: 'Probe', icon: FileText, tone: '#888888',
  kinds: ['ae10split'], preview: { render: SplitPreview }, commentable: false,
})

beforeEach(() => {
  onMessage = null
  current = { version: 2, content: V2_BODY }
  versions = [1, 2]
  readonlyArtifact = false
  investigate.mockReset()
  investigate.mockResolvedValue({ session_key: 'sess-42', context: { opening_prompt: 'Iterate on `revenue-widget`.' } })
})

/** The section under its real URL contract: `query` is state, `setQuery` patches it. */
function Harness() {
  const [query, setQueryState] = useState<Record<string, string>>({})
  const setQuery: RouteProps['setQuery'] = (patch) => {
    setQueryState((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === '') delete next[k]
        else next[k] = v
      }
      return next
    })
  }
  return <ArtifactsSection sub={SLUG} navigate={() => {}} navEpoch={0} query={query} setQuery={setQuery} />
}

function railLabels(): string[] {
  const sel = screen.queryByRole('combobox', { name: 'Version' })
  if (!sel) return []
  return [...sel.querySelectorAll('option')].map((o) => o.textContent ?? '')
}

function embed(): HTMLElement | null {
  return screen.queryByTitle('PersonalClaw chat')
}

async function openDetailsRail() {
  fireEvent.click(screen.getByRole('button', { name: /^Details/ }))
  await waitFor(() => expect(railLabels().length).toBeGreaterThan(0))
}

describe('the iterate panel sits BESIDE the detail view', () => {
  it('opens on the header action, keeps the viewer mounted, and deep-links the session', async () => {
    render(<Harness />)
    await waitFor(() => expect(screen.queryByTestId('preview')).not.toBeNull())
    expect(embed(), 'no panel until asked for').toBeNull()

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Iterate with agent/i })) })
    await waitFor(() => expect(embed()).not.toBeNull())

    // BOTH halves are on screen at once — that is the "split view".
    expect(screen.getByTestId('preview').textContent).toBe(V2_BODY)
    expect(screen.getByRole('complementary', { name: /Iterate with agent: Revenue widget/ })).toBeTruthy()
    // The panel replaced the `new` sentinel with the real key, so the open thread is
    // deep-linkable — and the embed points at that session.
    expect(embed()!.getAttribute('src')).toContain('/#/chat/sess-42?')
    expect(investigate).toHaveBeenCalledTimes(1)
  })

  it('closes again from the header, leaving the detail view intact', async () => {
    render(<Harness />)
    await waitFor(() => expect(screen.queryByTestId('preview')).not.toBeNull())
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Iterate with agent/i })) })
    await waitFor(() => expect(embed()).not.toBeNull())

    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Close iterate/i })) })
    expect(embed()).toBeNull()
    expect(screen.getByTestId('preview').textContent).toBe(V2_BODY)
  })

  it('is not offered on a read-only record (the server refuses every write to it)', async () => {
    readonlyArtifact = true
    render(<Harness />)
    await waitFor(() => expect(screen.queryByTestId('preview')).not.toBeNull())
    expect(screen.queryByRole('button', { name: /Iterate with agent/i })).toBeNull()
  })
})

describe('asking the agent to change the widget in the panel', () => {
  it('lands a new version in the rail AND repaints the preview, without touching the chat', async () => {
    render(<Harness />)
    await waitFor(() => expect(screen.queryByTestId('preview')).not.toBeNull())
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Iterate with agent/i })) })
    await waitFor(() => expect(embed()).not.toBeNull())
    await openDetailsRail()

    const before = railLabels()
    // VACUITY FLOOR — the rail already holds the current version plus a prior one, so
    // the growth assertion below cannot be satisfied by a rail that rendered empty.
    expect(before, 'the rail must be non-empty BEFORE the frame').toEqual(['Current · v2', 'v1'])
    const paintedBefore = screen.getByTestId('preview')
    const embedBefore = embed()
    expect(paintedBefore.textContent).toBe(V2_BODY)

    // The agent, in the panel, calls artifact_update on this slug.
    current = { version: 3, content: V3_BODY }
    versions = [1, 2, 3]
    await act(async () => {
      onMessage!({ type: 'tool_call', data: { session: 'sess-42', tool: 'artifact_update', input: { slug: SLUG, content: V3_BODY } } })
    })

    const after = railLabels()
    expect(after.length).toBe(before.length + 1)
    expect(after).toEqual(['Current · v3', 'v2', 'v1'])
    expect(screen.getByTestId('preview').textContent).toBe(V3_BODY)
    expect(screen.getByTestId('preview'), 'repainted in place').toBe(paintedBefore)
    // The conversation the user is mid-way through survived the refresh.
    expect(embed()).toBe(embedBefore)
    expect(investigate).toHaveBeenCalledTimes(1)
  })
})
