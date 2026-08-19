import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { PromptBindings } from '../../lib/api'
import { PromptsPanel } from './PromptsPanel'

// ── Forty rows that told the user nothing ─────────────────────────────────────────────────────────
//
// Measured live on `#/settings/prompts` before this change (44 bindable contexts in the dev home):
//
//   4 rows named + described     Chat / Background / Code / Goal Loop
//   40 rows showing a RAW KEY    `nl_to_cron`, `history_compression`, `cycle_judge_skeptic`, …
//                                with no description, and `aria-label="Prompt for nl_to_cron"`
//   1 flat list                  no grouping at all, under one heading reading
//                                "System prompt bindings"
//
// The panel carried its own four-entry label table. That could never be complete: the use-case
// vocabulary is OPEN — an installed app contributes bindable contexts (four, live, from the
// knowledge app) — so the table describes only what existed when someone wrote it.
//
// 🔑 THE GROUPING WAS ALREADY DECLARED, AND THE UI WAS NEVER SENT IT. `prompt_providers/catalog.py`
// gives every bundled prompt a `category`, and its docstring says the field "groups it for the
// Settings UI"; `AppPromptUseCase.category` repeats that wording. The endpoint simply never
// serialized it. So this is not a new information architecture — it is the one the catalog has
// declared all along, finally reaching the surface it was written for.
//
// These assertions are the frontend half. `tests/test_prompt_use_cases.py` and
// `tests/test_prompt_bindings_endpoint.py` prove the vocabulary resolves a label/hint/category for
// every member (with a vacuity floor); this proves the panel RENDERS what it is sent and invents
// nothing of its own.

const promptBindings = vi.fn()
const setPromptBinding = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    promptBindings: (...a: unknown[]) => promptBindings(...a),
    setPromptBinding: (...a: unknown[]) => setPromptBinding(...a),
  },
}))

const PAYLOAD: PromptBindings = {
  use_cases: ['chat', 'nl_to_cron', 'cycle_judge'],
  default_ref: 'native:system-chat',
  categories: [
    { key: 'agent', label: 'Agent system prompts', hint: 'The default-agent system prompt for a runtime context.' },
    { key: 'internal', label: 'Internal task prompts', hint: 'One-shot LLM tasks the system runs on your behalf.' },
    { key: 'loop', label: 'Loop & orchestration prompts', hint: 'Autonomous loop and orchestration prompts.' },
  ],
  bindings: [
    {
      use_case: 'chat', ref: '', effective_ref: 'native:system-chat',
      label: 'Chat', hint: 'Interactive sessions — dashboard, Slack, CLI', category: 'agent',
    },
    {
      use_case: 'nl_to_cron', ref: '', effective_ref: 'native:task-nl-to-cron',
      label: 'Natural language → cron',
      hint: 'Convert a natural-language scheduling request into a 5-field cron expression.',
      category: 'internal',
    },
    {
      use_case: 'cycle_judge', ref: 'native:system-chat', effective_ref: 'native:system-chat',
      label: 'Cycle judge', hint: 'Scores one loop cycle.', category: 'loop',
    },
  ],
  available: [{ name: 'system-chat', title: 'System chat', kind: 'system' } as PromptBindings['available'][number]],
}

describe('every runtime context is named, described and grouped', () => {
  beforeEach(() => {
    localStorage.clear()   // useCachedData(persist: true) would carry a prior payload
    vi.clearAllMocks()
    promptBindings.mockResolvedValue(PAYLOAD)
  })

  it('renders one heading per group the backend sent, in that order', async () => {
    render(<PromptsPanel />)
    await screen.findByRole('heading', { name: 'Agent system prompts', level: 2 })
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual(['Agent system prompts', 'Internal task prompts', 'Loop & orchestration prompts'])
  })

  it('a task context shows its human label and its description, not its key', async () => {
    render(<PromptsPanel />)
    expect(await screen.findByText('Natural language → cron')).toBeTruthy()
    expect(screen.getByText('Convert a natural-language scheduling request into a 5-field cron expression.')).toBeTruthy()
    // The defect, asserted as an absence: the raw key must not appear anywhere on the panel.
    expect(screen.queryByText('nl_to_cron')).toBeNull()
    expect(screen.queryByText('cycle_judge')).toBeNull()
  })

  it("the picker's accessible name is the label, which is what a screen reader announces", async () => {
    render(<PromptsPanel />)
    // Before: "Prompt for nl_to_cron".
    expect(await screen.findByLabelText('Prompt for Natural language → cron')).toBeTruthy()
    expect(screen.getByLabelText('Prompt for Cycle judge')).toBeTruthy()
    expect(screen.queryByLabelText('Prompt for nl_to_cron')).toBeNull()
  })

  it('a group with no rows renders no heading', async () => {
    // A stale cached payload can list a category whose rows are gone; a heading over
    // nothing is worse than no heading.
    promptBindings.mockResolvedValue({
      ...PAYLOAD,
      bindings: PAYLOAD.bindings.filter((b) => b.category !== 'loop'),
    })
    render(<PromptsPanel />)
    await screen.findByRole('heading', { name: 'Agent system prompts', level: 2 })
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Loop & orchestration prompts' })).toBeNull()
    })
  })

  it('the panel holds no label table of its own', async () => {
    // The structural half of the fix: a local table is what made 40 rows anonymous, so its
    // absence is the thing to pin. If a future edit reintroduces one, this fails.
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/PromptsPanel.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'no local use-case → label map').not.toMatch(/USE_CASE_LABEL|Record<string, \{ title/)
    expect(src, 'the label comes from the binding').toMatch(/\{binding\.label\}/)
    expect(src, 'and so does the hint').toMatch(/binding\.hint/)
  })
})
