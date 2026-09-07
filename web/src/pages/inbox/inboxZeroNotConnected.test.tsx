import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── "Inbox zero" congratulated a brand-new user for a state they have never been in ───────────────
//
// 🔑 THE SIBLING RAIL NEXT DOOR OPENS WITH "THE TITLE WAS ALREADY RIGHT AND THE HINT WAS NOT", and it
// was right — ON THE AXIS IT EXAMINED. `narrowedNotBlankSlate.test.tsx` fixed the HINT testing
// `disabled` before `narrowed`, and it checked the title against narrowed-vs-not, where the title
// genuinely was correct. It never asked the other question. The title branched on `narrowed` ONLY:
//
//     title={narrowed ? 'Nothing here' : 'Inbox zero'}
//     hint={narrowed ? … : disabled ? 'Enable a source to begin.' : 'You’re all caught up.'}
//
// So on a fresh install — `disabled = status ? !status.enabled : false`, true until a source is
// connected — the two halves disagreed again, on the axis nobody had looked at:
//
//     headline:  "Inbox zero"                    ← you are finished
//     body:      "Enable a source to begin."     ← you have not started
//
// 🔴 "Inbox zero" IS NOT A STATUS INDICATOR, IT IS A CLAIM ABOUT THE USER'S OWN TRIAGE STATE, and on
// a first visit to a nav-rail surface it is simply false: there is no zero to be at, because nothing
// has ever been connected. It is the same class of defect as a stats outage reporting an empty
// library — a confident sentence about the user's setup, derived from a state that means "not set up".
//
// 🪤 AND THE ONE ACTIONABLE STATE WAS THE ONLY ONE WITH NO ACTION. "Enable a source to begin" was
// prose with nothing to click; the user had to already know Settings › Inbox exists. `navigate` was
// in scope the whole time and `settings/inbox` is a real section id, so this was a wiring omission.
//
// 🪤 THE ACTION IS ON EXACTLY ONE BRANCH, AND THAT IS THE POINT. "Inbox zero" (connected, empty) and
// "Nothing here" (filtered to nothing) are both states where there is nothing for the user to do.
// `emptyStateRollout.test.tsx`'s taxonomy exists to stop a CTA being manufactured out of good news,
// and the tests below pin the absence on those two branches as hard as the presence on the third.

// 🪤 KNOWN GAP, STATED RATHER THAN HIDDEN. `status` is read as `api.inboxStatus().catch(() => null)`
// and `disabled = status ? !status.enabled : false`, so an UNREAD status resolves to `false` and lands
// on the "Inbox zero" branch. That catch is deliberate and the file says why (the status feeds the
// header's source-health readout, which has its own "unknown" rendering and must not take the whole
// list down), so it is not touched here. The case is also narrow: it needs the status read to fail
// while the items read succeeds, and a failed items read renders `LoadError` before this branch is
// reached. Fixing it properly is a copy decision about what to say when the setup state is unknown —
// deliberately not guessed at inside a bug fix.
//
// Worth recording that this file has now been bitten by the SAME conflation three times: the items
// read swallowing a 500 and rendering "Inbox zero" pixel-identical to a healthy queue (fixed — see the
// comment at the reads), the hint testing `disabled` before `narrowed` (fixed by
// `narrowedNotBlankSlate.test.tsx`), and now the title never testing `disabled` at all.

const ENVELOPE = { enabled: true, health: {} }

