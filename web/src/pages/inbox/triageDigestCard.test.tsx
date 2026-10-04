import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TriageDigestCard } from './TriageDigestCard'
import type { TriageDigestNotice, TriageDigestView } from '../../lib/api'

// ── The triage digest card ─────────────────────────────────────────────────────────────────
//
// The card has FIVE reasons to show no items and only one of them is good news, so every test
// below is a PAIR: the state, and the state it must not be confused with. A suite that only
// asserted "the list is empty" would pass for all five, which is the defect.
//
//   the read failed            → the error, with a retry
//   never installed            → the install offer; there is no schedule to be empty
//   installed but switched off  → dormant-but-kept (criterion 10)
//   installed, not yet run     → when it will run
//   ran, nothing to report     → the only reassuring one
//
// One level down, the same rule twice more: `auto_stage_ran === false` must not print "0 actions
// taken", and `ledger_complete === false` must not print "no rows". Both are absences of a
// measurement, not zeroes.
//
// 🔑 The last two tests assert the CALL SITE. A card that mounts green in isolation stays green
// after its render is deleted from the page, which reproduces the exact defect one level up — so
// the page source is checked for the render, with a vacuity assertion proving the check can fail.

const proactiveDigest = vi.fn()
const proactiveReply = vi.fn((_runId: string, _text: string) => Promise.resolve({ ok: true, outcome: 'acted' as const, results: [{ ordinal: '1', outcome: 'acted' as const, executed: true, recorded: true }] }))
const proactiveInstall = vi.fn((_cron?: string) => Promise.resolve({ ok: true, created: true, schedule: { id: 'system:triage:digest', name: 'Morning triage', cron: '0 8 * * *', enabled: true, created_by: 'system' } }))
const autonomyUndo = vi.fn((_id: string) => Promise.resolve({ ok: true, code: 'reversed', action_type: 'action.inbox_op', demoted: false }))

vi.mock('../../lib/api', () => ({
  api: {
    proactiveDigest: () => proactiveDigest(),
    proactiveReply: (runId: string, text: string) => proactiveReply(runId, text),
    proactiveInstall: (cron?: string) => proactiveInstall(cron),
    autonomyUndo: (id: string) => autonomyUndo(id),
  },
}))
const notify = vi.fn((_message: string, _tone?: string) => undefined)
vi.mock('../../app/appSdk', () => ({ notify: (m: string, t?: string) => notify(m, t) }))

function view(over: Partial<TriageDigestView> = {}): TriageDigestView {
  return {
    state: 'ready',
    enabled: true,
    installed: true,
    error: '',
    run_id: 'run-abc',
    permalink: '#/workflows/runs/run-abc',
    title: 'Morning triage',
    collected: 3,
    dropped: 1,
    auto_stage_ran: true,
    auto_done: [],
    pending: [],
    journal: [],
    ledger_complete: true,
    ledger_rows: 2,
    ...over,
  }
}

const PENDING = {
  ordinal: '1',
  action_type: 'reply_draft',
  tier: 'medium',
  pattern_key: 'reply_draft:inbox',
  clamped: true,
  action_config: {},
  reason: 'needs_you',
  rule: '',
  not_done: '',
  answered: false,
  answer: '',
  answer_not_done: '',
  permalink: '#/workflows/runs/run-abc',
  title: 'Review request on #412',
  source: 'inbox',
  item_permalink: '',
  materiality: 'action',
  carried_over: false,
  carried_note: '',
  first_proposed_at: '2026-10-04T08:00:00+00:00',
  first_run_id: 'run-abc',
}

const AUTO_DONE = {
  ordinal: '2',
  source_id: 'gh_2',
  action_type: 'archive',
  provider: 'inbox-op',
  rule: 'policy:trivial-tier',
  reversal: 'aW5ib3gtb3A6Z2hfMg==',
  undoable: true,
  permalink: '#/workflows/runs/run-abc',
  title: 'Dependabot bumped left-pad',
  source: 'inbox',
  item_permalink: '',
  materiality: 'none',
}

beforeEach(() => {
  proactiveDigest.mockReset()
  proactiveReply.mockClear()
  proactiveInstall.mockClear()
  autonomyUndo.mockClear()
  notify.mockClear()
  try { window.sessionStorage.clear() } catch { /* jsdom without storage */ }
})

