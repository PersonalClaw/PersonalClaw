import { describe, expect, it, vi } from 'vitest'
import { act, render, fireEvent } from '@testing-library/react'

// ── The def page's Versions/Ledger tabs, maturity badge, and Refine-now button ──
//
// The four surfaces of the template-detail page. Each is driven here against a
// mocked api so the render is real: the badge reads the maturity payload, the Versions tab lists
// the monotonic history with a working restore, the Run Ledger tab lists this template's runs,
// and Refine-now calls the propose-only refiner endpoint and navigates to the run it launches.
//
// The restore used to be a "Roll back" that called `versions/repin`, which moves a pointer no run
// start reads (`service.start_run` executes the definition's own file): the button said it rolled
// back and the next run executed the version it had not rolled back from. It now opens the editor
// on that version, whose save is a real new version.

const MATURITY = { level: 3, label: 'mature', signals: {}, clean_runs: 5, evaluator_rejected: true }
const VERSIONS = [
  { version: 1, saved_by: 'owner', created_at: '2026-08-15T00:00:00Z', note: '', run_ids: [], ops_count: 0 },
  { version: 2, saved_by: 'refiner', created_at: '2026-08-15T01:00:00Z', note: '', run_ids: ['r1'], ops_count: 1 },
]

function makeApi(overrides: Record<string, unknown> = {}) {
  return {
    workflowDef: () => Promise.resolve({
      definition: { name: 'code-project', description: 'A code project template.', version: 2, root: { kind: 'sequence', id: 'root' } },
      provider: 'bundled',
    }),
    startWorkflowRun: () => Promise.resolve({ run_id: 'r1' }),
    workflowVersions: () => Promise.resolve({ versions: VERSIONS, maturity: MATURITY }),
    workflowAutomations: () => Promise.resolve({ name: 'code-project', automations: [] }),
    workflowVersionDiff: () => Promise.resolve({ a: 1, b: 2, ops: [{ op: 'update_node', node_id: 'build', fields: ['retries'] }] }),
    workflowLedger: () => Promise.resolve({ name: 'code-project', runs: [
      { run_id: 'run-abc', status: 'complete', spec_version: 2, totals: { steps_completed: 3, steps_failed: 0 } },
    ], total: 1 }),
    refineWorkflow: vi.fn(() => Promise.resolve({ run_id: 'refine-run-1' })),
    ...overrides,
  }
}

async function mount(api: Record<string, unknown>, onStarted: (id: string) => void = () => {}, onEdit: (v?: number) => void = () => {}) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({ api }))
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name="code-project" onBack={() => {}} onStarted={onStarted} onEdit={onEdit} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('template-detail surfaces', () => {
  it('shows the maturity badge from the versions payload', async () => {
    const text = (await mount(makeApi())).container.textContent ?? ''
    expect(text).toContain('mature')
    expect(text).toContain('L3')
  })

  it('lists the version history with a restore on every version but the current one', async () => {
    const r = await mount(makeApi())
    const tab = [...r.container.querySelectorAll('[role="radio"]')].find((b) => (b.textContent ?? '').includes('Versions'))
    await act(async () => { fireEvent.click(tab!); await new Promise((res) => setTimeout(res, 0)) })
    const text = r.container.textContent ?? ''
    expect(text).toContain('v1')
    expect(text).toContain('v2')
    expect(text).toContain('current') // v2 is the definition's own version
    expect(text).toContain('Restore') // offered on v1
    expect(text).not.toContain('Roll back')
    expect(text).toContain('Latest change') // the typed-op diff
  })

  it('restores by opening the editor on the chosen version', async () => {
    const edits: Array<number | undefined> = []
    const r = await mount(makeApi(), () => {}, (v) => { edits.push(v) })
    const tab = [...r.container.querySelectorAll('[role="radio"]')].find((b) => (b.textContent ?? '').includes('Versions'))
    await act(async () => { fireEvent.click(tab!); await new Promise((res) => setTimeout(res, 0)) })
    const restore = [...r.container.querySelectorAll('button')].filter((b) => (b.textContent ?? '').includes('Restore'))
    expect(restore).toHaveLength(1) // v1 only — v2 is current
    await act(async () => { fireEvent.click(restore[0]); await new Promise((res) => setTimeout(res, 0)) })
    expect(edits).toEqual([1])
  })

  it('loads the Run Ledger tab lazily', async () => {
    const r = await mount(makeApi())
    const tab = [...r.container.querySelectorAll('[role="radio"]')].find((b) => (b.textContent ?? '').includes('Run Ledger'))
    await act(async () => { fireEvent.click(tab!); await new Promise((res) => setTimeout(res, 0)) })
    const text = r.container.textContent ?? ''
    expect(text).toContain('run-abc')
    expect(text).toContain('3 done')
  })

  it('Refine-now calls the refiner endpoint and navigates to the launched run', async () => {
    const started: string[] = []
    const api = makeApi()
    const r = await mount(api, (id: string) => { started.push(id) })
    const btn = [...r.container.querySelectorAll('button')].find((b) => (b.textContent ?? '').includes('Refine'))
    await act(async () => { fireEvent.click(btn!); await new Promise((res) => setTimeout(res, 0)) })
    expect(api.refineWorkflow as unknown as ReturnType<typeof vi.fn>).toHaveBeenCalledWith('code-project')
    expect(started).toEqual(['refine-run-1'])
  })
})
