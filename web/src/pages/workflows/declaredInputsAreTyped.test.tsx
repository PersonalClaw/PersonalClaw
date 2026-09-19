/** The workflow launch form renders and posts its DECLARED input types (#327).
 *
 *  Every declared input rendered as a free-text box whatever its type. So `knowledge-lint`'s
 *  `apply` (boolean, default false) and `min_cluster_size` (number, default 5) took the strings
 *  `banana` and `not-a-number`, showed no validation, left Run enabled, and posted them verbatim —
 *  accepted `202`.
 *
 *  Two things made that possible and both are fixed here: the state was typed
 *  `Record<string, string>`, so a free-text box was the only control it COULD hold; and the page
 *  had its own renderer while the tool inspector and the trigger action form share `SchemaField`,
 *  which has produced a switch for a boolean and a spinbutton for a number all along. This file
 *  asserts the launch form now speaks through that renderer — the third surface to hit this gap
 *  (#269 was the triggers form dropping array inputs).
 *
 *  Backend enforcement is the other half and is NOT a duplicate of this: a client-side check is a
 *  UX affordance, not a control, and `POST /api/workflows/runs` has other callers (the MCP tool,
 *  the chat planner, triggers, an A2A agent). See `tests/test_workflow_declared_input_types.py`.
 */
import { describe, expect, it, vi } from 'vitest'
import { act, render, fireEvent } from '@testing-library/react'

/** `knowledge-lint`'s real declaration, copied from `workflows/bundled/knowledge-lint/workflow.json`. */
const INPUTS = {
  apply: {
    type: 'boolean', required: false, default: false,
    help: 'Write the consolidated items and archive their inputs.',
  },
  min_cluster_size: { type: 'number', required: false, default: 5, help: 'Smallest cluster to act on.' },
}

function makeApi(overrides: Record<string, unknown> = {}, inputs: unknown = INPUTS) {
  return {
    workflowDef: () => Promise.resolve({
      definition: { name: 'knowledge-lint', root: { kind: 'sequence', id: 'root' }, inputs },
      provider: 'bundled',
    }),
    startWorkflowRun: vi.fn(() => Promise.resolve({ run_id: 'r1' })),
    workflowVersions: () => Promise.resolve({ versions: [], pinned: null, maturity: null }),
    workflowLedger: () => Promise.resolve({ name: 'knowledge-lint', runs: [], total: 0 }),
    refineWorkflow: vi.fn(() => Promise.resolve({ run_id: 'x' })),
    publishWorkflowToA2A: vi.fn(() => Promise.resolve({ a2a_published: false })),
    ...overrides,
  }
}

