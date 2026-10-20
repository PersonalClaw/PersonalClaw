import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'

// ── The cockpit's model pill names the model, whatever punctuation the model id carries ───────────
//
// A loop's `model` is a `<provider>:<model>` ref, and the MODEL half may carry colons and dots of
// its own (`qwen2.5vl:7b`, `anthropic/claude-3.5-sonnet`). The pill used to keep only what followed
// the LAST colon and then the LAST dot, so `ollama:qwen2.5vl:7b` read `7b` and
// `openrouter:anthropic/claude-3.5-sonnet` read `5-sonnet` — fragments that name no model. It now
// reads the ref through `lib/modelRef`, the one first-colon split Settings and onboarding use.
//
// 🔑 ASSERTED ON THE RENDERED PILL, found by the full ref it carries as its title, so this measures
// what a user sees rather than what a helper returns.

const { STORE } = vi.hoisted(() => ({ STORE: { loop: null as Loop | null } }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoop: () => (STORE.loop ? Promise.resolve(STORE.loop) : Promise.reject(new Error('no such loop'))),
    uLoopReport: () => Promise.resolve({ report: '', log: '' }),
    artifacts: () => Promise.resolve([]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
  },
}))

// jsdom has no EventSource, and a live stream is not what this measures.
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const { LoopCockpitPage } = await import('./LoopCockpitPage')

function loopWith(model: string): Loop {
  return {
    id: 'loop-model-pill',
    kind: 'goal',
    name: 'A loop with a bound model',
    task: 'Something to do',
    execution: 'solo',
    agent: 'default',
    model,
    attended: true,
    max_cycles: 5,
    idle_secs: 60,
    success_criteria: null,
    status: 'running',
    total_cycles: 1,
    error_message: null,
    created_at: 1_780_000_000,
    started_at: null,
    completed_at: null,
    kind_config: {},
  } as Loop
}

/** The model pill's visible text. The pill carries the full ref as its title. */
async function pillFor(model: string): Promise<string> {
  STORE.loop = loopWith(model)
  render(<LoopCockpitPage id="loop-model-pill" onBack={() => {}} query={{}} setQuery={() => {}} />)
  // Positive control: the cockpit resolved the loop — a page stuck on "Loading…" has no pill.
  await waitFor(() => expect(screen.getByRole('button', { name: 'Details' })).toBeTruthy())
  return (screen.getByTitle(model).textContent ?? '').trim()
}

beforeEach(() => {
  invalidateKeys('loop:', true)
  STORE.loop = null
})
afterEach(() => cleanup())

describe('the loop cockpit names the whole model, not its last segment', () => {
  it.each([
    // A colon inside the model id — the last-colon split read `7b`.
    ['ollama:qwen2.5vl:7b', 'qwen2.5vl:7b'],
    // The same model under a provider display name with a space, as `active_models.json` holds it.
    ['Local Ollama:qwen2.5vl:7b', 'qwen2.5vl:7b'],
    // A version dot inside the model id — the last-dot split read `5-sonnet`.
    ['openrouter:anthropic/claude-3.5-sonnet', 'anthropic/claude-3.5-sonnet'],
    // A dotted id the old heuristic shortened to its tail: no rule can tell a namespace dot from a
    // version dot, so the pill shows the id the provider serves, as Settings → Models does.
    ['Bedrock:global.anthropic.claude-opus-4-8', 'global.anthropic.claude-opus-4-8'],
  ])('%s → %s', async (ref, expected) => {
    expect(await pillFor(ref)).toBe(expected)
  })

  it('shows an unqualified ref whole — a bare model id is still a model id', async () => {
    expect(await pillFor('sonnet')).toBe('sonnet')
  })
})
