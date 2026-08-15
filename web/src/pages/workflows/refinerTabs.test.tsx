import { describe, expect, it, vi } from 'vitest'
import { act, render, fireEvent } from '@testing-library/react'

// ── WF2LEA-6: the def page's Versions/Ledger tabs, maturity badge, and Refine-now button ──
//
// The four §6 surfaces the atom owes the template-detail page. Each is driven here against a
// mocked api so the render is real: the badge reads the maturity payload, the Versions tab lists
// the monotonic history with a working roll-back, the Run Ledger tab lists this template's runs,
// and Refine-now calls the propose-only refiner endpoint and navigates to the run it launches.

const MATURITY = { level: 3, label: 'mature', signals: {}, clean_runs: 5, evaluator_rejected: true }
const VERSIONS = [
  { version: 1, source: 'user', created_at: '2026-08-15T00:00:00Z', note: '', run_ids: [], ops_count: 0 },
  { version: 2, source: 'refiner', created_at: '2026-08-15T01:00:00Z', note: '', run_ids: ['r1'], ops_count: 1 },
]

function makeApi(overrides: Record<string, unknown> = {}) {
  return {
    workflowDef: () => Promise.resolve({
      definition: { name: 'code-project', description: 'A code project template.', root: { kind: 'sequence', id: 'root' } },
      provider: 'bundled',
    }),
    startWorkflowRun: () => Promise.resolve({ run_id: 'r1' }),
    workflowVersions: () => Promise.resolve({ versions: VERSIONS, pinned: 2, maturity: MATURITY }),
    workflowVersionDiff: () => Promise.resolve({ a: 1, b: 2, ops: [{ op: 'update_node', node_id: 'build', fields: ['retries'] }] }),
    repinWorkflowVersion: vi.fn(() => Promise.resolve({ ok: true, name: 'code-project', pinned: 1 })),
    workflowLedger: () => Promise.resolve({ name: 'code-project', runs: [
      { run_id: 'run-abc', status: 'complete', spec_version: 2, totals: { steps_completed: 3, steps_failed: 0 } },
    ], total: 1 }),
    refineWorkflow: vi.fn(() => Promise.resolve({ run_id: 'refine-run-1' })),
    ...overrides,
  }
}

async function mount(api: Record<string, unknown>, onStarted: (id: string) => void = () => {}) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({ api }))
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name="code-project" onBack={() => {}} onStarted={onStarted} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('WF2LEA-6 template-detail surfaces', () => {
  it('shows the maturity badge from the versions payload', async () => {
    const text = (await mount(makeApi())).container.textContent ?? ''
    expect(text).toContain('mature')
    expect(text).toContain('L3')
  })

  it('lists the version history with a roll-back on the non-pinned version', async () => {
    const r = await mount(makeApi())
    const tab = [...r.container.querySelectorAll('[role="tab"]')].find((b) => (b.textContent ?? '').includes('Versions'))
    await act(async () => { fireEvent.click(tab!); await new Promise((res) => setTimeout(res, 0)) })
    const text = r.container.textContent ?? ''
    expect(text).toContain('v1')
    expect(text).toContain('v2')
    expect(text).toContain('pinned') // v2 is pinned
    expect(text).toContain('Roll back') // offered on v1
    expect(text).toContain('Latest change') // the typed-op diff
  })

  it('rolls back by calling repin with the chosen version', async () => {
    const api = makeApi()
    const r = await mount(api)
    const tab = [...r.container.querySelectorAll('[role="tab"]')].find((b) => (b.textContent ?? '').includes('Versions'))
    await act(async () => { fireEvent.click(tab!); await new Promise((res) => setTimeout(res, 0)) })
    const rollback = [...r.container.querySelectorAll('button')].find((b) => (b.textContent ?? '').includes('Roll back'))
    await act(async () => { fireEvent.click(rollback!); await new Promise((res) => setTimeout(res, 0)) })
    expect(api.repinWorkflowVersion as unknown as ReturnType<typeof vi.fn>).toHaveBeenCalledWith('code-project', 1)
  })

  it('loads the Run Ledger tab lazily', async () => {
    const r = await mount(makeApi())
    const tab = [...r.container.querySelectorAll('[role="tab"]')].find((b) => (b.textContent ?? '').includes('Run Ledger'))
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
