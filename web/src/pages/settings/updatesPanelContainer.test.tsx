import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import type { UpdateCheck } from '../../lib/api'

// ── The container Updates panel renders the channel/pin-resolved tag ──────────────────────
//
// The backend (build_update_status) now emits `instructions` for the resolved channel/pin's
// tag (here a Compose install's, carried on `PERSONALCLAW_IMAGE_TAG=<tag>`). The panel must render
// THOSE commands verbatim — not a hard-coded `latest` — and, on a pin that matches no release,
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

describe('UpdatesPanel container commands', () => {
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

  it('a pin set back to an older release shows the rollback commands, as `personalclaw update` prints them', async () => {
    // What the server sends: nothing newer, so not `available`; the pin names an OLDER release,
    // so `pin_older`, and `instructions` carry the pull of that exact release.
    const { container } = mountWith({
      ...BASE,
      available: false,
      changes: '',
      current: '0.2.0',
      latest: '0.1.3',
      channel: 'stable',
      pin: '0.1.3',
      pin_older: true,
      image_tag: '0.1.3',
      instructions: [
        'PERSONALCLAW_IMAGE_TAG=0.1.3 docker compose -f deploy/compose/compose.yaml pull',
        'PERSONALCLAW_IMAGE_TAG=0.1.3 docker compose -f deploy/compose/compose.yaml up -d',
      ],
    })
    await waitFor(() => expect(container.textContent).toContain('Pinned to v0.1.3, older than this build (v0.2.0)'))
    const text = container.textContent ?? ''
    expect(text).not.toContain('Up to date')
    const commands = container.querySelector('[aria-label="Rollback commands"]')
    expect(commands, 'the rollback commands are shown, not hidden behind `available`').not.toBeNull()
    expect(commands?.textContent).toContain('PERSONALCLAW_IMAGE_TAG=0.1.3 docker compose -f deploy/compose/compose.yaml pull')
    expect(text).toContain('Roll this container install back by pulling the pinned image and recreating')
  })
})