describe('the digest card tells five different states apart', () => {
  it('renders the failure, not an empty digest, when the read rejects', async () => {
    proactiveDigest.mockRejectedValue(new Error('gateway said 500'))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/gateway said 500/)).toBeTruthy())
    // The pair: NOT the install offer. A failed read that rendered "install Morning triage" would
    // offer to install something that may already be running.
    expect(screen.queryByRole('button', { name: /^Install$/ })).toBeNull()
  })

  it("renders the server's own error verdict the same way", async () => {
    proactiveDigest.mockResolvedValue(view({ state: 'error', error: 'OSError: events.jsonl is unreadable' }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/events\.jsonl is unreadable/)).toBeTruthy())
  })

  it('offers §5.4 install with an editable schedule when nothing is installed', async () => {
    proactiveDigest.mockResolvedValue(view({ state: 'uninstalled', installed: false }))
    render(<TriageDigestCard />)
    const field = await screen.findByLabelText('Digest schedule (cron)')
    fireEvent.change(field, { target: { value: '30 7 * * 1-5' } })
    fireEvent.click(screen.getByRole('button', { name: /^Install$/ }))
    // The trigger the card installs carries the cron the user typed — that is what makes it
    // "editable" rather than a schedule they discover afterwards and have to go fix.
    await waitFor(() => expect(proactiveInstall).toHaveBeenCalledWith('30 7 * * 1-5'))
  })

  it('says dormant-but-kept when triage is off, not "no digest yet"', async () => {
    proactiveDigest.mockResolvedValue(view({
      state: 'off', enabled: false,
      schedule: { id: 'system:triage:digest', name: 'Morning triage', cron: '0 8 * * *', enabled: false, created_by: 'system' },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/are kept/)).toBeTruthy())
    expect(screen.getByText('0 8 * * *')).toBeTruthy()
    // The pair: "off" is not "not yet run".
    expect(screen.queryByText(/No digest has run yet/)).toBeNull()
  })

  it('says not-yet-run when it is installed and on but has never fired', async () => {
    proactiveDigest.mockResolvedValue(view({
      state: 'never_run',
      schedule: { id: 'system:triage:digest', name: 'Morning triage', cron: '0 8 * * *', enabled: true, created_by: 'system' },
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/No digest has run yet/)).toBeTruthy())
    // The pair: not-yet-run is not off.
    expect(screen.queryByText(/switched off/)).toBeNull()
  })
})

describe('an unmeasured value is not rendered as a zero', () => {
  it('says auto-execution is OFF rather than reporting no actions', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_stage_ran: false, auto_done: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/Auto-execution is off/)).toBeTruthy())
    expect(screen.queryByText(/ran and found nothing/)).toBeNull()
  })

  it('says the stage RAN and found nothing when it did — the distinguishing pair', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_stage_ran: true, auto_done: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/ran and found nothing/)).toBeTruthy())
    // Both arms produce an empty auto-done list, so this negative is the whole point of the pair.
    expect(screen.queryByText(/Auto-execution is off/)).toBeNull()
  })

  it('reports an incomplete ledger rather than an empty one', async () => {
    proactiveDigest.mockResolvedValue(view({ ledger_complete: false, journal: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/not recorded/)).toBeTruthy())
    expect(screen.queryByText(/wrote no ledger rows/)).toBeNull()
  })

  it('reports a genuinely empty ledger as empty — the pair', async () => {
    proactiveDigest.mockResolvedValue(view({ ledger_complete: true, journal: [] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/wrote no ledger rows/)).toBeTruthy())
  })

  it('badges an unscored tier as untiered, never as the cheapest one', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [{ ...PENDING, tier: '', clamped: false }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText('untiered')).toBeTruthy())
    // A blank tier must not read as `trivial`: the badge is a permission cue and the safe default
    // is to say we do not know.
    expect(screen.queryByText('trivial')).toBeNull()
  })
})

