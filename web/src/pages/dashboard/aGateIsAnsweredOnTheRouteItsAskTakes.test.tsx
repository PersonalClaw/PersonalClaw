// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resetDataStore } from '../../lib/data'
import type { WaitingItem } from '../../lib/api'
import { NeedsYouHarness, waitingList } from '../../test/needsYouHarness'
import { fixtureGate, fixtureSends, type GateAsk } from '../../test/waitingGates'

// ── A workflow gate is answered on the route its ask takes ───────────────────────────────────────
//
// Each gate here is the one `GET /api/waiting` serves (`test/fixtures/waitingGates.json`, held to
// the gateway by `tests/test_a_gates_waiting_item_answers_on_the_route_its_ask_takes.py`, which also
// sends each `sends` through the real handler and sees the run move on). On its card and in its
// panel, each ask is answered as it asks: an approval with a verb on `/confirm`, a choice with its
// pick, an open choice and a form in the panel, a text gate with a typed line, an event by waking
// it, each on `/resume` with the gate's token. 🔴 Red before: the gateway listed every gate on
// `/confirm`, and the card drew each as Approve / Deny, whose answer the gate refused.

const waiting = vi.fn()
const resumeWorkflowRun = vi.fn()
const confirmWorkflowRun = vi.fn()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => waiting(),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    status: () => Promise.resolve({}),
    running: () => Promise.resolve({ running: [], finished: [] }),
    chatSessions: () => Promise.resolve([]),
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...a),
    confirmWorkflowRun: (...a: unknown[]) => confirmWorkflowRun(...a),
  },
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  sessionStorage.clear()
  resumeWorkflowRun.mockResolvedValue({ ok: true, resumed: true })
  confirmWorkflowRun.mockResolvedValue({ ok: true, resumed: true, verb: 'approve' })
})

const needsYou = () => screen.getByRole('region', { name: 'Needs you' })

/** The detail pane: the region its heading names. */
const pane = async () => {
  const headings = await screen.findAllByRole('heading', { level: 2 })
  const heading = headings.find((h) => h.getAttribute('data-type') === 'title-l')!
  return heading.closest('[role="region"]') as HTMLElement
}

/** The gate of *key*, listed, and what its route is sent: the run, and the body the gateway's
 *  handler took (the fixture's `sends`) with the gate's token. */
function listed(key: GateAsk): { item: WaitingItem; run: string; body: Record<string, unknown> } {
  const item = fixtureGate(key)
  waiting.mockResolvedValue(waitingList([item]))
  return { item, run: `run-${key}`, body: { ...fixtureSends(key), resume_token: `tok-${key}` } }
}

/** The one call that answered it: on `/confirm` for an approval, `/resume` for every other ask. */
async function sentOnItsRoute(item: WaitingItem, run: string, body: Record<string, unknown>) {
  const onConfirm = item.answer.path.endsWith('/confirm')
  const called = onConfirm ? confirmWorkflowRun : resumeWorkflowRun
  const other = onConfirm ? resumeWorkflowRun : confirmWorkflowRun
  await waitFor(() => expect(called).toHaveBeenCalledTimes(1))
  expect(called.mock.calls[0][0]).toBe(run)
  expect(called.mock.calls[0][1]).toMatchObject(body)
  expect(other).not.toHaveBeenCalled()
}

describe('on its card', () => {
  it('an approval: Approve sends its verb on the confirm route', async () => {
    const { item, run, body } = listed('approval')
    expect(item.answer.path).toBe('/api/workflows/runs/run-approval/confirm')
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Approve/ }))
    await sentOnItsRoute(item, run, body)
  })

  it('a choice: its choices, no Approve or Deny, and a pick is its answer', async () => {
    const { item, run, body } = listed('choice')
    render(<NeedsYouHarness />)
    const north = await within(needsYou()).findByRole('button', { name: /^north: / })
    expect(within(needsYou()).getByRole('button', { name: /^south: / })).toBeInTheDocument()
    expect(within(needsYou()).queryByRole('button', { name: /^(Approve|Deny)/ })).toBeNull()
    await userEvent.click(north)
    await sentOnItsRoute(item, run, body)
  })

  it('a choice that names no choices is answered in its panel, never as Approve / Deny', async () => {
    listed('choice_open')
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByText('Answer it in the panel')).toBeInTheDocument()
    expect(within(needsYou()).queryByRole('button', { name: /^(Approve|Deny)/ })).toBeNull()
  })

  it('a text ask takes its line on the card, and Enter sends it', async () => {
    const { item, run, body } = listed('text')
    render(<NeedsYouHarness />)
    const field = await within(needsYou()).findByRole('textbox', { name: /^Reply to / })
    expect(within(needsYou()).queryByRole('button', { name: /^(Approve|Deny)/ })).toBeNull()
    await userEvent.type(field, 'Orchard{Enter}')
    await sentOnItsRoute(item, run, body)
  })

  it('a form is answered in its panel, never as Approve / Deny', async () => {
    listed('form')
    render(<NeedsYouHarness />)
    expect(await within(needsYou()).findByText('Answer it in the panel')).toBeInTheDocument()
    expect(within(needsYou()).queryByRole('button', { name: /^(Approve|Deny)/ })).toBeNull()
  })

  it('an event is woken, and offers nothing to decide', async () => {
    const { item, run, body } = listed('event')
    render(<NeedsYouHarness />)
    await userEvent.click(await within(needsYou()).findByRole('button', { name: /^Wake it now: / }))
    expect(within(needsYou()).queryByRole('button', { name: /^(Approve|Deny)/ })).toBeNull()
    await sentOnItsRoute(item, run, body)
  })
})

