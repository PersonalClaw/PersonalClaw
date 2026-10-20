// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import type { WorkflowInputParam } from '../../lib/api'
import { KIND_TO_TEMPLATE } from './containerKey'
import { intentInput } from './templateStart'

// ── "Start from template" keeps what was typed ────────────────────────────────────────────────
//
// Measured: Workflows › "Start from template" → "What do you want to do?" → a question → Continue
// → the "Run deep-research" form opened with its Question EMPTY and Run disabled, so the question
// was typed a second time. The page resolved the sentence to a template and dropped the sentence.
// Now it opens in the input the template takes its job in — the one it marks `loop_field: "task"`,
// else its first required text input — for every template the picker can suggest.

/** A shipped template's declared inputs, read from the bundled definition itself. */
function shippedInputs(name: string): Record<string, WorkflowInputParam> {
  const path = join(process.cwd(), '..', 'src/personalclaw/workflows/bundled', name, 'workflow.json')
  return (JSON.parse(readFileSync(path, 'utf8')) as { inputs: Record<string, WorkflowInputParam> }).inputs
}

const INTENT = 'Research the ways a small Python service can cache HTTP responses, and cite the sources you read.'

const workflowDef = vi.fn()
const startWorkflowRun = vi.fn()
const promptInput = vi.fn()
/** The run form, answered the way a user who presses Run without typing answers it. */
const promptForm = vi.fn(async (opts: { fields: Array<{ name: string; initial?: string }> }) =>
  Object.fromEntries(opts.fields.map((f) => [f.name, f.initial ?? ''])))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    workflowDefs: () => Promise.resolve({ defs: Object.values(KIND_TO_TEMPLATE).map((name) => ({ name, description: '' })), total: 5 }),
    workflowRuns: () => Promise.resolve({ runs: [], total: 0 }),
    workflowSurfacing: () => Promise.resolve({ defs: [], total: 0, findings: [] }),
    workflowDef: (...a: unknown[]) => workflowDef(...a),
    startWorkflowRun: (...a: unknown[]) => startWorkflowRun(...a),
  },
}))
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  promptInput: (...a: unknown[]) => promptInput(...a),
  promptForm: (opts: Parameters<typeof promptForm>[0]) => promptForm(opts),
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

import { WorkflowsListPage } from './WorkflowsListPage'

beforeEach(() => {
  vi.clearAllMocks()
  resetDataStore()
  workflowDef.mockImplementation((name: string) => Promise.resolve({ definition: { name, inputs: shippedInputs(name), root: {} } }))
  startWorkflowRun.mockResolvedValue({ run_id: 'run-71c2' })
  promptInput.mockResolvedValue(INTENT)
})

describe('Start from template', () => {
  it('opens the chosen template with the question already in its Question field', async () => {
    const navigate = vi.fn()
    render(<WorkflowsListPage sub="" navEpoch={0} query={{}} setQuery={() => {}} navigate={navigate} />)
    fireEvent.click((await screen.findAllByRole('button', { name: 'Start from template' }))[0])

    await waitFor(() => expect(promptForm).toHaveBeenCalled())
    expect(workflowDef).toHaveBeenCalledWith('deep-research')
    const question = promptForm.mock.calls[0][0].fields.find((f) => f.name === 'question')
    expect(question?.initial, 'the form opened with the question dropped').toBe(INTENT)
    await waitFor(() => expect(startWorkflowRun).toHaveBeenCalled())
    expect(startWorkflowRun.mock.calls[0][0]).toMatchObject({ name: 'deep-research', inputs: { question: INTENT } })
    // The run page opens once the start has answered, a step after the start was asked for.
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('workflows/runs/run-71c2'))
  })
})

describe('the input an intent fills', () => {
  it('is the one each template the picker can suggest takes its job in', () => {
    const into = Object.fromEntries(Object.values(KIND_TO_TEMPLATE).map((name) => [name, intentInput(shippedInputs(name))]))
    expect(into).toEqual({
      'general-project': 'task',
      'goal-pursuit-open-ended': 'task',
      'code-project': 'task',
      'design-project': 'brief',
      'deep-research': 'question',
    })
  })

  it('is the declared intake before any other input, and none when nothing takes text', () => {
    expect(intentInput({ topic: { type: 'string', required: true }, ask: { type: 'string', loop_field: 'task' } })).toBe('ask')
    expect(intentInput({ n: { type: 'number', required: true }, note: { type: 'string' } })).toBe('')
    expect(intentInput(undefined)).toBe('')
  })
})
