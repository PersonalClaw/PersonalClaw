/** Clearing a findings log keeps its intent: Clear findings… asks first,
 *  in the approved words, then deletes the findings alone; the intent keeps looking. A findings log
 *  has no Delete…, and its More holds exactly a findings log's rows. While the intents read has no
 *  count for it (failed, or not answered yet), the confirm and the skill's toast state none, never a 0. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, calls, resetGateway, route, row } from '../../test/libraryHarness'

const asked: { title: string; body: unknown }[] = []
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<typeof import('../../ui/dialog')>()),
  confirm: (o: { title: string; body: unknown }) => { asked.push(o); return Promise.resolve(true) },
}))
vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { moreRows } = await import('./libraryActions')
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

const inputs = {
  navigate: vi.fn(), writeUps: new Map<string, string>(), projects: [], chatTitle: () => '',
  pickChat: vi.fn(), pickProject: vi.fn(), offersSkill: () => true, foundBy: () => 14,
}
const log = row('findings:homelab', 'Anything that could improve my homelab')

beforeEach(() => { resetDataStore(); resetGateway(); localStorage.clear(); asked.length = 0 })
afterEach(cleanup)

describe('clearing findings keeps the intent', () => {
  it('🔑 a findings log\'s More is its own rows, with Clear findings… and no Delete…', () => {
    expect(moreRows(log, inputs).map((r) => r.label)).toEqual([
      'Copy text', 'Copy link', 'Download', 'Generate skill', 'Mark as read', 'Archive', 'Clear findings…',
    ])
  })

  it('🔑 Clear findings… asks in the approved words, then clears the findings and never the intent', async () => {
    answers.clearIntentFindings = {}
    await moreRows(log, inputs).find((r) => r.label === 'Clear findings…')!.onSelect()
    expect(asked).toEqual([expect.objectContaining({
      title: 'Clear the 14 findings?', body: "Your intent keeps looking and starts again from nothing. You can't undo this.",
    })])
    expect(calls.clearIntentFindings).toEqual([['homelab']])
    expect(calls.deleteKnowledgeIntent).toBeUndefined()
    expect(calls.libraryBulk).toBeUndefined()
  })

  it('🔴 with no count known (the intents read failed or hasn\'t answered), it asks without a number', async () => {
    answers.clearIntentFindings = {}
    await moreRows(log, { ...inputs, foundBy: () => undefined }).find((r) => r.label === 'Clear findings…')!.onSelect()
    expect(asked).toEqual([expect.objectContaining({
      title: "Clear this intent's findings?", body: "Your intent keeps looking and starts again from nothing. You can't undo this.",
    })])
    expect(asked[0].title, 'never a 0 it does not know').not.toMatch(/\b0\b/)
  })

  it('🔴 on the page, with the intents read failed, Clear findings… asks without a number', async () => {
    answers.library = answer([log])
    answers.knowledgeIntents = new Error('gateway down')
    answers.researchReports = { reports: [] }
    answers.knowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: false } }
    answers.clearIntentFindings = {}
    // The log open beside the list: its preview's More holds the same rows as the row's.
    const r = route({ item: log.ref })
    render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
    await userEvent.click(await screen.findByRole('button', { name: 'More for Findings: Anything that could improve my homelab' }))
    await userEvent.click(await screen.findByText('Clear findings…'))
    await waitFor(() => expect(asked).toHaveLength(1))
    expect(asked[0].title).toBe("Clear this intent's findings?")
  })

  it('a skill made from it names the count when it is known, and none when it is not', async () => {
    const said: string[] = []
    const onToast = (e: Event) => { said.push(String((e as CustomEvent).detail?.message ?? '')) }
    window.addEventListener('ne:toast', onToast)
    try {
      answers.generateSkillFromIntent = { skill: 'homelab-tuning' }
      await moreRows(log, inputs).find((r) => r.label === 'Generate skill')!.onSelect()
      await moreRows(log, { ...inputs, foundBy: () => undefined }).find((r) => r.label === 'Generate skill')!.onSelect()
      await vi.waitFor(() => expect(said).toHaveLength(2))
      expect(said).toEqual([
        'Made the skill “homelab-tuning” from 14 findings. Find it under Skills.',
        'Made the skill “homelab-tuning”. Find it under Skills.',
      ])
    } finally { window.removeEventListener('ne:toast', onToast) }
  })
})
