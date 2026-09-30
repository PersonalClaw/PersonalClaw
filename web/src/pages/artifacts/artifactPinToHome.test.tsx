import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Artifact, PinnedArtifact } from '../../lib/api'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'
import { ArtifactsSection } from './ArtifactsSection'

// ── An artifact's page pins it to Home ─────────────────────────────────────────────────────────
//
// Home's "Pinned artifacts" card says "Pin one from its page to keep it here." The route existed
// and so did the card, but no page offered a Pin: the card's one caller of the pin route was its own
// Unpin. The control lives in the artifact's header, and a read-only record takes a pin too — a pin
// is a bookmark on Home, not a write to the artifact.

let readonlyArtifact = false
let pins: PinnedArtifact[] = []
let pinsReadFails = false
const pinCalls: Array<{ slug: string; pinned: boolean }> = []

const artifact = (): Artifact => ({
  slug: 'kitchen-render', name: 'Kitchen render', description: '', tags: [], kind: 'markdown',
  source: 'chat', source_path: '', version: 1, content: '# Kitchen', events: [], readonly: readonlyArtifact,
  created_at: '2026-09-19T00:00:00Z', updated_at: '2026-09-19T00:00:00Z',
} as Artifact)

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      artifacts: async () => [artifact()],
      artifact: async () => artifact(),
      artifactVersions: async () => ({ slug: 'kitchen-render', versions: [1] }),
      artifactEvents: async () => ({ slug: 'kitchen-render', events: [] }),
      viewRender: async () => ({}),
      deployedArtifacts: async () => [],
      pinnedArtifacts: async () => {
        if (pinsReadFails) throw new Error('the pin list is unreadable')
        return { pins }
      },
      pinArtifact: async (slug: string, pinned: boolean) => {
        pinCalls.push({ slug, pinned })
        pins = pinned ? [{ slug, pinned_at: '2026-09-29T00:00:00Z', run_id: '' }] : pins.filter((p) => p.slug !== slug)
        return { ok: true, pinned, pins }
      },
    },
  }
})

registerBuiltinContentTypes()

beforeEach(() => {
  readonlyArtifact = false
  pinsReadFails = false
  pins = []
  pinCalls.length = 0
})

const open = () => render(
  <ArtifactsSection sub="kitchen-render" navigate={vi.fn()} navEpoch={0} query={{}} setQuery={() => {}} />,
)

describe('Pin to Home on the artifact page', () => {
  it('pins the open artifact, and then offers to unpin it', async () => {
    open()
    const pin = await screen.findByRole('button', { name: /Pin to Home/ })
    await act(async () => { fireEvent.click(pin) })
    expect(pinCalls).toEqual([{ slug: 'kitchen-render', pinned: true }])
    const unpin = await screen.findByRole('button', { name: /Unpin from Home/ })
    await act(async () => { fireEvent.click(unpin) })
    expect(pinCalls[1]).toEqual({ slug: 'kitchen-render', pinned: false })
    expect(await screen.findByRole('button', { name: /Pin to Home/ })).toBeTruthy()
  })

  it('reads an existing pin as pinned', async () => {
    pins = [{ slug: 'kitchen-render', pinned_at: '2026-09-28T00:00:00Z', run_id: '' }]
    open()
    expect(await screen.findByRole('button', { name: /Unpin from Home/ })).toBeTruthy()
  })

  it('says the pin list could not be read, and still pins', async () => {
    pinsReadFails = true
    open()
    const pin = await screen.findByRole('button', { name: /Pin to Home/ })
    expect(pin.getAttribute('title')).toContain("Couldn't read your pins")
    expect(pin.getAttribute('title')).not.toContain('Keep this artifact on Home')
    await act(async () => { fireEvent.click(pin) })
    expect(pinCalls).toEqual([{ slug: 'kitchen-render', pinned: true }])
    expect(await screen.findByRole('button', { name: /Unpin from Home/ })).toBeTruthy()
  })

  it('is offered on a read-only record too', async () => {
    readonlyArtifact = true
    open()
    expect(await screen.findByRole('button', { name: /Pin to Home/ })).toBeTruthy()
    await waitFor(() => expect(screen.queryByRole('button', { name: /Iterate with agent/ })).toBeNull())
  })
})
