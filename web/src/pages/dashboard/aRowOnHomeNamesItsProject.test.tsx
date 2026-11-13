import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { RunningList, WaitingList } from '../../lib/api'

// ── A card on Home names the project its work is in ─────────────────────────────────────────────
//
// Every item of `GET /api/waiting` and row of `GET /api/running` carries the project its work is
// in (`project_id`), and Home's Needs you and Running now cards draw it as the project's chip, a
// link to the project's page: on an approval on a line of its own above the approval card (which
// has no head row to hold it), on a question at the end of its asker's line, on a running card at
// its top right; inside neither card's own controls. From 320 to 768 px the chip is plain text,
// since the card is itself a control. Work in no project draws none, and a running card says what
// kind of work it is.

const lists = vi.hoisted(() => ({
  waiting: { waiting: [], unreadable: [] } as WaitingList,
  running: { running: [], finished: [] } as RunningList,
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => Promise.resolve(lists.waiting),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    chatSessions: () => Promise.resolve([]),
    running: () => Promise.resolve(lists.running),
    status: () => Promise.resolve({}),
    projects: () => Promise.resolve([{ id: 'p-garden', name: 'Garden' }, { id: 'p-taxes', name: 'Taxes 2026' }]),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('../../app/identity', () => ({ useIdentity: () => ({ name: '' }), firstNameOf: (n: string) => n }))
vi.mock('./PinnedTiles', () => ({ PinnedTiles: () => null }))
vi.mock('../../ui/surfaces/SurfaceOverlay', () => ({ SurfaceOverlay: () => null }))
vi.mock('./widgets/Suggestions', () => ({ Suggestions: () => null }))

import { HomeHarness, runningRow, waitingItem } from '../../test/homeHarness'

function atWidth(phone: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: phone && query === '(max-width: 768px)', media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

const needsYou = () => screen.getByRole('region', { name: 'Needs you' })
const runningNow = () => screen.getByRole('region', { name: 'Running now' })

beforeEach(() => {
  resetDataStore()
  atWidth(false)
  lists.waiting = {
    waiting: [
      waitingItem({ project_id: 'p-garden' }),
      waitingItem({
        id: 'question:q1', kind: 'question', work: 'chat:seed-order', session: 'seed-order', project_id: 'p-garden',
        brief: { questions: [{ question: 'Which vendor should I order from?' }], asked_by: 'PersonalClaw in “Seed order”' },
        consequence: 'asks', risk: '',
      }),
      waitingItem({
        id: 'question:q2', kind: 'question', work: 'chat:loose', session: 'loose',
        brief: { questions: [{ question: 'Shall I tidy the downloads?' }], asked_by: 'PersonalClaw in “Loose ends”' },
        consequence: 'asks', risk: '',
      }),
    ],
    unreadable: [],
  }
  lists.running = {
    running: [
      runningRow({
        work: 'loop:l1', kind: 'loop', loop_kind: 'code', session: '', name: 'Fix the irrigation sensor tests',
        agent: 'Code agent', since: '2026-10-21T14:02:00Z', route: '#/code/l1', project_id: 'p-garden',
      }),
      runningRow({ work: 'chat:c9', session: 'c9', name: 'File the receipts', route: '#/chat/c9' }),
    ],
    finished: [],
  }
})
afterEach(() => { vi.unstubAllGlobals() })

describe("a card on Home names its work's project", () => {
  it("an approval's chip is a line of its own above the approval card, never inside it", async () => {
    render(<HomeHarness />)
    const approval = await within(needsYou()).findByRole('group', { name: /Permission needed to run/ })
    const card = approval.closest('li')!
    const chip = await within(card).findByRole('link', { name: 'Open project Garden' })
    expect(chip.getAttribute('href')).toBe('#/projects/p-garden')
    expect(approval.contains(chip), 'inside the approval card').toBe(false)
    expect(chip.compareDocumentPosition(approval) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("a question's chip ends its asker's line, outside its answer", async () => {
    render(<HomeHarness />)
    const asker = await within(needsYou()).findByText('PersonalClaw in “Seed order”')
    const card = asker.closest('li')!
    const chip = await within(card).findByRole('link', { name: 'Open project Garden' })
    expect(asker.parentElement?.contains(chip), "on the asker's line").toBe(true)
    // A long project name never takes the asker's line: the asker keeps at least half of it.
    expect(asker.className).toContain('min-w-[50%]')
    const answer = within(card).getByRole('link', { name: /Answer it where it asks/ })
    expect(answer.contains(chip)).toBe(false)
  })

  it('a card whose work is in no project draws no chip', async () => {
    render(<HomeHarness />)
    const card = (await within(needsYou()).findByText('PersonalClaw in “Loose ends”')).closest('li')!
    expect(within(card).queryByRole('link', { name: /^Open project/ })).toBeNull()
    const loose = (await within(runningNow()).findByText('File the receipts')).closest('li')!
    expect(within(loose).queryByRole('link', { name: /^Open project/ })).toBeNull()
  })

  it('a running card carries its chip at its top right and says what kind of work it is', async () => {
    render(<HomeHarness />)
    const card = (await within(runningNow()).findByText('Fix the irrigation sensor tests')).closest('li')!
    const chip = await within(card).findByRole('link', { name: 'Open project Garden' })
    expect(chip.parentElement?.textContent).toContain('Fix the irrigation sensor tests')
    expect(card.querySelector('p[data-type="label-m"]')?.className).toContain('min-w-[50%]')
    // The kind leads the meta line, each part after a dot.
    const kind = within(card).getByText('Code session')
    expect(kind.nextElementSibling?.textContent).toBe('·')
    expect(within(card).getByText('Code agent')).toBeTruthy()
    expect(within(card).getByRole('link', { name: 'Open Fix the irrigation sensor tests — code session' })).toBeTruthy()
  })

  it('from 320 to 768 px the chip is plain text, so a card gains no target under 44 px', async () => {
    atWidth(true)
    render(<HomeHarness />)
    const asker = await within(needsYou()).findByText('PersonalClaw in “Seed order”')
    const card = asker.closest('li')!
    await waitFor(() => expect(within(card).getByText('Garden')).toBeTruthy())
    expect(within(card).queryByRole('link', { name: 'Open project Garden' })).toBeNull()
    const running = (await within(runningNow()).findByText('Fix the irrigation sensor tests')).closest('li')!
    expect(within(running).getByText('Garden')).toBeTruthy()
    expect(within(running).queryByRole('link', { name: 'Open project Garden' })).toBeNull()
  })
})
