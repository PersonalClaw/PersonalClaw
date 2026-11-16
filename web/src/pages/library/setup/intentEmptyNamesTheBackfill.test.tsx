/** A new knowledge intent read as broken because its empty state stated a verdict (#266).
 *
 * Intents check only items saved AFTER they exist. So a freshly made one says it has found nothing
 * even when the library is full of matches, and the one-click backfill that fixes it was a level
 * down. The reasonable conclusion was "intent matching doesn't work", and the feature got abandoned
 * on first use. Measured: the same intent went from nothing to "2 gathered" the moment the backfill
 * was found.
 *
 * The library's set-up row (#6101) says "Nothing found yet" with Run on saved items beside it, the
 * cause and the way out on one line.
 *
 * 🪤 AND THE WAY OUT IS WITHHELD WHILE PAUSED, or the fix commits the error it removes: a paused
 * intent checks nothing (measured in #542: `{evaluated: 0}` over the five items an active one
 * answers `{evaluated: 5}` for), so Run on saved items is off there, saying why.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { answers, resetGateway } from '../../../test/libraryHarness'
import type { KnowledgeIntent } from '../../../lib/api'

vi.mock('../../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../../test/libraryHarness')
  return { ...(await orig<typeof import('../../../lib/api')>()), api: gatewayApi() }
})
const { SetupPanel, PAUSED_RUN } = await import('./SetupPanel')
const { resetDataStore } = await import('../../../lib/data/store')

const ACTIVE: KnowledgeIntent = {
  id: 'intent-homelab', goal: 'Hardware failure patterns and early-warning signals in my homelab',
  enabled: true, enabled_for: [], propose_skill: false, revision: 'r1',
}

beforeEach(() => {
  resetDataStore(); resetGateway()
  answers.researchReports = { reports: [] }
  answers.knowledgeSources = { sources: [], kinds: [] }
  answers.intentRun = new Error('no run')
})
afterEach(cleanup)

async function row(intent: KnowledgeIntent) {
  answers.knowledgeIntents = { intents: [intent] }
  render(<SetupPanel nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} />)
  const name = await screen.findByText(intent.goal!)
  return within(name.closest('div.rounded-lg') as HTMLElement)
}

describe('an intent that has found nothing says why, and where the way out is', () => {
  it('🔑 says "Nothing found yet" with Run on saved items beside it', async () => {
    const r = await row(ACTIVE)
    expect(r.getByText(/Nothing found yet/)).toBeTruthy()
    const run = r.getByRole('button', { name: 'Run on saved items' })
    expect(run.getAttribute('aria-disabled') === 'true' || (run as HTMLButtonElement).disabled).toBe(false)
  })

  it('🪤 points at no backfill while PAUSED, where it would check nothing', async () => {
    const r = await row({ ...ACTIVE, enabled: false })
    const run = r.queryByRole('button', { name: 'Run on saved items' })
    // A disabled inline link is not drawn; the ⋯ holds the row, off, with its reason.
    if (run) expect(run.getAttribute('aria-disabled') === 'true' || (run as HTMLButtonElement).disabled).toBe(true)
    r.getByRole('button', { name: `More for ${ACTIVE.goal}` }).click()
    expect(await screen.findByText(PAUSED_RUN)).toBeTruthy()
  })

  it('says only what it found once it has found something, and opens the findings', async () => {
    const r = await row({ ...ACTIVE, outcome_count: 2 })
    expect(r.getByText(/^2 found/)).toBeTruthy()
    expect(r.queryByText(/Nothing found yet/)).toBeNull()
    expect(r.getByRole('button', { name: 'See findings' })).toBeTruthy()
  })
})