describe('the card shows the items it counts', () => {
  // The card said "2 items in this window" and showed neither: its sections held only proposals
  // and actions taken, while the digest's own notification listed the two under "What your machine
  // did". Every item the gate kept now has one place: the runs that ended, and what else waits.
  const RAN = {
    ordinal: '2', title: 'general-project: escalated (4 effects)', source: 'run',
    item_permalink: '#/workflows/runs/fb54446b', materiality: 'error', needs_you: true,
  }
  const WAITING = {
    ordinal: '1', title: 'Can you look at the venue contract?', source: 'inbox',
    item_permalink: '', materiality: 'response',
  }

  it('🔴 lists the runs that ended under what your machine did, and what needs you among them', async () => {
    proactiveDigest.mockResolvedValue(view({ collected: 1, dropped: 0, auto_stage_ran: false, ran: [RAN] }))
    render(<TriageDigestCard />)
    const runs = await screen.findByRole('list', { name: 'Runs that ended in this window' })
    expect(runs.textContent).toContain('general-project: escalated (4 effects)')
    expect(runs.textContent).toContain('needs you')
    expect(screen.getByRole('link', { name: 'Open the run general-project: escalated (4 effects)' }).getAttribute('href'))
      .toBe('#/workflows/runs/fb54446b')
    // The off-stage sentence no longer claims that everything below is a proposal: a run is not.
    expect(screen.queryByText(/Everything below is a proposal/)).toBeNull()
  })

  it('🔴 lists what else is waiting', async () => {
    proactiveDigest.mockResolvedValue(view({ collected: 1, dropped: 0, waiting: [WAITING] }))
    render(<TriageDigestCard />)
    const rest = await screen.findByRole('list', { name: 'Also waiting' })
    expect(rest.textContent).toContain('Can you look at the venue contract?')
  })

  it('shows neither list when nothing is in it', async () => {
    proactiveDigest.mockResolvedValue(view({ ran: [], waiting: [] }))
    render(<TriageDigestCard />)
    await screen.findByText(/items in this window/)
    expect(screen.queryByRole('list', { name: 'Runs that ended in this window' })).toBeNull()
    expect(screen.queryByRole('list', { name: 'Also waiting' })).toBeNull()
  })
})

describe('the auto-done section offers undo where an undo exists', () => {
  it('undoes through the platform reversal handle', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [AUTO_DONE] }))
    render(<TriageDigestCard />)
    const btn = await screen.findByRole('button', { name: /Undo/ })
    fireEvent.click(btn)
    // The handle the provider recorded, not an id the card invented.
    await waitFor(() => expect(autonomyUndo).toHaveBeenCalledWith('aW5ib3gtb3A6Z2hfMg=='))
  })

  it('offers no Undo — and says why — when the provider recorded no reversal', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [{ ...AUTO_DONE, reversal: '', undoable: false }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/no undo recorded/)).toBeTruthy())
    // A dead Undo would be worse than none: the user would press it and learn nothing.
    expect(screen.queryByRole('button', { name: /Undo/ })).toBeNull()
  })

  it('names the rule that authorised each action', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [AUTO_DONE] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText('policy:trivial-tier')).toBeTruthy())
  })
})

