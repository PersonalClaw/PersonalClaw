import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { duration } from '../design/motion'

// ── No page can strand the shell's route fade (the blank Files page) ───────────────────────────
//
// The shell fades each routed page in with a keyed framer-motion element, and the route's
// code-split boundary used to sit AROUND that element. Any lazy component deeper in a page that
// suspended after the page had painted then made React hide the element (`display: none
// !important`) and detach its ref. On the reveal framer-motion re-mounts it and jumps its values
// back to `initial` to replay the entrance — but that replay runs from its effect after a RENDER,
// and a Suspense reveal re-renders nothing there. Measured live on `#/files`: the page came back
// pinned at `opacity: 0; transform: translateY(6px)`, header and explorer invisible, until some
// unrelated navigation re-rendered the shell. The first preview of each file type did it.
//
// ContentSurface's preview now has its own boundary (`ui/content/previewSuspendsLocally.test.tsx`),
// but that fixes one caller. THIS is the class: a probe page with an unbounded lazy child, in the
// REAL shell, stands in for any page a future lazy import lands in.
//
// 🪤 WHAT IS ASSERTED IS OPACITY, not presence. The stranded page was fully in the DOM, focusable
// and clickable — `getByRole`, and Playwright's own `toBeVisible()`, both pass on it. What was wrong
// was what the user SEES, so every ancestor's computed opacity is multiplied and must reach 1.

vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ status: 'ready', name: 'Ada', username: 'ada', onboarded: true, setName: async () => {} }),
  }
})
// Every gateway read resolves empty; the envelope-shaped reads are named (see navDisclosure.test).
const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
}
vi.mock('../lib/api', async (orig) => {
  const real = await orig<typeof import('../lib/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : []),
  })
  return { ...real, api: stub }
})

// The probe: a routed page (mounted at `#/tools`) that renders a lazy part on demand, with no
// boundary of its own — so the part's suspension lands on the route's.
const probe = vi.hoisted(() => {
  let arrive = () => {}
  const chunk = new Promise<void>((resolve) => { arrive = resolve })
  return { chunk, arrive: () => arrive() }
})
vi.mock('../pages/tools/ToolsPage', async () => {
  const { createElement: h, lazy, useState } = await import('react')
  const LatePart = lazy(async () => {
    await probe.chunk
    return { default: () => h('p', null, 'the late part') }
  })
  function ToolsPage() {
    const [open, setOpen] = useState(false)
    return h('div', null,
      h('h1', null, 'Probe page'),
      h('button', { type: 'button', onClick: () => setOpen(true) }, 'Load the late part'),
      open ? h(LatePart) : null)
  }
  return { ToolsPage }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')
// The route's own chunk, resolved up front so the only suspension in the test is the late part.
await import('../pages/tools/ToolsPage')

/** What the user sees of `el`: every ancestor's computed opacity, multiplied. */
function effectiveOpacity(el: Element): number {
  let opacity = 1
  for (let node: Element | null = el; node; node = node.parentElement) {
    const own = getComputedStyle(node).opacity
    opacity *= own === '' ? 1 : Number(own)
  }
  return opacity
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/tools'
})
afterEach(cleanup)

describe('the route fade survives a page that suspends after it painted', () => {
  it('brings the page back fully visible once the late part loads', async () => {
    render(<ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>)
    const heading = await screen.findByRole('heading', { name: 'Probe page' })
    // The failure needs a page that had PAINTED: let its entrance play out first.
    await waitFor(() => expect(effectiveOpacity(heading)).toBe(1))

    await userEvent.click(screen.getByRole('button', { name: 'Load the late part' }))
    await act(async () => { probe.arrive() })
    await screen.findByText('the late part')
    // 🪤 NOT a `waitFor`. The strand is written a frame AFTER the reveal commits, so a poll that
    // starts at once can read the pre-reveal `1` and pass on the broken tree. Wait out two full
    // entrances instead, then read the frame the user is left with.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, duration.medium * 2000)) })

    const after = screen.getByRole('heading', { name: 'Probe page' })
    expect(
      effectiveOpacity(after),
      'the page is back in the DOM but invisible — the route fade was stranded at its initial frame',
    ).toBe(1)
    expect(after).toBeVisible()
  })
})
