/** Read missed items and Process everything again… say what happened.
 *
 *  Measured on a home with no model bound, the old Knowledge list's Regenerate answered a refusal
 *  with nothing; once it reported, a refusal was said in the gateway's own words ("No model is set
 *  up, so…"), which is the one sentence that says what to fix. The library's "…" rows keep that: a
 *  refusal is said in those words, and an accepted run says how many items it is reading. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, calls, resetGateway, route, row } from '../../test/libraryHarness'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { ApiError } = await import('../../lib/api')
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

const SERVER_SENTENCE = 'No model is set up, so there is nothing to extract insights or entities with. Connect a model in Settings → Models & Providers → Models, then regenerate.'

let toasts: { message: string; level: string }[] = []
const onToast = (e: Event) => {
  const d = (e as CustomEvent).detail ?? {}
  toasts.push({ message: String(d.message ?? ''), level: String(d.level ?? '') })
}
beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.library = answer([row('knowledge:k1', 'Router manual')])
  toasts = []
  window.addEventListener('ne:toast', onToast)
})
afterEach(() => { cleanup(); window.removeEventListener('ne:toast', onToast) })

async function readMissed() {
  const r = route()
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  await screen.findByRole('button', { name: /^Router manual/ })
  await userEvent.click(screen.getByRole('button', { name: 'More actions' }))
  await userEvent.click(await screen.findByText('Read missed items'))
}

describe('reading again says what happened', () => {
  it('🔑 a refusal reaches the user in the gateway\'s own words', async () => {
    answers.regenerateKnowledgeIntelligence = new ApiError(SERVER_SENTENCE, 409, 'model_unresolved')
    await readMissed()
    await waitFor(() => expect(calls.regenerateKnowledgeIntelligence).toEqual([['missing']]))
    await waitFor(() => expect(toasts.map((t) => t.message).join('|')).toContain(SERVER_SENTENCE))
    expect(toasts.find((t) => t.message.includes(SERVER_SENTENCE))?.level).toBe('error')
  })

  it('🔑 an accepted run says how many items it is reading', async () => {
    answers.regenerateKnowledgeIntelligence = { queued: 2, scope: 'missing' }
    await readMissed()
    await waitFor(() => expect(toasts.map((t) => t.message)).toContain('Reading 2 items again. You can keep working.'))
  })
})