describe('one-tap yes / no / always emits the reply grammar', () => {
  it.each([
    ['Yes', '1 yes'],
    ['No', '1 no'],
    ['Always', 'always yes 1'],
    ['Never', 'always no 1'],
  ])('%s sends "%s"', async (label, text) => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: label }))
    // The SAME grammar `approval.parse_reply` accepts for a typed channel reply — one parser, so
    // a tap and a message cannot disagree about what "always no 1" means.
    await waitFor(() => expect(proactiveReply).toHaveBeenCalledWith('run-abc', text))
  })

  it('shows the tier badge and flags a raised tier', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/needs a look \(raised\)/)).toBeTruthy())
  })

  it('withholds "always" when the run recorded no pattern to teach', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [{ ...PENDING, pattern_key: '' }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/no pattern to remember/)).toBeTruthy())
    // Inventing a pattern from the action type would teach a rule far broader than the one item
    // the user is looking at.
    expect(screen.queryByRole('button', { name: 'Always' })).toBeNull()
    // Vacuity: the once-only buttons ARE offered, so the absence above is about `always` alone.
    expect(screen.getByRole('button', { name: 'Yes' })).toBeTruthy()
  })

  it('keeps an answered proposal visible and does not re-offer it', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [{ ...PENDING, answered: true, answer: 'always no' }] }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/You answered/)).toBeTruthy())
    expect(screen.getByText('always no')).toBeTruthy()
    // Re-offering the buttons would invite a duplicate answer; removing the row would make the
    // reply look like it did nothing.
    expect(screen.queryByRole('button', { name: 'Yes' })).toBeNull()
  })

  it('surfaces an expired digest instead of acting on a stale ordinal', async () => {
    // 🔴 REJECTS, does not resolve. The refusal is a 409 and `api.ts`'s `post` throws on any
    // non-2xx, so the first version of this test resolved `{outcome: 'expired'}` — a shape the api
    // layer cannot produce — and went green against a branch that could never run. The double now
    // fails the way the real client fails, with the status the card branches on.
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    const expired = Object.assign(new Error('that digest expired — open the current one'), { status: 409 })
    proactiveReply.mockRejectedValueOnce(expired)
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('expired'), 'error'))
    // And it is NOT reported as a generic failure: an expired digest is a re-read, not a retry.
    expect(notify).not.toHaveBeenCalledWith(expect.stringContaining("Couldn't answer"), 'error')
  })

  it('reports a genuine failure as a failure — the discriminating pair', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    proactiveReply.mockRejectedValueOnce(Object.assign(new Error('HTTP 502'), { status: 502 }))
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining("Couldn't answer"), 'error'))
  })

  it('surfaces an unrecorded answer, because the next tap would act again', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    proactiveReply.mockResolvedValueOnce({
      ok: true, outcome: 'acted' as const,
      results: [{ ordinal: '1', outcome: 'acted' as const, executed: true, recorded: false }],
    } as never)
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining("wasn't recorded"), 'error'))
  })

  it('says nothing ran again when the ordinal was already answered', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    proactiveReply.mockResolvedValueOnce({
      ok: true, outcome: 'acted' as const,
      results: [{ ordinal: '1', outcome: 'already' as const, detail: 'already answered no' }],
    } as never)
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('Already answered'), 'info'))
  })
})

