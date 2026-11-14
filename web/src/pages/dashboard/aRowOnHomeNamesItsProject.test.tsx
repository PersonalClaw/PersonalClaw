import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { WaitingList } from '../../lib/api'

// ── A card on Needs you names the project its work is in ────────────────────────────────────────
//
// Every item of `GET /api/waiting` carries the project its work is in (`project_id`), and a Needs
// you card draws it as the project's chip, a link to the project's page: on an approval at the end
// of the approval card's own tag line ("Permission needed · From …"), on a question at the end of
// its asker's line; inside neither card's answers. From 320 to 768 px the chip is plain text, since
// the card is itself a control. Work in no project draws none. (A running card's chip is read with
// its lane, `RunningLane.test.tsx`.)

const lists = vi.hoisted(() => ({
  waiting: { waiting: [], unreadable: [] } as WaitingList,
}))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    waiting: () => Promise.resolve(lists.waiting),
    inboxOpen: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    projects: () => Promise.resolve([{ id: 'p-garden', name: 'Garden' }, { id: 'p-taxes', name: 'Taxes 2026' }]),
  },
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))

import { NeedsYouHarness, waitingItem } from '../../test/needsYouHarness'

function atWidth(phone: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: phone && query === '(max-width: 768px)', media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    dispatchEvent: () => false,
  }))
}

const needsYou = () => screen.getByRole('region', { name: 'Needs you' })

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
})
afterEach(() => { vi.unstubAllGlobals() })

describe("a card on Needs you names its work's project", () => {
  it("an approval's chip ends the approval card's tag line, outside its answers", async () => {
    render(<NeedsYouHarness />)
    const approval = await within(needsYou()).findByRole('group', { name: /Permission needed to run/ })
    const chip = await within(approval).findByRole('link', { name: 'Open project Garden' })
    expect(chip.getAttribute('href')).toBe('#/projects/p-garden')
    const tagLine = within(approval).getByText('Permission needed').closest('div')!
    expect(tagLine.contains(chip), 'on the tag line').toBe(true)
    for (const answer of within(approval).getAllByRole('button')) expect(answer.contains(chip)).toBe(false)
  })

  it("a question's chip ends its asker's line, outside its answer", async () => {
    render(<NeedsYouHarness />)
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
    render(<NeedsYouHarness />)
    const card = (await within(needsYou()).findByText('PersonalClaw in “Loose ends”')).closest('li')!
    await within(needsYou()).findAllByRole('link', { name: 'Open project Garden' })
    expect(within(card).queryByRole('link', { name: /^Open project/ })).toBeNull()
  })

  it('from 320 to 768 px the chip is plain text, so a card gains no target under 44 px', async () => {
    atWidth(true)
    render(<NeedsYouHarness />)
    const asker = await within(needsYou()).findByText('PersonalClaw in “Seed order”')
    const card = asker.closest('li')!
    await waitFor(() => expect(within(card).getByText('Garden')).toBeTruthy())
    expect(within(card).queryByRole('link', { name: 'Open project Garden' })).toBeNull()
    const approval = within(needsYou()).getByRole('group', { name: /Permission needed to run/ })
    expect(within(approval).getByText('Garden')).toBeTruthy()
    expect(within(approval).queryByRole('link', { name: 'Open project Garden' })).toBeNull()
  })
})
