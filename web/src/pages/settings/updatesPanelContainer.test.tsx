import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import type { UpdateCheck } from '../../lib/api'

// ── RUM-7: the container Updates panel renders the channel/pin-resolved tag ───────────────
//
// The backend (build_update_status) now emits `instructions` carrying
// `PERSONALCLAW_IMAGE_TAG=<tag>` for the resolved channel/pin. The panel must render THOSE
// commands verbatim — not a hard-coded `latest` — and, on a pin that matches no release,
// must say so rather than silently falling back to a bare `latest` pull. Both are things
// only the frontend can get wrong, so they are locked here.
//
// 🪤 The pin-miss case used to be asserted with `available: true` — a payload the server
// cannot send: a pin that names no release resolves nothing, so nothing is available. The
// notice was gated on `available`, so it passed here and was unreachable on every real
// install. It is asserted with the wire's real shape now (`available: false`, `pin_miss`).

// Drive the data layer directly: `useQuery` returns the query result the panel destructures.
const useQuery = vi.fn()
vi.mock('../../lib/data', () => ({
  useQuery: (...a: unknown[]) => useQuery(...a),
  invalidateKeys: vi.fn(),
}))

const { UpdatesPanel } = await import('./UpdatesPanel')

function mountWith(info: UpdateCheck) {
  useQuery.mockReturnValue({ data: { info, changelog: '' }, loading: false, error: null, refresh: vi.fn() })
  return render(<UpdatesPanel />)
}

const BASE: UpdateCheck = { available: true, changes: 'A new version is ready.', checked: true, auto: 'off', kind: 'container' }

describe('UpdatesPanel container commands (RUM-7)', () => {
  it('renders the exact pull + up -d for the resolved image tag', async () => {
    const { container } = mountWith({
      ...BASE,
      channel: 'stable',
      image_tag: '0.2',
      instructions: [
        'PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml pull',
        'PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml up -d',
      ],
    })
    await waitFor(() => expect(container.textContent).toContain('PERSONALCLAW_IMAGE_TAG=0.2'))
    const text = container.textContent ?? ''
    expect(text).toContain('PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml pull')
    expect(text).toContain('PERSONALCLAW_IMAGE_TAG=0.2 docker compose -f deploy/compose/compose.yaml up -d')
    // the commands render inside the labelled command group
    expect(container.querySelector('[aria-label="Update commands"]')).not.toBeNull()
  })

  it('refuses a pin that matches no release instead of offering a bare latest', async () => {
    const { container } = mountWith({
      ...BASE,
      available: false, // what the server sends for a pin-miss: it resolves no release
      latest: '',
      channel: 'stable',
      pin: '9.9.9',
      pin_miss: true,
      image_tag: '',
      instructions: [], // pin-miss: the backend emits no commands
    })
    await waitFor(() => expect(container.textContent).toContain('No release matches pin 9.9.9'))
    const text = container.textContent ?? ''
    expect(text).toContain('Nothing is offered or installed while this pin stands')
    // The whole point of the refusal: NO pull command is offered (no silent `latest`).
    expect(text).not.toContain('docker compose')
    expect(container.querySelector('[aria-label="Update commands"]')).toBeNull()
  })
})
