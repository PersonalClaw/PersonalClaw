import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import { CompanionPage } from './CompanionPage'
import { invalidateKeys } from '../../lib/data'
import type { PendingApproval, PushStatus } from '../../lib/api'

// ── `#/companion?approval=<id>` — where a push tap lands (MOBILE-COMPANION MC-5 / T3.4) ──
//
// The milestone is "a locked phone rings, you tap, and THE CARD YOU WERE PINGED ABOUT is in
// front of you". A tap that lands on an unsorted queue is a different, worse product: the
// user scans while the run stays blocked, which is the latency this whole atom exists to
// remove.
//
// Two halves, and the second is the one that gets forgotten:
//   1. the addressed card is found, scrolled to, focused and visibly marked;
//   2. an id that is NO LONGER pending SAYS SO. An approval can time out while the phone is
//      in a pocket, or be answered at the desk. Rendering the ordinary queue with no
//      explanation reads as "the notification opened the wrong thing", and the user goes
//      hunting for a card that does not exist.
//
// Focus lands on the CARD, not on Allow. A phone that just woke up in a pocket with focus
// on an irreversible grant is one stray Enter from an unintended approval.

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const approvals = vi.fn()
const pushStatus = vi.fn()
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      approvals: () => approvals(),
      resolveApproval: vi.fn().mockResolvedValue({ ok: true }),
      pushStatus: () => pushStatus(),
    },
  }
})

const AP = (id: string, tool: string): PendingApproval => ({
  id, source: 'cron', tool, tool_input: `run ${tool}`, tool_purpose: '', session: '', ts: 0,
})

const OFF: PushStatus = {
  backend: 'none', vapid_public_key: '', vapid_ready: false,
  ntfy_configured: false, approval_targeted: true, devices: [], subscribed: 0,
}

const routeWith = (query: Record<string, string>) => ({
  sub: '', navigate: vi.fn(), navEpoch: 0, query, setQuery: vi.fn(),
})

beforeEach(() => {
  approvals.mockReset()
  pushStatus.mockReset().mockResolvedValue(OFF)
  invalidateKeys('companion:approvals')
  invalidateKeys('companion:push')
  sessionStorage.clear()
})
afterEach(cleanup)

describe('the push deep link focuses the addressed approval', () => {
  it('focuses and marks the card named in ?approval, not the first one', async () => {
    approvals.mockResolvedValue([AP('ap-1', 'Bash'), AP('ap-2', 'WebFetch'), AP('ap-3', 'Write')])
    render(<CompanionPage {...routeWith({ approval: 'ap-2' })} />)

    const target = await screen.findByRole('group', { name: 'Permission needed to run WebFetch' })
    // The focused element is the card's own wrapper, and the card inside it is the ADDRESSED
    // one — asserted by containment rather than by an id attribute, so the DOM can change
    // shape without the rail going quietly vacuous.
    await waitFor(() => {
      const active = document.activeElement as HTMLElement | null
      expect(active, 'nothing received focus').toBeTruthy()
      expect(active!.contains(target), 'focus did not land on the addressed card').toBe(true)
    })
    // Visibly marked as well as focused: a focus ring alone is invisible to a touch user,
    // who is exactly who arrived here.
    const marked = document.activeElement as HTMLElement
    expect(marked.className).toContain('ring-2')

    // And the OTHER cards are neither focused nor marked — otherwise "the right card is
    // focused" would be true of a build that marked all three.
    const bash = screen.getByRole('group', { name: 'Permission needed to run Bash' })
    expect(marked.contains(bash)).toBe(false)
    const rings = document.querySelectorAll('.ring-2')
    expect(rings.length).toBe(1)
  })

  it('does not focus anything when no ?approval is present', async () => {
    // The vacuity floor for the test above. Without it, a build that focused the first card
    // unconditionally would pass the "focus landed on ap-2" assertion whenever ap-2 was first.
    approvals.mockResolvedValue([AP('ap-1', 'Bash'), AP('ap-2', 'WebFetch')])
    render(<CompanionPage {...routeWith({})} />)
    await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    expect(document.querySelectorAll('.ring-2').length).toBe(0)
    expect(document.activeElement).toBe(document.body)
  })

  it('scrolls the addressed card into view', async () => {
    // A card focused below the fold on a phone is a card the user cannot see.
    const scrolls: unknown[] = []
    const original = Element.prototype.scrollIntoView
    Element.prototype.scrollIntoView = function (arg?: unknown) { scrolls.push(arg) }
    try {
      approvals.mockResolvedValue([AP('ap-1', 'Bash'), AP('ap-2', 'WebFetch')])
      render(<CompanionPage {...routeWith({ approval: 'ap-2' })} />)
      await screen.findByRole('group', { name: 'Permission needed to run WebFetch' })
      await waitFor(() => expect(scrolls.length).toBeGreaterThan(0))
      expect(scrolls[0]).toMatchObject({ block: 'center' })
    } finally {
      Element.prototype.scrollIntoView = original
    }
  })

  it('SAYS SO when the addressed approval is no longer pending', async () => {
    approvals.mockResolvedValue([AP('ap-1', 'Bash')])
    render(<CompanionPage {...routeWith({ approval: 'ap-GONE' })} />)
    // Queried by TEXT, then its role checked. `findByRole('status')` alone matches
    // `ListSkeleton`, which is also a status region — so a role query here would have
    // asserted "the loading skeleton exists" and passed on a build with no notice at all.
    const notice = await screen.findByText(/isn’t waiting anymore/)
    expect(notice.getAttribute('role')).toBe('status')
    expect(document.querySelectorAll('.ring-2').length).toBe(0)
  })

  it('says it on an EMPTY queue too, instead of only "nothing waiting on you"', async () => {
    // The commonest real case: the push fired, the run timed out to a denial while the phone
    // was locked, and the queue is now empty. "Nothing waiting on you" alone would leave the
    // user believing the notification was spurious.
    approvals.mockResolvedValue([])
    render(<CompanionPage {...routeWith({ approval: 'ap-GONE' })} />)
    expect(await screen.findByText('Nothing waiting on you')).toBeTruthy()
    expect(await screen.findByText(/isn’t waiting anymore/)).toBeTruthy()
  })

  it('stays silent while the queue is still loading', async () => {
    // A "no longer waiting" line rendered before the first fetch resolved would be a lie the
    // page could not possibly know yet.
    let release: (v: PendingApproval[]) => void = () => {}
    approvals.mockReturnValue(new Promise<PendingApproval[]>((r) => { release = r }))
    render(<CompanionPage {...routeWith({ approval: 'ap-2' })} />)
    expect(screen.queryByText(/isn’t waiting anymore/)).toBeNull()
    release([AP('ap-2', 'WebFetch')])
    await screen.findByRole('group', { name: 'Permission needed to run WebFetch' })
    expect(screen.queryByText(/isn’t waiting anymore/)).toBeNull()
  })
})

