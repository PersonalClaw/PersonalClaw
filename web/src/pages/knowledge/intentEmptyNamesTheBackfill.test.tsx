/** A new knowledge intent read as broken because its empty state stated a verdict (#266).
 *
 * Intents only evaluate items saved AFTER the intent exists. So a freshly-created one shows
 * **"nothing gathered yet"** even when the library is already full of matches — and the one-click
 * backfill that fixes it, `Run on existing items`, lives a level down in the detail panel. The row
 * never mentioned it and neither did the new-intent form, so the reasonable conclusion was "intent
 * matching doesn't work" and the feature got abandoned on first use.
 *
 * Measured in the report: the same intent went from "nothing gathered yet" to "2 gathered" the
 * moment Run was found, with typed structured extraction from free-form content
 * (`early_warning_signal: SMART attribute 197 Current_Pending_Sector climbing before FAULTED`).
 * That quality was invisible to anyone who took the row at face value — a pure discoverability
 * loss on a feature that works.
 *
 * 🔑 THE ROW WAS THE LAST SURFACE STILL STATING A CONCLUSION. `IntentDetail`'s own empty state
 * already read *"Nothing gathered yet. Save items relevant to this intent, or run it on what you
 * already have."* — cause and affordance. The list row had only the verdict.
 *
 * 🪤 AND THE HINT HAS TO BE WITHHELD WHILE PAUSED, or the fix commits the error it removes: Run is
 * enabled on a paused intent and SUCCEEDS, it just evaluates nothing (measured against a live
 * gateway in #542: paused answers `{evaluated: 0}` over the same five items an active intent
 * answers `{evaluated: 5}` for). Pointing a user at a backfill that gathers nothing is the same
 * class of lie one step further on.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { KnowledgeIntent } from '../../lib/api'

const ACTIVE: KnowledgeIntent = {
  id: 'intent-homelab',
  goal: 'Hardware failure patterns and early-warning signals in my homelab',
  enabled: true,
  enabled_for: [],
  propose_skill: false,
}

async function mountList(intents: KnowledgeIntent[]) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        knowledgeIntents: () => Promise.resolve({ intents }),
        upsertKnowledgeIntent: vi.fn(() => Promise.resolve({ intents: [], id: intents[0]?.id })),
      },
    }
  })
  const { IntentsView } = await import('./KnowledgeListPage')
  render(<IntentsView selectedId={null} onSelect={() => {}} reloadKey={0} />)
  await waitFor(() => expect(screen.getByText(intents[0].goal!)).toBeTruthy())
}

/** The row's subtitle element — the line that carried the bare verdict. */
function subtitle(): HTMLElement {
  return screen.getByText(/nothing gathered yet|\d+ gathered/)
}

beforeEach(() => { cleanup(); vi.resetModules(); sessionStorage.clear() })
afterEach(() => cleanup())

describe('an intent that has gathered nothing says WHY, and where the fix is', () => {
  it('🔴 no longer leaves "nothing gathered yet" as a bare verdict', async () => {
    await mountList([ACTIVE])
    const line = subtitle()
    // The affordance is named on the row, so the user is not left to discover the detail panel.
    expect(line.textContent, 'the row must point at the backfill').toMatch(/run it on items you already saved/i)
    // And the CAUSE — forward-only evaluation — is on the element that truncates, which is this
    // app's idiom for a line a narrow viewport cuts (measured at 390px on the goal above it).
    expect(line.getAttribute('title'), 'the reason the count is zero').toMatch(/only evaluates items saved after it was created/i)
    expect(line.getAttribute('title')).toMatch(/Run on existing items/)
  })

  it('🪤 promises nothing while the intent is PAUSED, where Run gathers nothing', async () => {
    await mountList([{ ...ACTIVE, enabled: false }])
    const line = subtitle()
    expect(line.textContent, 'a paused intent must not be told to run').not.toMatch(/run it on items/i)
    expect(line.getAttribute('title'), 'and carries no backfill promise either').toBeNull()
    // The consequence is still explained — by the badge that owns it, which is the whole reason
    // this branch adds nothing rather than repeating it.
    expect(screen.getByText('Paused').getAttribute('title')).toMatch(/not evaluated against new items/i)
  })

  it('says only the count once the intent HAS gathered something', async () => {
    // The vacuity floor in the other direction: the hint is scoped to the empty case, so a
    // populated row does not carry a permanent instruction, and `2 gathered` is not decorated.
    await mountList([{ ...ACTIVE, outcome_count: 2 }])
    const line = subtitle()
    expect(line.textContent).toMatch(/^2 gathered/)
    expect(line.textContent).not.toMatch(/run it on items/i)
    expect(line.getAttribute('title'), 'an established count needs no explanation').toBeNull()
  })

  it('keeps the type filter suffix it already carried', async () => {
    // Regression floor: the empty branch is one arm of a ternary that is CONCATENATED with the
    // `enabled_for` suffix. Rewriting the arm must not eat the sibling.
    await mountList([{ ...ACTIVE, enabled_for: ['note', 'gist'] }])
    expect(subtitle().textContent).toMatch(/· note\/gist$/)
  })

  it('the detail panel still owns the action itself', async () => {
    // The row HINTS; it does not grow a second trigger. `Run on existing items` fans out one model
    // call per existing item, and the row is not where a user can see what that will cost — the
    // same argument the row's own comment makes for keeping the pause lever and nothing more.
    vi.doMock('../../lib/api', async (orig) => {
      const real = await orig<Record<string, unknown>>()
      return {
        ...real,
        api: { ...(real.api as object), knowledgeIntentOutcomes: () => Promise.resolve({ outcomes: [] }) },
      }
    })
    const { IntentDetail } = await import('./KnowledgeListPage')
    render(<IntentDetail intent={ACTIVE} onChanged={() => {}} onClose={() => {}} onEdit={() => {}} onOpenItem={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /run on existing items/i })).toBeTruthy())
  })
})