describe('an absent notification is explained, never claimed as delivered', () => {
  // 🔴 A digest run inside quiet hours reported `delivered: true` while the notification list did
  // not grow. `DashboardState.notify` returns nothing, so the run cannot know. The card reads what
  // your settings make of the digest's notice (`view.notice`, the server asking the rule layer
  // `notify()` delivers by) and says that, never an outcome the run could not verify — and never
  // "held back" for a notice your rule put in the bell or kept for the notification digest.
  const notice = (over: Partial<Extract<TriageDigestNotice, { known: true }>> = {}): TriageDigestNotice => ({
    known: true,
    mute_all: false,
    min_severity: 'info',
    quiet_hours: { enabled: true, start: '22:00', end: '08:00' },
    rule: 'Notice',
    inside: 'suppressed',
    outside: 'immediate',
    ...over,
  })

  it('says a ping rule does not notify you inside the quiet-hours window', async () => {
    proactiveDigest.mockResolvedValue(view({ handed_to_notify: true, notice: notice() }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/Quiet hours 22:00–08:00: a digest that lands inside that window does not notify you/)).toBeTruthy())
    // Never the word the run could not verify.
    expect(screen.queryByText(/delivered/i)).toBeNull()
  })

  it('says a badge rule shows it in your notifications, not that it was held back', async () => {
    proactiveDigest.mockResolvedValue(view({ notice: notice({ inside: 'badge', outside: 'badge' }) }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/“Notice” notification rule shows this digest as a badge in your notifications/)).toBeTruthy())
    expect(screen.queryByText(/held back/)).toBeNull()
    expect(screen.queryByText(/does not notify you/)).toBeNull()
  })

  it('says a digest rule keeps it for the notification digest, not that it was held back', async () => {
    proactiveDigest.mockResolvedValue(view({ notice: notice({ inside: 'digest', outside: 'digest' }) }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/keeps this digest for your notification digest/)).toBeTruthy())
    expect(screen.queryByText(/held back/)).toBeNull()
  })

  it('names quiet hours only for what they change: a raised badge rule rings outside, badges inside', async () => {
    proactiveDigest.mockResolvedValue(view({ notice: notice({ inside: 'badge', outside: 'immediate' }) }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/inside that window shows as a badge in your notifications, without a sound or a push/)).toBeTruthy())
  })

  it('says a Never rule, which holds at every hour, rather than quiet hours', async () => {
    proactiveDigest.mockResolvedValue(view({ notice: notice({ inside: 'never', outside: 'never' }) }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/“Notice” notification rule never notifies/)).toBeTruthy())
    expect(screen.queryByText(/Quiet hours/)).toBeNull()
  })

  it('says the minimum severity when it drops the digest at every hour', async () => {
    proactiveDigest.mockResolvedValue(view({ notice: notice({ min_severity: 'warning', inside: 'dropped', outside: 'dropped' }) }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/Your notifications show only warnings and errors/)).toBeTruthy())
    expect(screen.queryByText(/Quiet hours/)).toBeNull()
  })

  it('says mute-all when everything is muted, which is a different cause', async () => {
    proactiveDigest.mockResolvedValue(view({
      notice: notice({ mute_all: true, quiet_hours: { enabled: false, start: '', end: '' }, inside: 'dropped', outside: 'dropped' }),
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/All notifications are muted/)).toBeTruthy())
    expect(screen.queryByText(/Quiet hours/)).toBeNull()
  })

  it('says UNKNOWN when the settings could not be read, not "quiet hours are off"', async () => {
    proactiveDigest.mockResolvedValue(view({ notice: { known: false } }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/is unknown/)).toBeTruthy())
  })

  it('stays silent when the digest simply notifies you — the vacuity pair', async () => {
    proactiveDigest.mockResolvedValue(view({
      notice: notice({ quiet_hours: { enabled: false, start: '22:00', end: '08:00' }, inside: 'immediate', outside: 'immediate' }),
    }))
    render(<TriageDigestCard />)
    await waitFor(() => expect(screen.getByText(/What your machine did/)).toBeTruthy())
    expect(screen.queryByText(/Quiet hours/)).toBeNull()
    expect(screen.queryByText(/notification rule/)).toBeNull()
    expect(screen.queryByText(/is unknown/)).toBeNull()
    expect(screen.queryByText(/was not announced/)).toBeNull()
  })
})

describe('the ledger section permalinks into the run journal', () => {
  it('links every row at the run the digest came from', async () => {
    proactiveDigest.mockResolvedValue(view({
      journal: [{
        kind: 'skipped_triage', seq: 4, ordinal: '5', action_type: '', rule: 'dependabot',
        outcome: '', reason: 'automated dependency bump', detail: '', verb: '',
        permalink: '#/workflows/runs/run-abc',
      }],
    }))
    render(<TriageDigestCard />)
    const link = await screen.findByRole('link', { name: /Open the run journal for skipped_triage/ })
    expect(link.getAttribute('href')).toBe('#/workflows/runs/run-abc')
  })
})

// ── THE CALL SITE ─────────────────────────────────────────────────────────────────────────
//
// Every test above mounts the card directly, and every one of them would stay green if the
// render were deleted from the page — reproducing the same defect one level up. These two read
// the pages instead.

const SRC = join(process.cwd(), 'src')
const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the cards are rendered BY their pages', () => {
  it('the inbox page renders the digest card', () => {
    const code = stripComments(readFileSync(join(SRC, 'pages/inbox/InboxPage.tsx'), 'utf8'))
    expect(code).toContain("import { TriageDigestCard } from './TriageDigestCard'")
    expect(code, 'a card the page imports but never renders is a card no user sees').toMatch(/<TriageDigestCard\s*\/>/)
    // Vacuity: a component this page does NOT render must be absent, so the match above cannot be
    // satisfied by any substring of the file.
    expect(code).not.toMatch(/<TriageRulesCard\s*\/>/)
  })

  it('the inbox settings panel renders the rules manager and the triage switches', () => {
    const code = stripComments(readFileSync(join(SRC, 'pages/settings/InboxSettingsPanel.tsx'), 'utf8'))
    expect(code).toContain("import { TriageRulesCard } from './TriageRulesCard'")
    expect(code).toMatch(/<TriageRulesCard\s*\/>/)
    // The card is only meaningful beside the switch that makes its rules dormant (criterion 10),
    // and `proactive.triage_enabled` had no frontend control at all.
    expect(code).toContain("api.patchConfig('proactive.triage_enabled'")
    expect(code).toContain("api.patchConfig('proactive.auto_execute_enabled'")
    // Turning triage off must ALSO retire the schedule, or a cron keeps firing for a disabled
    // digest. The reconcile is the second half of that write.
    expect(code).toMatch(/patchConfig\('proactive\.triage_enabled', v\)[\s\S]{0,160}proactiveInstall\(\)/)
    expect(code).not.toMatch(/<TriageDigestCard\s*\/>/)
  })
})

