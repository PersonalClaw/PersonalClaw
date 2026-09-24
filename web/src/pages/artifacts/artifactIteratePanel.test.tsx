import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ArtifactIteratePanel, ITERATE_PENDING } from './ArtifactIteratePanel'

// ── AE-10: the chat half of the split view ───────────────────────────────────────────
//
// The panel is deliberately NOT a new chat surface: it stages a session through
// INVESTIGATE-ANYWHERE's `POST /api/investigate {kind:'artifact'}` (AE-7's resolver —
// fenced current content, `agent` task mode, slug-naming opening prompt) and renders it
// with the SDK's `ChatEmbed`. So the two things worth pinning are (a) it asks the
// resolver rather than composing context client-side, and (b) a failure to stage is its
// own state — a spinner that never resolves reads as a chat that simply never arrived.
//
// 🪤 REPORTING THE SESSION KEY BACK UP RE-RENDERS THE PANEL (the host writes it to
// `?iterate`, which is this surface's own doctrine — selection state IS the URL). Without
// a latch that second render stages a SECOND session, and the user's first turn lands in
// an abandoned thread. Asserted by call count, not by eye.

const investigate = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, investigate: (body: unknown) => investigate(body) } }
})

beforeEach(() => { investigate.mockReset() })

/** The ChatEmbed iframe's src, or '' when no embed is mounted. */
function embedSrc(): string {
  const f = screen.queryByTitle('PersonalClaw chat')
  return f ? (f as HTMLIFrameElement).getAttribute('src') ?? '' : ''
}

function mount(session = ITERATE_PENDING, onSession = vi.fn(), onClose = vi.fn()) {
  const view = render(<ArtifactIteratePanel slug="revenue-widget" name="Revenue widget"
    session={session} onSession={onSession} onClose={onClose} />)
  return { view, onSession, onClose }
}

describe('the iterate panel stages its session through the investigate resolver', () => {
  it('asks the resolver for an artifact session and embeds it, seeded with the prompt', async () => {
    investigate.mockResolvedValue({
      session_key: 'sess-42',
      context: { opening_prompt: 'Iterate on artifact `revenue-widget`.' },
    })
    const { onSession } = mount()

    await waitFor(() => expect(embedSrc()).not.toBe(''))
    expect(investigate).toHaveBeenCalledWith({
      kind: 'artifact', id: 'revenue-widget', back_link: '#/artifacts/revenue-widget',
    })
    const src = embedSrc()
    expect(src).toContain('/#/chat/sess-42?')
    expect(src).toContain('embed=1')
    // The opening prompt PRE-FILLS the composer; the user fires the first turn.
    const qs = new URLSearchParams(src.split('?')[1] ?? '')
    expect(qs.get('seed')).toBe('Iterate on artifact `revenue-widget`.')
    // The host is told the key so it can deep-link the open thread.
    expect(onSession).toHaveBeenCalledWith('sess-42')
  })

  it('stages exactly ONE session even after the host rewrites the session prop', async () => {
    investigate.mockResolvedValue({ session_key: 'sess-42', context: { opening_prompt: 'go' } })
    const { view } = mount()
    await waitFor(() => expect(embedSrc()).not.toBe(''))

    // What the host does with onSession: `?iterate=new` → `?iterate=sess-42`.
    view.rerender(<ArtifactIteratePanel slug="revenue-widget" name="Revenue widget"
      session="sess-42" onSession={vi.fn()} onClose={vi.fn()} />)
    await act(async () => {})

    expect(investigate).toHaveBeenCalledTimes(1)
    expect(embedSrc()).toContain('/#/chat/sess-42?')
  })

  it('resumes a deep-linked session without staging a new one', async () => {
    mount('sess-earlier')
    await act(async () => {})
    expect(investigate).not.toHaveBeenCalled()
    expect(embedSrc()).toContain('/#/chat/sess-earlier?')
  })

  it('names itself after the artifact so two panels never announce identically', async () => {
    investigate.mockResolvedValue({ session_key: 's', context: {} })
    mount()
    await act(async () => {})
    expect(screen.getByRole('complementary', { name: 'Iterate with agent: Revenue widget' })).toBeTruthy()
  })

  it('is closable', async () => {
    investigate.mockResolvedValue({ session_key: 's', context: {} })
    const { onClose } = mount()
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /Close the iterate panel/i }))
    expect(onClose).toHaveBeenCalled()
  })
})

describe('the panel fills its column (a SOURCE rail — jsdom has no layout)', () => {
  // MEASURED DEFECT, found by driving a real browser at 1440px and NOT visible to any
  // test in this file: with no `flex-1` on the aside, its height is its CONTENT height,
  // the embed's `height: 100%` resolves against nothing, and an iframe with no resolved
  // height falls back to its 150px intrinsic default. The panel rendered as a 150px
  // letterbox (measured 479×150; after the fix, 479×801.5). jsdom computes no layout, so
  // this is pinned at the source — the same reason `contentSurfaceTransient` is a source
  // rail.
  const source = readFileSync(join(process.cwd(), 'src/pages/artifacts/ArtifactIteratePanel.tsx'), 'utf8')
  const m = source.match(/<aside[\s\S]{0,400}?className="([^"]*)"/)

  it('found the aside and its class string (not vacuous)', () => {
    expect(m, 'no <aside … className> in the panel source — the rail below would assert nothing').not.toBeNull()
    expect(m![1].length).toBeGreaterThan(10)
  })

  it('the aside grows into its track and is allowed to shrink', () => {
    const classes = m![1]
    expect(classes, `aside classes: ${classes}`).toContain('flex-1')
    expect(classes, `aside classes: ${classes}`).toContain('min-h-0')
  })
})

describe('a failure to stage is its own state, not an endless spinner', () => {
  it('names the failure, offers a retry, and mounts no embed', async () => {
    investigate.mockRejectedValueOnce(new Error('HTTP 500 investigate'))
    mount()

    await waitFor(() => expect(screen.queryByText(/Couldn't open an iteration session/)).not.toBeNull())
    expect(screen.getByText(/HTTP 500 investigate/)).toBeTruthy()
    expect(embedSrc()).toBe('')
    // A "Loading …" line here would be the swallowed-error-as-empty-state shape.
    expect(screen.queryByText(/Loading the iteration session/)).toBeNull()
  })

  it('recovers on retry', async () => {
    investigate.mockRejectedValueOnce(new Error('HTTP 500 investigate'))
    mount()
    await waitFor(() => expect(screen.queryByText(/Couldn't open an iteration session/)).not.toBeNull())

    investigate.mockResolvedValue({ session_key: 'sess-later', context: { opening_prompt: 'go' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /Try again/i })) })

    expect(screen.queryByText(/Couldn't open an iteration session/)).toBeNull()
    expect(embedSrc()).toContain('/#/chat/sess-later?')
  })
})
