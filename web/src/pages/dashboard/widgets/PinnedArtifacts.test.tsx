import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Artifact, PinnedArtifact } from '../../../lib/api'
import type { RouteProps } from '../../../app/useQueryState'
import { PinnedArtifacts } from './PinnedArtifacts'

// ── WORK-CONTAINERS §6.5d (WF2WOR-7): pin-to-dashboard, adapted to reality ──
//
// There is NO tile registry — the bento grid and per-user layout persistence were deliberately
// retired. So a pin is a slug in a list that THIS one hard-imported widget renders. The property
// that matters most is that a pin holds a REFERENCE: the name comes from the artifact on every
// load, so a rename shows through and a deleted artifact drops off instead of leaving a dead card.

let pins: PinnedArtifact[]
let artifacts: Artifact[]
const unpinned: string[] = []

vi.mock('../../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      pinnedArtifacts: async () => ({ pins }),
      artifacts: async () => artifacts,
      pinArtifact: async (slug: string, pinned: boolean) => {
        if (!pinned) unpinned.push(slug)
        return { ok: true, pinned, pins }
      },
    },
  }
})

function art(over: Partial<Artifact> = {}): Artifact {
  return {
    slug: 'report', name: 'Weekly report', kind: 'markdown', source: 'workflow',
    description: '', tags: [], version: 3, created_at: '', updated_at: '',
    events: [], source_path: '', live_dirty: false, mime: '', project_id: '', collection: '',
    ...over,
  } as Artifact
}

const route: RouteProps = {
  sub: '', navigate: () => {}, navEpoch: 0, query: {}, setQuery: () => {},
}

beforeEach(() => {
  pins = [{ slug: 'report', pinned_at: '2026-08-11T02:00:00+00:00', run_id: 'r1' }]
  artifacts = [art()]
  unpinned.length = 0
})

describe('a pin renders the artifact, resolved at load', () => {
  it('shows the CURRENT name and version, not a stored copy', async () => {
    // A denormalized title would go stale on the next rename; a card that is confidently wrong is
    // worse than one that is absent.
    artifacts = [art({ name: 'Renamed report', version: 7 })]
    render(<PinnedArtifacts {...route} />)
    expect(await screen.findByText('Renamed report')).toBeTruthy()
    expect(screen.getByText(/v7/)).toBeTruthy()
  })

  it('omits the version suffix for a v1 artifact', async () => {
    artifacts = [art({ version: 1 })]
    render(<PinnedArtifacts {...route} />)
    await screen.findByText('Weekly report')
    expect(screen.queryByText(/v1/)).toBeNull()
  })
})

describe('a pin whose artifact is gone', () => {
  it('drops off the surface rather than rendering a dead card', async () => {
    artifacts = []
    render(<PinnedArtifacts {...route} />)
    // The empty state, not a row that navigates nowhere.
    expect(await screen.findByText(/no pinned artifacts/i)).toBeTruthy()
  })

  it('does NOT delete the pin from the store', async () => {
    // The artifact could be a provider read that is briefly unavailable. Silently discarding a
    // user's pin because one list read came back short would be worse than hiding the row.
    artifacts = []
    render(<PinnedArtifacts {...route} />)
    await screen.findByText(/no pinned artifacts/i)
    expect(unpinned).toEqual([])
  })
})

describe('unpinning', () => {
  it('removes the row and writes through', async () => {
    render(<PinnedArtifacts {...route} />)
    fireEvent.click(await screen.findByTitle('Unpin'))
    await waitFor(() => expect(unpinned).toEqual(['report']))
    // Optimistic: a bookmark is cheap and reversible, so the interaction should feel instant.
    expect(screen.queryByText('Weekly report')).toBeNull()
  })
})

describe('the empty state', () => {
  it('says how to get here rather than just being blank', async () => {
    pins = []
    render(<PinnedArtifacts {...route} />)
    expect(await screen.findByText(/pin one from its page/i)).toBeTruthy()
  })

  it('renders nothing at all before the first load resolves', () => {
    // Not an empty state during load: flashing "no pinned artifacts" before the data arrives
    // would tell the user something false.
    const { container } = render(<PinnedArtifacts {...route} />)
    expect(container.textContent).toBe('')
  })
})