function mockApi(over: Record<string, unknown> = {}) {
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      inboxStatus: () => Promise.resolve(ENVELOPE),
      // The real read is `api.inbox()` returning `InboxItem[]` directly — NOT an `{items}` envelope.
      // My first draft mocked `inboxItems` and an envelope; both were wrong.
      inbox: () => Promise.resolve([]),
      // 🪤 The page also mounts `TriageDigestCard`, which reads `api.proactiveDigest`. Omitting it
      // threw `api.proactiveDigest is not a function` and failed all five DOM tests for a reason that
      // had nothing to do with the assertion — a mock must cover everything the tree MOUNTS, not just
      // what the assertion touches.
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

describe('the inbox empty state tells a new user the truth about their setup', () => {
  beforeEach(() => { vi.resetModules() })

  it('with NO source connected it does not say "Inbox zero"', async () => {
    mockApi({ inboxStatus: () => Promise.resolve({ enabled: false, health: {} }) })
    await renderInbox()
    // 🪤 Wait on the POSITIVE new title, never on the absence — a `waitFor` on an absence succeeds on
    // its first check, before the status read could have resolved, so it would pass against the defect.
    await waitFor(() => expect(screen.getByText('Inbox is not connected yet')).toBeInTheDocument())
    expect(screen.queryByText('Inbox zero'), 'an unconfigured inbox has no zero to be at').toBeNull()
  })

  it('and it offers a way to get there instead of only naming it in prose', async () => {
    mockApi({ inboxStatus: () => Promise.resolve({ enabled: false, health: {} }) })
    const navigate = await renderInbox()
    const cta = await screen.findByRole('button', { name: /Connect a source/i })
    await userEvent.click(cta)
    expect(navigate, 'the CTA must reach the section the hint names').toHaveBeenCalledWith('settings/inbox')
  })

  it('with a source connected "Inbox zero" survives — it is TRUE there', async () => {
    // The other half. Suppressing the congratulation unconditionally would pass the first test while
    // deleting the one state where it is the right thing to say.
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Inbox zero')).toBeInTheDocument())
    expect(screen.queryByText('Inbox is not connected yet')).toBeNull()
  })

  it('and the connected-empty state offers NO action — good news is not a task', async () => {
    mockApi()
    await renderInbox()
    await waitFor(() => expect(screen.getByText('Inbox zero')).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: /Connect a source/i }),
      'manufacturing a CTA out of success is what the empty-state taxonomy forbids').toBeNull()
  })

  it('a NARROWED search keeps "Nothing here" and gets no connect action either', async () => {
    // 🪤 The regression the sibling rail was written for, re-pinned from this side: a user whose
    // filter matched nothing must not be handed setup advice OR a setup button. `narrowed` wins over
    // `disabled` for both the copy and the action.
    mockApi({ inboxStatus: () => Promise.resolve({ enabled: false, health: {} }) })
    await renderInbox(vi.fn(), { q: 'zzqqxnomatch' })
    await waitFor(() => expect(screen.getByText('Nothing here')).toBeInTheDocument())
    expect(screen.queryByText('Inbox is not connected yet')).toBeNull()
    expect(screen.queryByRole('button', { name: /Connect a source/i }),
      'a no-match filter is not a setup problem').toBeNull()
  })
})

describe('the source still says what the DOM tests rely on', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/inbox/InboxPage.tsx'), 'utf8')

  it('the title branches on `disabled`, not only on `narrowed`', () => {
    // The load-bearing half: without this the DOM assertions could pass on a title that happened to
    // read correctly for some other reason.
    expect(src, 'the title must distinguish not-connected from caught-up')
      .toMatch(/title=\{narrowed \? 'Nothing here' : disabled \? 'Inbox is not connected yet' : 'Inbox zero'\}/)
  })

  it('the action is gated on BOTH flags', () => {
    // `disabled` alone would put a setup button under a no-match search.
    expect(src).toMatch(/action=\{disabled && !narrowed/)
  })

  it('the icon is not the glyph this product assigns to Providers', () => {
    // `Plug` is Settings › Providers in this product (SettingsPage / settingsWidgets). Pointing at
    // Settings › Inbox with the Providers glyph would name the wrong destination.
    const action = src.match(/action=\{disabled && !narrowed[\s\S]{0,240}?\}/)?.[0] ?? ''
    expect(action, 'the action block must be found before it can be checked').not.toBe('')
    expect(action).not.toMatch(/icon:\s*Plug\b/)
  })
})
