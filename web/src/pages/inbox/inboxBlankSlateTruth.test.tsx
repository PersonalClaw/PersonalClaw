import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The inbox blank slate called a working inbox unconnected, one inch under its own banner ────────
//
// Two rails have now fixed this element, and each traded one wrong sentence for another because both
// read `disabled = status ? !status.enabled : false` as "no source is connected".
//
//   rail 1 (`narrowedNotBlankSlate`)  hint tested `disabled` before `narrowed`, so a no-match search
//                                     got setup advice.                              fixed, correctly
//   rail 2 (this file, first version) title never tested the second flag at all, so a fresh install
//                                     read "Inbox zero" over "Enable a source to begin".
//                                     Diagnosis right; REPLACEMENT SENTENCE FALSE.
//
// 🔴 `!status.enabled` DOES NOT MEAN "THE INBOX IS NOT CONNECTED". `handlers_inbox.py` publishes
// `enabled` for the POLL providers only, and says so at the site: "the native source is ALWAYS active
// (push-based agent→inbox sink); the poll-based providers run only when cfg.inbox.enabled". That is why
// the same payload carries `native_source_active: true` unconditionally, and why `InboxPage`'s own
// source-health banner renders **"Native source active — agents post here directly"** off it.
//
// Measured on a fresh container (v0.1.3 wheel, nothing configured), `GET /api/inbox/status` → 200
// `{"enabled": false, "native_source_active": true, "open_count": 0, "total_count": 0}`, and `#/inbox`
// rendered, in one viewport:
//
//   banner    "Native source active — agents post here directly. Connect a message source
//              (filesystem/Slack) to collect more."                                     ← it IS connected
//   headline  "Inbox is not connected yet"                                              ← it is NOT
//   body      "… Enable a source to begin."                                             ← you cannot start
//   action    "Connect a source"
//
// Then the header's own **Capture a note** posted an item into that inbox and the heading became
// "Inbox 1 open · 1 total", with `enabled` still `false`. So the page was handed the truth and rendered
// its opposite. Nothing 4xx'd, the console was clean and the gateway log had zero tracebacks — this is
// the #3394/#3396/#3409 family with the swallow removed and the fabrication left in.
//
// 🔑 THE AXIS WAS WRONG, NOT THE FLAG. What a never-used inbox and a cleared one differ on is whether
// anything ever ARRIVED, and `items` answers that from a read (`neverReceived`). Both resulting
// sentences are true, and neither contradicts the banner beside them.
//
// 🪤 THE CTA IS AN OFFER NOW, NOT A DIAGNOSIS. "Add a message source" on an inbox nothing has reached
// is useful; on one that already polls a source it would be wrong, so `pollConnected` gates it. The
// `emptyStateRollout` taxonomy still forbids a CTA under real good news, and "Inbox zero" and a
// no-match filter still get none.
//
// 🪤 KNOWN GAP, RESTATED. The first version recorded that `api.inboxStatus().catch(() => null)` makes
// an unread status resolve to `disabled = false`. `neverReceived` removes that gap from the TITLE
// entirely — it reads `items`, and a failed items read renders `LoadError` before this branch. The only
// thing still keyed to `status` is the CTA, where an unknown status offers the source link. An offer is
// not a claim, so there is nothing to guess at.

const OPEN_ITEM = {
  id: 'i-1', channel: 'agent', channel_name: 'agent', message: 'something to triage',
  sender_id: 'agent', sender_name: 'agent', classification: 'needs_reply', status: 'pending',
  created_at: 1, item_kind: 'message',
}
/** Handled, so the default `open` filter shows nothing while `items.length > 0` — a user who cleared
 *  their queue. This is the ONLY state in which "Inbox zero" is a true thing to say. */
const HANDLED_ITEM = { ...OPEN_ITEM, id: 'i-2', status: 'handled' }

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      // A FRESH INSTALL is the default here, because it is the case both earlier rails got wrong:
      // poll providers off, native sink live — exactly what the container measurement returned.
      inboxStatus: () => Promise.resolve({
        enabled: false, native_source_active: true, health: {},
        sources: [{ name: 'native', active: true, kind: 'push', can_reply: true },
                  { name: 'filesystem', active: false, kind: 'poll', can_reply: false }],
      }),
      // The real read is `api.inbox()` returning `InboxItem[]` directly — NOT an `{items}` envelope.
      inbox: () => Promise.resolve([]),
      // 🪤 The page also mounts `TriageDigestCard`, which reads `api.proactiveDigest`. Omitting it
      // throws `api.proactiveDigest is not a function` and fails every DOM test for a reason that has
      // nothing to do with the assertion — a mock must cover everything the tree MOUNTS.
      proactiveDigest: () => Promise.resolve({ installed: false }),
      ...over,
    },
  }))
}

async function renderInbox(navigate = vi.fn(), query: Record<string, string> = {}) {
  const { InboxPage } = await import('./InboxPage')
  render(<InboxPage query={query} setQuery={() => {}} navigate={navigate} />)
  return navigate
}

/** The one sentence this file exists to keep out of the DOM, in either spelling. */
const UNCONNECTED_CLAIM = /not connected|Enable a source to begin/i