describe('in its panel', () => {
  const open = (item: WaitingItem) => render(<NeedsYouHarness query={{ open: `waiting:${item.id}` }} />)

  it('an approval: Approve in the decide bar sends its verb on the confirm route', async () => {
    const { item, run, body } = listed('approval')
    open(item)
    const p = await pane()
    await userEvent.click(await within(p).findByRole('button', { name: /^Approve/ }))
    await sentOnItsRoute(item, run, body)
  })

  it('a choice: its choices in the decide bar, and a pick is its answer', async () => {
    const { item, run, body } = listed('choice')
    open(item)
    const p = await pane()
    await userEvent.click(await within(p).findByRole('button', { name: /^north: / }))
    expect(within(p).queryByRole('button', { name: /^(Approve|Deny)/ })).toBeNull()
    await sentOnItsRoute(item, run, body)
  })

  it('a choice that names no choices takes your words in the run’s own ask', async () => {
    const { item, run, body } = listed('choice_open')
    open(item)
    const p = await pane()
    expect(p.querySelector('[data-ask-form="hosted"]')).not.toBeNull()
    // Nothing typed, nothing sent: an open choice takes your words, not an empty pick.
    const submit = within(p).getByRole('button', { name: /Submit/ })
    await userEvent.click(submit)
    expect(resumeWorkflowRun).not.toHaveBeenCalled()
    expect(within(p).queryByRole('combobox')).toBeNull()
    await userEvent.type(within(p).getByRole('textbox', { name: 'Your answer' }), 'the east bed')
    await userEvent.click(submit)
    await sentOnItsRoute(item, run, body)
  })

  it('a text ask takes its line in the panel', async () => {
    const { item, run, body } = listed('text')
    open(item)
    const p = await pane()
    await userEvent.type(await within(p).findByRole('textbox', { name: /^Reply to / }), 'Orchard{Enter}')
    await sentOnItsRoute(item, run, body)
  })

  it('a form: its fields in the run’s own ask, sent as typed values', async () => {
    const { item, run, body } = listed('form')
    open(item)
    const p = await pane()
    expect(p.querySelector('[data-ask-form="hosted"]')).not.toBeNull()
    await userEvent.type(within(p).getByRole('textbox', { name: 'Which day' }), 'Thursday')
    const crates = within(p).getByRole('spinbutton', { name: 'How many crates' })
    await userEvent.clear(crates)
    await userEvent.type(crates, '3')
    await userEvent.tab()
    await userEvent.click(within(p).getByRole('button', { name: /Submit/ }))
    await sentOnItsRoute(item, run, body)
  })

  it('an event: Wake it now in the panel', async () => {
    const { item, run, body } = listed('event')
    open(item)
    const p = await pane()
    await userEvent.click(await within(p).findByRole('button', { name: /^Wake it now/ }))
    await sentOnItsRoute(item, run, body)
  })
})

describe('a gate listed on the confirm route that asks for more than a yes or a no', () => {
  it('is answered where it asks: no Approve / Deny, no picks, on its card or in its panel', async () => {
    // An older gateway listed every gate on `/confirm`, which sends only a yes or a no.
    const old = fixtureGate('choice')
    const item: WaitingItem = {
      ...old,
      answer: { method: 'POST', path: '/api/workflows/runs/run-choice/confirm', takes: ['approve', 'reject', 'skip', 'quit'], body: { resume_token: 'tok-choice' } },
    }
    waiting.mockResolvedValue(waitingList([item]))
    render(<NeedsYouHarness query={{ open: `waiting:${item.id}` }} />)
    const p = await pane()
    expect(within(p).getByRole('link', { name: 'Open where it asks' })).toHaveAttribute('href', '#/workflows/runs/run-choice')
    expect(screen.queryByRole('button', { name: /^(Approve|Deny|north|south)/ })).toBeNull()
    expect(confirmWorkflowRun).not.toHaveBeenCalled()
  })
})