// ── The pending row carries its own accessible name (issue 618) ────────────────────────────────
//
// The two lists in this card name themselves; a LIST ITEM is announced by its content, and this
// row's content is an ordinal span, a verb span and a title in one paragraph followed by a badge, a
// source and up to two links. Landing on the row read the whole subtree in order, so "which
// proposal is this" arrived interleaved with "what can I do to it".
//
// The pair here is the point: the name must be built from the same fields the row SHOWS, so the two
// cannot drift — including the missing-field case, where `proposedVerb('')` prints 'Act on' and the
// title falls back to `item <ordinal>`. A name that quietly omitted the verb there would announce
// the row differently from the row.

describe('a proposal row announces a concise name', () => {
  it('names the row from the ordinal, verb, title and source it displays', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    render(<TriageDigestCard />)
    const row = await screen.findByRole('listitem', { name: 'Proposal 1: Draft a reply to Review request on #412, inbox' })
    // The name above could be satisfied by a string that merely looks right, so the text the user
    // SEES is checked inside the very node just found by that name.
    expect(row.textContent).toContain('Review request on #412')
    expect(row.textContent).toContain('Draft a reply to')
    // A proposal is a request, so it is never said as done.
    expect(row.textContent).not.toContain('Drafted')
  })

  it('names the missing-field row the way the row itself renders it', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [{ ...PENDING, action_type: '', title: '', source: '' }] }))
    render(<TriageDigestCard />)
    // `proposedVerb('')` and the `item ${n}` fallback are the paragraph's own output, so an empty
    // proposal is reachable by name instead of being announced as a bare "Proposal 1:".
    const row = await screen.findByRole('listitem', { name: 'Proposal 1: Act on item 1' })
    expect(row.textContent).toContain('Act on')
    expect(row.textContent).toContain('item 1')
  })

  it('shows and names the task a Yes would file, by the title its run recorded', async () => {
    // Yes files the task under this title, so it is on the row before she answers: what she
    // approves is what she reads.
    const TASK = {
      ...PENDING, action_type: 'create_task', tier: 'low', clamped: false,
      pattern_key: 'create_task:sender:venue', action_config: { title: 'Renew the venue booking' },
      title: 'Your booking for the spring talk venue lapses next week.',
    }
    proactiveDigest.mockResolvedValue(view({ pending: [TASK] }))
    render(<TriageDigestCard />)
    const row = await screen.findByRole('listitem', {
      name: 'Proposal 1: File a task for Your booking for the spring talk venue lapses next week., as the task “Renew the venue booking”, inbox',
    })
    expect(row.textContent).toContain('as the task “Renew the venue booking”')
  })

  it('names no task for a proposal that binds none — the pair', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    render(<TriageDigestCard />)
    const reply = await screen.findByRole('listitem', { name: 'Proposal 1: Draft a reply to Review request on #412, inbox' })
    expect(reply.textContent).not.toContain('as the task')
  })
})

// ── An action that did not happen is said as not done ──────────────────────────────────────────
//
// The digest can act on its own and on a tap. When the attempt failed, or a guard held it, the
// card said it in the words of success or of a proposal nobody tried: the row read "Archived" with
// no reason, the section said the stage "found nothing it was allowed to do", and a failed "yes"
// toasted "Noted.". Each test is a pair with the case it must not be confused with.