describe('the inbox blank slate never contradicts its own source banner', () => {
  beforeEach(() => { vi.resetModules() })

  it('does not tell a fresh install its inbox is unconnected — the native source is live', async () => {
    // 🪤 Wait on the POSITIVE new title, never on the absence: a `waitFor` on an absence succeeds on
    // its first check, before the reads resolve, so it would pass against the defect.
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Nothing has arrived yet')).toBeInTheDocument())
    expect(screen.queryByText(UNCONNECTED_CLAIM),
      'the page receives `native_source_active: true` — it may not claim the opposite').toBeNull()
  })

  it('and the banner saying so is on screen AT THE SAME TIME — the measured contradiction', async () => {
    // The whole defect in one assertion: both sentences rendered together. Asserting the absence of
    // the false one is not enough on its own, because a build that dropped the banner would pass it.
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Nothing has arrived yet')).toBeInTheDocument())
    expect(screen.getByText(/Native source active/),
      'the banner is the evidence the headline used to contradict').toBeInTheDocument()
    expect(screen.queryByText(UNCONNECTED_CLAIM)).toBeNull()
  })

  it('does not congratulate them with "Inbox zero" either — nothing has arrived to clear', async () => {
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Nothing has arrived yet')).toBeInTheDocument())
    expect(screen.queryByText('Inbox zero'),
      'an inbox nothing has reached has no zero to be at').toBeNull()
  })

  it('offers the source link as an OFFER, and it reaches the section the hint names', async () => {
    mockApi()
    const navigate = await renderInbox()
    const cta = await screen.findByRole('button', { name: /Add a message source/i })
    await userEvent.click(cta)
    expect(navigate, 'the CTA must reach the section the hint names').toHaveBeenCalledWith('settings/inbox')
  })

  it('withholds that offer once a poll source IS collecting and simply has nothing yet', async () => {
    // The `pollConnected` half. Offering "add a source" to someone who has one is the same class of
    // wrong sentence this file is about, pointing the other way.
    mockApi({ inboxStatus: () => Promise.resolve({ enabled: true, native_source_active: true, health: {} }) })
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Nothing has arrived yet')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /Add a message source/i }),
      'a connected source makes the offer wrong').toBeNull()
  })

  it('"Inbox zero" survives for the state where it is TRUE — a queue the user cleared', async () => {
    // The other half. Suppressing the congratulation unconditionally would pass the tests above while
    // deleting the one state where it is the right thing to say. Note `enabled` is still FALSE here:
    // "caught up" is about the items, never about the poll providers.
    mockApi({ inbox: () => Promise.resolve([HANDLED_ITEM]) })
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Inbox zero')).toBeInTheDocument())
    expect(screen.getByText(/all caught up/)).toBeInTheDocument()
    expect(screen.queryByText('Nothing has arrived yet')).toBeNull()
  })

  it('and that cleared-queue state offers NO action — good news is not a task', async () => {
    mockApi({ inbox: () => Promise.resolve([HANDLED_ITEM]) })
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Inbox zero')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /Add a message source/i }),
      'manufacturing a CTA out of success is what the empty-state taxonomy forbids').toBeNull()
  })

  it('a NARROWED search keeps "Nothing here" and gets no setup copy or button', async () => {
    // 🪤 The regression rail 1 was written for, re-pinned from this side: a user whose filter matched
    // nothing must not be handed setup advice OR a setup button. `narrowed` wins for copy and action.
    mockApi({ inbox: () => Promise.resolve([OPEN_ITEM]) })
    await renderInbox(vi.fn(), { q: 'zzqqxnomatch' })
    await waitFor(() => expect(screen.getByText('Nothing here')).toBeInTheDocument())
    expect(screen.getByText(/Try a different search or filter/)).toBeInTheDocument()
    expect(screen.queryByText(UNCONNECTED_CLAIM)).toBeNull()
    expect(screen.queryByRole('button', { name: /Add a message source/i }),
      'a no-match filter is not a setup problem').toBeNull()
  })
})

describe('the source still says what the DOM tests rely on', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/inbox/InboxPage.tsx'), 'utf8')
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

  it('the blank-slate flag is derived from ITEMS, not from the poll-provider switch', () => {
    // The load-bearing half: without this the DOM assertions could pass on a title that happened to
    // read correctly because the fixture's `enabled` and `items` agreed.
    expect(code, 'nothing-ever-arrived is an items fact')
      .toMatch(/const neverReceived = \(items\?\.length \?\? 0\) === 0/)
    expect(code, '`!status.enabled` may not be read as "the inbox is off" again')
      .not.toMatch(/const disabled = status \? !status\.enabled : false/)
  })

  it('the title branches narrowed → neverReceived → caught-up', () => {
    expect(src).toMatch(/title=\{narrowed \? 'Nothing here' : neverReceived \? 'Nothing has arrived yet' : 'Inbox zero'\}/)
  })

  it('the action is gated on all three flags', () => {
    // `neverReceived` alone would put the offer under a no-match search; without `pollConnected` it
    // would sit under an inbox that already has a source.
    expect(src).toMatch(/action=\{neverReceived && !narrowed && !pollConnected/)
  })

  it('the icon is not the glyph this product assigns to Providers', () => {
    // `Plug` is Settings › Providers in this product (SettingsPage / settingsWidgets). Pointing at
    // Settings › Inbox with the Providers glyph would name the wrong destination.
    const action = src.match(/action=\{neverReceived && !narrowed && !pollConnected[\s\S]{0,240}?\}/)?.[0] ?? ''
    expect(action, 'the action block must be found before it can be checked').not.toBe('')
    expect(action).not.toMatch(/icon:\s*Plug\b/)
  })

  it('and the banner it must agree with is still rendered off the native-source fact', () => {
    // If this banner ever moves behind `enabled`, the contradiction comes back from the other side.
    expect(code).toMatch(/Native source active — agents post here directly/)
  })
})
