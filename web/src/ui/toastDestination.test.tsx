import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { Toaster } from './Toaster'

// ── The toast HOST renders the destination (#258) ─────────────────────────────────────────────────
//
// 🪤 A DISPATCHED `href` THAT THE HOST DROPS IS AN INERT SURFACE. `useApprovalToasts` is tested on
// the detail it EMITS, which would pass verbatim with this host ignoring the field — a toast that
// carries a destination nobody can click is the same false assurance as the 404 key it replaced,
// one layer down. So these legs assert the REACHABLE control: an anchor in the a11y tree, with
// that href, named so a screen-reader user knows where it goes.
//
// The message text itself is `aria-hidden` here (the host's live regions own the announcement), so
// the link's own accessible name is the only place the destination reaches the tree — which is why
// it is asserted rather than assumed.

vi.mock('../design/soundCues', () => ({
  playCue: () => {}, armCueAudio: () => {}, soundCuesEnabled: () => false, setSoundCuesEnabled: () => {},
}))

function toast(detail: Record<string, unknown>) {
  render(<Toaster />)
  act(() => { window.dispatchEvent(new CustomEvent('ne:toast', { detail })) })
}

beforeEach(() => { cleanup() })

describe('a toast that names a destination gives the user a way to get there', () => {
  it('renders a link to the hash route, named by its label', () => {
    toast({
      level: 'info',
      message: 'A subagent needs approval to run subagent_run(…) — open the synthesize step of workflow run 11b9a34c to respond.',
      href: '#/workflows/runs/11b9a34c?node=synthesize',
      hrefLabel: 'Open the workflow run',
    })
    const link = screen.getByRole('link', { name: /Open the workflow run/ })
    expect(link.getAttribute('href')).toBe('#/workflows/runs/11b9a34c?node=synthesize')
    // In-app, so no new tab and no off-app rel — a hash route opened in a new context loses the app.
    expect(link.getAttribute('target')).toBeNull()
  })

  it('🪤 VACUITY: a toast with no destination renders no link', () => {
    // Without this, a host that rendered an anchor unconditionally (to "" or to the message) would
    // satisfy the leg above.
    toast({ level: 'success', message: 'Saved.' })
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('refuses a destination that is not an in-app hash route', () => {
    // `ne:toast` is a GLOBAL event any contributed app can fire through the SDK's notify, so the
    // host decides what a toast may link to. An off-app or `javascript:` target gets no link at
    // all rather than one this surface renders on an app's behalf.
    for (const href of ['https://example.com/', 'javascript:alert(1)', '/absolute', 'chat/main']) {
      cleanup()
      toast({ level: 'info', message: 'Something happened', href, hrefLabel: 'Open' })
      expect(screen.queryByRole('link'), href).toBeNull()
    }
  })

  it('still announces the message itself — the link is an addition, not the announcement', () => {
    toast({ level: 'info', message: 'Loop stalled — needs an answer', href: '#/loops', hrefLabel: 'Open loops' })
    const live = document.querySelector('[role="status"][aria-live="polite"]')
    expect(live?.textContent).toContain('Loop stalled — needs an answer')
  })
})