describe('an action that did not happen is said as not done', () => {
  const NOT_DONE = 'Not done: the digest tried it on its own and it failed — the inbox item is gone. '
    + 'Yes tries it again, or open the item to do it yourself.'
  const FAILED = {
    ...PENDING, ordinal: '2', action_type: 'archive', tier: 'trivial', pattern_key: 'archive:sender:news',
    clamped: false, reason: 'auto_failed', title: 'Weekly newsletter', not_done: NOT_DONE,
  }

  it('🔴 says on the proposal that it failed, why, and what to do next', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [FAILED, PENDING] }))
    render(<TriageDigestCard />)
    const row = await screen.findByRole('listitem', { name: /^Proposal 2:/ })
    expect(row.textContent).toContain('the inbox item is gone')
    expect(row.textContent).toContain('Yes tries it again')
    // Said as a request: "Archived" on this row read exactly like the action that landed.
    expect(row.textContent).toContain('Archive Weekly newsletter')
    expect(row.textContent).not.toContain('Archived')
    // The pair: a proposal nobody tried carries no failure.
    const untried = screen.getByRole('listitem', { name: /^Proposal 1:/ })
    expect(untried.textContent).not.toContain('Not done')
  })

  it('🔴 does not say the stage found nothing to do when what it tried did not happen', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [], pending: [FAILED] }))
    render(<TriageDigestCard />)
    await screen.findByText(/Nothing was done on its own/)
    expect(screen.queryByText(/found nothing it was allowed/)).toBeNull()
  })

  it('says beside what landed that more did not happen', async () => {
    proactiveDigest.mockResolvedValue(view({ auto_done: [AUTO_DONE], pending: [FAILED] }))
    render(<TriageDigestCard />)
    const done = await screen.findByRole('list', { name: 'What your machine did' })
    // Only the action that landed is listed as done.
    expect(done.textContent).toContain('Archived Dependabot bumped left-pad')
    expect(done.textContent).not.toContain('Weekly newsletter')
    expect(screen.getByText(/One more action it was about to take did not happen/)).toBeTruthy()
  })

  it('🔴 says why nothing ran when the stage stopped as a whole', async () => {
    const stopped = "Nothing ran on its own: incident mode is on, which holds the digest's actions. "
      + 'What it proposed waits for you.'
    proactiveDigest.mockResolvedValue(view({ auto_done: [], auto_stopped: stopped, pending: [{ ...PENDING, reason: 'incident_active' }] }))
    render(<TriageDigestCard />)
    await screen.findByText(/incident mode is on, which holds the digest's actions/)
    expect(screen.queryByText(/found nothing it was allowed/)).toBeNull()
  })

  it('🔴 reports a yes that did not happen as a failure, in its own words', async () => {
    const sentence = 'Not done: it was tried and it failed — the inbox item is gone. Open the item to do it yourself.'
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING] }))
    proactiveReply.mockResolvedValueOnce({
      ok: true, outcome: 'acted' as const,
      results: [{ ordinal: '1', outcome: 'acted' as const, executed: false, recorded: true, not_done: sentence }],
    } as never)
    render(<TriageDigestCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(notify).toHaveBeenCalledWith(sentence, 'error'))
    expect(notify).not.toHaveBeenCalledWith('Noted.', 'success')
  })

  it('keeps saying an answered yes did not happen — and says nothing for a no', async () => {
    const sentence = 'Not done: a safety rule held it — the action is on your denylist. Open the item to do it yourself.'
    proactiveDigest.mockResolvedValue(view({
      pending: [
        { ...PENDING, answered: true, answer: 'yes', answer_not_done: sentence },
        { ...FAILED, not_done: '', answered: true, answer: 'no' },
      ],
    }))
    render(<TriageDigestCard />)
    const yes = await screen.findByRole('listitem', { name: /^Proposal 1:/ })
    expect(yes.textContent).toContain(sentence)
    const no = screen.getByRole('listitem', { name: /^Proposal 2:/ })
    expect(no.textContent).not.toContain('Not done')
  })

  it('lists two failure rows that share a sequence number, so neither hides the other', async () => {
    // A reply's journal rows and the digest's own were each numbered from 1, so a key built from
    // kind and seq repeated, and React may drop a row whose key repeats.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    const row = (ordinal: string, reason: string) => ({
      kind: 'auto_failed', seq: 1, ordinal, action_type: 'archive', rule: 'policy:trivial-tier',
      outcome: 'auto_failed', reason, detail: '', verb: '', permalink: '#/workflows/runs/run-abc',
    })
    proactiveDigest.mockResolvedValue(view({ journal: [row('2', 'the first item is gone'), row('3', 'the second item is gone')] }))
    render(<TriageDigestCard />)
    const list = await screen.findByRole('list', { name: "This run's ledger rows" })
    expect(list.textContent).toContain('the first item is gone')
    expect(list.textContent).toContain('the second item is gone')
    expect(errors.mock.calls.some((call) => String(call[0]).includes('same key'))).toBe(false)
    errors.mockRestore()
  })
})