async function mount(api: Record<string, unknown>) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({ api }))
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name="knowledge-lint" onBack={() => {}} onStarted={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

const runButton = (r: ReturnType<typeof render>) =>
  [...r.container.querySelectorAll('button')].find((b) => (b.textContent ?? '').includes('Run'))!

async function click(el: Element) {
  await act(async () => { fireEvent.click(el); await new Promise((res) => setTimeout(res, 0)) })
}

// ── the controls ──────────────────────────────────────────────────────────────────────────

describe('a declared type picks the control', () => {
  it('🔑 a boolean renders a switch, not a text box', async () => {
    const r = await mount(makeApi())
    const sw = r.container.querySelector('[role="switch"][aria-label="apply"]')
    expect(sw).toBeTruthy()
    expect(sw!.getAttribute('aria-checked')).toBe('false') // seeded from `default: false`
  })

  it('🔑 a number renders a numeric input', async () => {
    const r = await mount(makeApi())
    const num = [...r.container.querySelectorAll('input[type="number"]')]
    expect(num.length).toBe(1)
    expect((num[0] as HTMLInputElement).value).toBe('5') // seeded from `default: 5`
  })

  it('🪤 vacuity floor — a string input is STILL a text box', async () => {
    // Every assertion above would also pass on a form that rendered nothing at all, and a fix that
    // turned every field into a switch would pass the first one.
    const r = await mount(makeApi({}, { target: { type: 'string', required: true, help: 'What to lint.' } }))
    const text = [...r.container.querySelectorAll('input')].filter(
      (i) => (i as HTMLInputElement).type !== 'number' && i.getAttribute('role') !== 'switch',
    )
    expect(text.length).toBe(1)
    expect(r.container.querySelector('[role="switch"][aria-label="target"]')).toBeNull()
  })

  it('the declared type and requiredness are visible, the way the tool inspector shows them', async () => {
    const r = await mount(makeApi({}, {
      target: { type: 'string', required: true }, ...INPUTS,
    }))
    const text = r.container.textContent ?? ''
    expect(text).toContain('boolean')
    expect(text).toContain('number')
    expect(text).toContain('required')
    expect(text).toContain('Smallest cluster to act on.') // `help` survives the adapter
  })

  it('a template with no declared inputs renders no Inputs section', async () => {
    const r = await mount(makeApi({}, {}))
    expect(r.container.textContent).not.toContain('Inputs')
  })
})

// ── what goes over the wire ───────────────────────────────────────────────────────────────

describe('what is posted', () => {
  it('🔑 posts real JSON types, not the strings a text box would have produced', async () => {
    const api = makeApi()
    const r = await mount(api)
    await click(r.container.querySelector('[role="switch"][aria-label="apply"]')!)
    const num = r.container.querySelector('input[type="number"]') as HTMLInputElement
    await act(async () => { fireEvent.change(num, { target: { value: '2' } }) })
    await click(runButton(r))

    expect(api.startWorkflowRun).toHaveBeenCalledWith({
      name: 'knowledge-lint',
      inputs: { apply: true, min_cluster_size: 2 },
    })
  })

  it('a boolean left at its default is posted as `false`, not omitted', async () => {
    // It used to be dropped by a `value !== ''` filter, so the run record could not distinguish
    // "the user left `apply` off" from "the user was never asked".
    const api = makeApi()
    await click(runButton(await mount(api)))
    expect(api.startWorkflowRun).toHaveBeenCalledWith({
      name: 'knowledge-lint',
      inputs: { apply: false, min_cluster_size: 5 },
    })
  })

  it('an optional input with no default is still dropped when left blank', async () => {
    // The one behaviour of the old hand-rolled loop worth keeping: an empty optional must not post
    // `""`, or the server stores a value the user never entered.
    const api = makeApi({}, { focus: { type: 'string', required: false } })
    await click(runButton(await mount(api)))
    expect(api.startWorkflowRun).toHaveBeenCalledWith({ name: 'knowledge-lint', inputs: {} })
  })

  it('an array input is parsed before it leaves — #269 over again on this surface', async () => {
    const api = makeApi({}, { commits: { type: 'array', required: true } })
    const r = await mount(api)
    const box = r.container.querySelector('textarea') as HTMLTextAreaElement
    await act(async () => { fireEvent.change(box, { target: { value: '["abc", "def"]' } }) })
    await click(runButton(r))
    expect(api.startWorkflowRun).toHaveBeenCalledWith({
      name: 'knowledge-lint', inputs: { commits: ['abc', 'def'] },
    })
  })

  it('malformed JSON in an array field is reported and NOTHING is posted', async () => {
    const api = makeApi({}, { commits: { type: 'array', required: true } })
    const r = await mount(api)
    const box = r.container.querySelector('textarea') as HTMLTextAreaElement
    await act(async () => { fireEvent.change(box, { target: { value: '[oops' } }) })
    await click(runButton(r))
    expect(api.startWorkflowRun).not.toHaveBeenCalled()
  })
})

// ── the Run button ────────────────────────────────────────────────────────────────────────

describe('Run says why it is off', () => {
  it('🔑 is disabled while a required input is blank, and names it', async () => {
    // The issue measured `disabled: false` with junk in both fields: the only feedback was a toast
    // after the round-trip.
    const r = await mount(makeApi({}, { target: { type: 'string', required: true } }))
    const run = runButton(r)
    expect(run.getAttribute('aria-disabled') ?? String((run as HTMLButtonElement).disabled)).toMatch(/true/)
    expect(run.getAttribute('title') ?? '').toContain('target')
  })

  it('🪤 vacuity floor — it is ENABLED once the required input is filled', async () => {
    // A permanently-disabled Run button would pass the assertion above and ship a dead page.
    const api = makeApi({}, { target: { type: 'string', required: true } })
    const r = await mount(api)
    const box = r.container.querySelector('input') as HTMLInputElement
    await act(async () => { fireEvent.change(box, { target: { value: 'docs/' } }) })
    await click(runButton(r))
    expect(api.startWorkflowRun).toHaveBeenCalledWith({
      name: 'knowledge-lint', inputs: { target: 'docs/' },
    })
  })

  it('a `false` required boolean is PRESENT, not missing', async () => {
    // `missingRequired`'s rule, restated where it bites: a bare `!value` would count a legitimate
    // `false` as unfilled and disable Run on a form the user has completed.
    const api = makeApi({}, { apply: { type: 'boolean', required: true, default: false } })
    const r = await mount(api)
    await click(runButton(r))
    expect(api.startWorkflowRun).toHaveBeenCalledWith({
      name: 'knowledge-lint', inputs: { apply: false },
    })
  })
})
