/**
 * #521 — the "ranked deep recall" tab must say HOW it ranked.
 *
 * The tab rendered a pre block and nothing else, so a recall scored by the vector arm
 * and a recall that fell back to keyword search looked identical. Measured against a
 * real gateway on an isolated home, the payload was the reason: the key set was
 * ['deep','query','result'] in BOTH states, and `deep` is the request's own depth flag
 * echoed back. The sibling entity-graph section was the only surface in the product
 * that named this degradation, and it authored the sentence itself.
 *
 * Now one backend owner (personalclaw/memory_ranking.py) composes the sentence and
 * every recall-ish endpoint serves it; the panel renders `ranking.summary` verbatim
 * through one component. The Python side of the rail (the derived capability and
 * endpoint censuses, and "the clause exists in exactly one file") lives in
 * tests/test_recall_ranking_disclosure.py.
 *
 * This file drives the REAL panel: type a question, click Recall, read the screen.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const DEGRADED = {
  vector: false,
  full_text_search: true,
  entity_graph: true,
  mode: 'keyword' as const,
  degraded: true,
  label: 'keyword search + entity graph',
  summary: 'Keyword-ranked only: no embedding model is bound, so recall falls back to search alone.',
}
const HEALTHY = {
  vector: true,
  full_text_search: true,
  entity_graph: true,
  mode: 'semantic' as const,
  degraded: false,
  label: 'semantic ranking + keyword search + entity graph',
  summary: 'Ranked by semantic similarity over embeddings, following entity-graph links.',
}

/** The recall body is IDENTICAL in both states on purpose: the results looking the same
 *  is precisely why the payload had to carry the disclosure. */
const RESULT = 'project.deploy_decision: ship behind a flag'

let ranking: typeof DEGRADED | typeof HEALTHY = DEGRADED

vi.mock('../../lib/api', () => ({
  api: {
    memoryRecall: () => Promise.resolve({ result: RESULT, query: 'q', deep: false, ranking }),
    memorySettings: () => Promise.resolve({}),
    memoryStats: () => Promise.resolve({}),
    personalclawConfig: () => Promise.resolve({}),
  },
}))

const { MemoryPanel } = await import('./MemoryPanel')

function renderRecallTab() {
  return render(<MemoryPanel query={{ tab: 'recall' }} setQuery={() => {}} />)
}

async function askAndRead(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByLabelText(/question for deep memory recall/i),
    'what did I decide about the deploy',
  )
  await user.click(screen.getByRole('button', { name: /^recall$/i }))
  return waitFor(() => screen.getByTestId('recall-ranking'))
}

describe('the deep-recall tab says how it actually ranked (#521)', () => {
  beforeEach(() => { ranking = DEGRADED })

  it('names the missing embedding model when the ranking degraded', async () => {
    const user = userEvent.setup()
    renderRecallTab()
    const note = await askAndRead(user)
    expect(note.textContent).toBe(DEGRADED.summary)
    // The results themselves are on screen too — the disclosure annotates, never replaces.
    await waitFor(() => expect(screen.getByText(RESULT)).toBeTruthy())
  })

  it('renders DIFFERENT text when the vector arm did run — the whole defect', async () => {
    const user = userEvent.setup()
    const degradedView = renderRecallTab()
    const degradedText = (await askAndRead(user)).textContent
    degradedView.unmount()

    ranking = HEALTHY
    renderRecallTab()
    const healthyText = (await askAndRead(user)).textContent

    expect(degradedText).not.toBe(healthyText)
    expect(healthyText).toBe(HEALTHY.summary)
  })

  it('says nothing at all before a recall has run', () => {
    renderRecallTab()
    expect(screen.queryByTestId('recall-ranking')).toBeNull()
  })

  it('renders the server sentence verbatim, never a locally composed one', async () => {
    const user = userEvent.setup()
    renderRecallTab()
    const note = await askAndRead(user)
    // No prefix, no suffix, no re-wording: the backend owns the wording so that the
    // Recall tab and the entity-graph section cannot describe one fact two ways.
    expect(note.textContent).toBe(DEGRADED.summary)
  })
})

describe('the panel has exactly one renderer for the disclosure', () => {
  // Source-level, and comments are STRIPPED first: this file's own explanation names the
  // old hand-written copy, and a rail that counts its own prose proves nothing.
  const src = readFileSync(join(process.cwd(), 'src/pages/settings/MemoryPanel.tsx'), 'utf8')
  const code = src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

  it('defines RankingNote once and routes every surface through it', () => {
    expect(code.match(/function RankingNote\b/g)?.length).toBe(1)
    // Recall, Inspect, and both entity-graph branches (off / on-but-degraded).
    expect(code.match(/<RankingNote\b/g)?.length).toBe(4)
  })

  it('reads the sentence from the payload rather than building one', () => {
    // The only reference to a ranking's prose is the one inside RankingNote.
    expect(code.match(/ranking\.summary/g)?.length).toBe(1)
  })
})