// ── What an earlier digest left waiting comes back ─────────────────────────────────────────────
//
// A proposal she had not answered used to vanish with the digest that made it. The next digest now
// carries it, under its own number there, until she answers it, it is dealt with or a week has
// passed; one that waited longer is named rather than simply gone. Each test is a pair with the
// case it must not be confused with: a proposal this digest made says none of it.

describe('what an earlier digest left waiting comes back', () => {
  const RULE = 'A proposal you have not answered comes back in each digest for up to 7 days.'
  const CARRIED = {
    ...PENDING, ordinal: '2', action_type: 'create_task', tier: 'low', clamped: false,
    pattern_key: 'create_task:sender:venue', action_config: { title: 'Renew the venue booking' },
    reason: '', title: 'Your booking for the spring talk venue lapses next week.',
    carried_over: true, carried_note: 'Carried over: proposed 2 days ago, and not answered yet.',
    first_proposed_at: '2026-10-02T08:00:00+00:00', first_run_id: 'run-mon',
  }

  it('🔴 says a carried proposal is carried over and how long it has waited, and answers it here', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING, CARRIED], carry_rule: RULE }))
    render(<TriageDigestCard />)
    const row = await screen.findByRole('listitem', { name: /^Proposal 2:.*carried over from an earlier digest$/ })
    expect(row.textContent).toContain('Carried over: proposed 2 days ago, and not answered yet.')
    expect(screen.getByText(RULE)).toBeTruthy()
    // Its answer is this digest's, under the number this digest gave it.
    const yes = row.querySelector('button')
    expect(yes?.textContent).toBe('Yes')
    fireEvent.click(yes as HTMLElement)
    await waitFor(() => expect(proactiveReply).toHaveBeenCalledWith('run-abc', '2 yes'))
    // The pair: the proposal this digest made says none of it.
    const own = screen.getByRole('listitem', { name: 'Proposal 1: Draft a reply to Review request on #412, inbox' })
    expect(own.textContent).not.toContain('Carried over')
  })

  it('says no rule when nothing was carried — the vacuity pair', async () => {
    proactiveDigest.mockResolvedValue(view({ pending: [PENDING], carry_rule: RULE }))
    render(<TriageDigestCard />)
    await screen.findByRole('listitem', { name: /^Proposal 1:/ })
    expect(screen.queryByText(RULE)).toBeNull()
    expect(screen.queryByRole('list', { name: 'No longer offered' })).toBeNull()
  })

  it('🔴 names what waited a week instead of letting it vanish', async () => {
    const note = 'Not offered again: proposed 8 days ago, and never answered. Open the item to act on it.'
    proactiveDigest.mockResolvedValue(view({
      pending: [],
      carry_rule: RULE,
      no_longer_offered: [{
        action_type: 'dismiss', title: 'Thanks for the notes, nothing needed.', source: 'inbox',
        item_permalink: '', first_proposed_at: '2026-09-26T08:00:00+00:00', note,
      }],
    }))
    render(<TriageDigestCard />)
    const gone = await screen.findByRole('list', { name: 'No longer offered' })
    expect(gone.textContent).toContain('Dismiss Thanks for the notes, nothing needed.')
    expect(gone.textContent).toContain(note)
    expect(screen.getByText(RULE)).toBeTruthy()
    // Nothing is waiting, and the card still says so: what is gone is not offered.
    expect(screen.getByText('Nothing is waiting on you in this digest.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Yes' })).toBeNull()
  })
})