describe('the per-device push control', () => {
  it('points at Settings when push is switched off gateway-wide', async () => {
    approvals.mockResolvedValue([])
    render(<CompanionPage {...routeWith({})} />)
    expect(await screen.findByText('Push is switched off for this gateway.')).toBeTruthy()
  })

  it('names the missing keypair rather than offering a button that cannot work', async () => {
    pushStatus.mockResolvedValue({ ...OFF, backend: 'webpush' })
    approvals.mockResolvedValue([])
    render(<CompanionPage {...routeWith({})} />)
    expect(await screen.findByText(/personalclaw push init/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /turn on push/i })).toBeNull()
  })

  it('says push is ON for a device that is already subscribed', async () => {
    // The device id is minted into localStorage on first read, so seed the same key the
    // client uses rather than guessing a value.
    const { pushDeviceId } = await import('../../app/pushClient')
    const id = pushDeviceId()
    pushStatus.mockResolvedValue({
      ...OFF, backend: 'webpush', vapid_ready: true, vapid_public_key: 'k', devices: [id], subscribed: 1,
    })
    approvals.mockResolvedValue([])
    render(<CompanionPage {...routeWith({})} />)
    expect(await screen.findByText('Push is on for this device.')).toBeTruthy()
  })

  it('tells an ntfy user there is nothing to do on the device', async () => {
    pushStatus.mockResolvedValue({ ...OFF, backend: 'ntfy', ntfy_configured: true })
    approvals.mockResolvedValue([])
    render(<CompanionPage {...routeWith({})} />)
    expect(await screen.findByText(/Pings go to your ntfy topic\. Nothing to set up/)).toBeTruthy()
  })

  it('names the SUBSCRIBED-BUT-UNROUTED state instead of looking healthy', async () => {
    // 🪤 The two-switch trap, made visible. Turning push on writes the `approval/requested`
    // rule for a user who never set one, but it never OVERRIDES a rule they did set — so a
    // user who once turned approval pushes off has a subscribed device that stays silent.
    // Saying "Push is on" and nothing else would be the exact lie this row exists to avoid.
    const { pushDeviceId } = await import('../../app/pushClient')
    pushStatus.mockResolvedValue({
      ...OFF, backend: 'webpush', vapid_ready: true, vapid_public_key: 'k',
      devices: [pushDeviceId()], subscribed: 1, approval_targeted: false,
    })
    approvals.mockResolvedValue([])
    render(<CompanionPage {...routeWith({})} />)
    const line = await screen.findByText(/approvals are not routed to it/)
    expect(line.textContent).toContain('Settings → Notifications')
  })

  it('names an ntfy backend with no topic configured, instead of looking healthy', async () => {
    pushStatus.mockResolvedValue({ ...OFF, backend: 'ntfy', ntfy_configured: false })
    approvals.mockResolvedValue([])
    render(<CompanionPage {...routeWith({})} />)
    expect(await screen.findByText(/no topic URL is configured/)).toBeTruthy()
  })
})
