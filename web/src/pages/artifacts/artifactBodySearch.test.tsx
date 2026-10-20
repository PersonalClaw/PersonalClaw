import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { Artifact } from '../../lib/api'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'
import { ArtifactsSection } from './ArtifactsSection'

const TOKEN = 'heliotrope'
const target = {
  slug: 'quarterly-report',
  name: 'Quarterly report',
  description: 'Revenue summary',
  tags: ['finance'],
  collection: 'Board packets',
  kind: 'markdown',
  source: 'chat',
  version: 1,
  created_at: '2026-09-19T00:00:00Z',
  updated_at: '2026-09-19T00:00:00Z',
} as Artifact
const decoy = {
  ...target,
  slug: 'meeting-notes',
  name: 'Meeting notes',
  description: 'Action items',
  tags: ['operations'],
  collection: 'Team notes',
} as Artifact

const mocks = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
  getArtifact: vi.fn(),
}))

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifacts: mocks.listArtifacts,
      artifact: mocks.getArtifact,
      deployedArtifacts: async () => [],
    },
  }
})

registerBuiltinContentTypes()

beforeEach(() => {
  mocks.listArtifacts.mockReset()
  mocks.getArtifact.mockReset()
  mocks.listArtifacts.mockImplementation(async (filters?: { q?: string }) =>
    filters?.q === TOKEN ? [target] : [target, decoy])
  mocks.getArtifact.mockImplementation(async (slug: string) => ({
    ...(slug === target.slug ? target : decoy),
    content: slug === target.slug ? `The launch codename is ${TOKEN}.` : 'No launch details here.',
  }))
})

describe('artifact library body search (#292)', () => {
  it('asks the server for the query and shows a card whose metadata does not contain it', async () => {
    const metadata = [
      target.name, target.description, ...(target.tags ?? []), target.collection ?? '',
    ].join(' ').toLowerCase()
    expect(metadata, 'the fixture must only match through the body shown in its card')
      .not.toContain(TOKEN)

    render(
      <ArtifactsSection
        sub=""
        navigate={() => {}}
        navEpoch={0}
        query={{ q: TOKEN }}
        setQuery={() => {}}
      />,
    )

    await waitFor(() => expect(mocks.listArtifacts).toHaveBeenCalledWith({ q: TOKEN }))
    expect(await screen.findByRole('button', { name: target.name })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: decoy.name })).toBeNull()
    expect(await screen.findByText(`The launch codename is ${TOKEN}.`)).toBeInTheDocument()
  })

  it('keeps the live region mounted across the query, then announces the count', async () => {
    // 🪤 The regression this pins is invisible to the source-text census in
    // `ui/listResultAnnounce.test.tsx`: gating `active` and wrapping the whole tag in
    // `{!searchPending && …}` both satisfy it. They are NOT equivalent to a screen reader —
    // `ResultAnnouncement` renders its aria-live node unconditionally and only blanks the
    // text, so gating keeps one region that later CHANGES (announced), while unmounting
    // re-inserts a region whose content is already there (not reliably announced). Now that
    // text search is a remote read, every search goes through the in-flight state, so an
    // unmount would silence the count on the page's main path. Asserted as DOM presence
    // rather than as source text, because the two spellings look the same to a grep.
    let release: (rows: Artifact[]) => void = () => {}
    mocks.listArtifacts.mockImplementation((filters?: { q?: string }) =>
      filters?.q === TOKEN
        ? new Promise<Artifact[]>((resolve) => { release = resolve })
        : Promise.resolve([target, decoy]))

    render(
      <ArtifactsSection
        sub=""
        navigate={() => {}}
        navEpoch={0}
        query={{ q: TOKEN }}
        setQuery={() => {}}
      />,
    )

    // In flight: the region exists and is silent (no stale count from the previous query).
    const live = await waitFor(() => {
      const node = document.querySelector('[role="status"][aria-live="polite"]')
      expect(node, 'the live region must survive the in-flight query').not.toBeNull()
      return node as HTMLElement
    })
    await waitFor(() => expect(mocks.listArtifacts).toHaveBeenCalledWith({ q: TOKEN }))
    expect(live.textContent).toBe('')

    release([target])

    // Resolved: the SAME node gained the count — a change a live region announces.
    await waitFor(() => expect(live.textContent).toBe('1 artifact'))
    expect(live.isConnected, 'the same node, not a replacement').toBe(true)
  })
})
