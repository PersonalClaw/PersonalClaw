import { describe, expect, it, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── #258's shape again, on the lane its fix left behind ───────────────────────────────────────────
//
// The sibling rail (`actionCenterApprovalOpens`) fixed the APPROVAL row's open target and records
// why: *"a user who opened the row instead of pressing Approve landed on a 404"*. The inbox lane in
// the same `routeFor` still read:
//
//     if (e.kind === 'inbox') return 'inbox'
//     …
//     else navigate('inbox')  // reply in the detail where the draft editor lives
//
// The comment describes the intent; the call went to the LIST. Measured on a fresh container: a note
// captured through the inbox's own "Capture a note" appeared in this card, its control carrying
// `aria-label="Reply: user — Decide whether the IoT VLAN…"` and `title="Open to reply"`, and pressing
// it produced `#/inbox` with no panel open. The one thing the button names is the one thing it threw
// away — and `?open=<id>` is the inbox's OWN deep link, read by `InboxPage`'s `open` param, with an
// `anchorKey` on its `WindowedList` added specifically so a deep-linked row scrolls into view. So
// nothing was missing except the id.
//
// 🪤 ASSERTING THE ROW RENDERS WOULD PROVE NOTHING — it already rendered; it is where it WENT that
// was wrong. Every leg here reads the argument `navigate` receives.
//
// 🪤 AND THE VACUITY FLOOR IS THE OTHER TWO LANES. Returning an inbox deep link unconditionally would
// satisfy the first legs while sending every approval and every skill proposal in this widget to the
// inbox, so the proposal leg below is load-bearing, not decoration.

const NOTE_ID = 'user_note_0dd0506a_1790235557.755152'
/** A channel-backed id, because Slack/filesystem ids carry `:` and a raw interpolation would emit a
 *  hash whose query the router re-splits. The app's own trigger deep link is already `%3A`-encoded. */
const CHANNEL_ID = 'slack:C07AB12CD:1790235557.755152'

const inboxOpen = vi.fn()
const skillProposals = vi.fn()

function mockApi() {
  vi.resetModules()
  vi.doMock('../../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ items: [] }),
      approvals: () => Promise.resolve([]),
      inboxOpen,
      skillProposals,
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ runs: [], did_ids: [], suppressed: 0 }),
    },
  }))
}

async function mount() {
  cleanup()
  mockApi()
  const navigate = vi.fn()
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const { ActionCenter } = await import('./ActionCenter')
  await act(async () => {
    render(
      <DashboardLiveProvider>
        {/* The full `RouteProps` — `navigate` is the only one these assertions read, but the widget's
            contract is the whole shape and `tsc` is right to insist. */}
        <ActionCenter sub="" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />
      </DashboardLiveProvider>,
    )
    await new Promise((res) => setTimeout(res, 0))
  })
  return navigate
}

/** One pending inbox item, shaped as `/api/inbox/open` returns it. */
function oneItem(id: string, sender = 'user') {
  inboxOpen.mockReset()
  inboxOpen.mockResolvedValue([{
    id, sender_name: sender, channel: 'user', channel_name: 'user', status: 'pending',
    message: 'Decide whether the IoT VLAN gets its own DNS resolver', item_kind: 'user_note',
    classification: 'needs_reply', can_reply: false, created_at: 0,
  }])
  skillProposals.mockReset()
  skillProposals.mockResolvedValue({ proposals: [], lastReview: null })
}

beforeEach(() => { cleanup(); inboxOpen.mockReset(); skillProposals.mockReset() })

describe('an Action Center inbox row opens the ITEM, not the inbox', () => {
  it('the Reply control carries the item into the route it navigates to', async () => {
    oneItem(NOTE_ID)
    const navigate = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Reply:/ }))
    expect(navigate).toHaveBeenCalledWith(`inbox?open=${encodeURIComponent(NOTE_ID)}`)
  })

  it('🪤 and NEVER the bare list — that is the defect, stated directly', async () => {
    oneItem(NOTE_ID)
    const navigate = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Reply:/ }))
    for (const call of navigate.mock.calls) {
      expect(String(call[0]), 'the shipped call was a bare `inbox`').not.toBe('inbox')
    }
  })

  it('the row body and the Reply control reach the SAME place — one derivation', async () => {
    // They were two spellings of one destination, which is how they came to disagree with the
    // comment between them. This leg is what keeps a future edit from re-splitting them.
    // 🪤 STATED PLAINLY: this leg PASSES against the defect, because both spellings were `'inbox'`
    // and therefore agreed. It is a forward guard, not a detector — measured against the reverted
    // file, 3 of these 5 legs red and this is one of the 2 that do not.
    // 🪤 The row's hit target composes its name as `<title> — <subject>`, and the Reply pill's is
    // `Reply: <subject>`; anchoring on `^user — ` is what picks the hit target rather than the pill.
    oneItem(NOTE_ID)
    const viaRow = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /^user — / }))
    oneItem(NOTE_ID)
    const viaReply = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Reply:/ }))
    expect(viaRow.mock.calls[0][0]).toBe(viaReply.mock.calls[0][0])
  })

  it('a channel-backed id is encoded, so its colons cannot re-split the query', async () => {
    oneItem(CHANNEL_ID, 'Ada')
    const navigate = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Reply:/ }))
    const arg = String(navigate.mock.calls[0][0])
    expect(arg).toBe(`inbox?open=${encodeURIComponent(CHANNEL_ID)}`)
    expect(arg, 'a raw `:` would ship un-encoded').not.toContain(':')
  })

  it('🪤 VACUITY: a skill proposal still opens the proposals lens', async () => {
    // Without this, returning the inbox deep link unconditionally would pass every leg above and
    // send both other lanes in this widget to the inbox.
    inboxOpen.mockReset(); inboxOpen.mockResolvedValue([])
    skillProposals.mockReset()
    skillProposals.mockResolvedValue({
      proposals: [{ id: 'sp-1', slug: 'vlan-notes', description: 'Capture VLAN decisions' }],
      lastReview: null,
    })
    const navigate = await mount()
    await userEvent.click(await screen.findByRole('button', { name: /^Skill: vlan-notes/ }))
    expect(navigate).toHaveBeenCalledWith('skills?mode=proposals')
  })
})
