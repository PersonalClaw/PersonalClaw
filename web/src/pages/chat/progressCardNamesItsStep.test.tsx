import { describe, it, expect, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { foldEvent, foldSnapshot } from '../workflows/workflowFold'

// ── The chat's progress card names a step as the run page does: by its label ─────────────────────
//
// The run page reads each node row's `label`, the name the run's ending and failure lines give a
// step. The chat's card still printed the active node's id, and the fold it reads through dropped
// the label from every row a live event touched — so even a card that asked for the label would have
// lost it the moment the step started. The fold now keeps it, `workflow_node_started` carries it for
// a node the snapshot had not listed, and the card names the step by it.

const snapshot = (): WorkflowRunDetailData => ({
  run_id: 'r1', workflow: 'deep-research', status: 'running', spec_version: 1, error: '',
  attention: null, tokens: 0, elapsed_secs: 0,
  nodes: [
    { instance_path: 'root.children[0]', node_id: 'sweep', label: 'Sweep the sources', state: 'pending' },
    { instance_path: 'root.children[1]', node_id: 'judge', state: 'pending' },
  ],
}) as WorkflowRunDetailData

const ev = (over: Record<string, unknown>) => ({ run_id: 'r1', event_id: `e-${Math.random()}`, epoch: 0, ...over })

describe('the fold keeps a step’s name', () => {
  it('🔴 through the live events that start and finish it', () => {
    let vm = foldSnapshot(snapshot())
    vm = foldEvent(vm, 'workflow_node_started', ev({ instance_path: 'root.children[0]', node_id: 'sweep' }))
    expect(vm.nodes[0].label).toBe('Sweep the sources')
    vm = foldEvent(vm, 'workflow_node_done', ev({ instance_path: 'root.children[0]', node_id: 'sweep', status: 'done' }))
    expect(vm.nodes[0].label).toBe('Sweep the sources')
  })

  it('names a node the snapshot had not listed, from its start event', () => {
    const vm = foldEvent(foldSnapshot(snapshot()), 'workflow_node_started',
      ev({ instance_path: 'root.children[2]', node_id: 'write', label: 'Write the report' }))
    expect(vm.nodes.find((n) => n.node_id === 'write')?.label).toBe('Write the report')
  })

  it('and a step without one stays named by its id', () => {
    const vm = foldEvent(foldSnapshot(snapshot()), 'workflow_node_started',
      ev({ instance_path: 'root.children[1]', node_id: 'judge' }))
    expect(vm.nodes[1].label).toBeUndefined()
  })
})

vi.mock('../workflows/useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))

describe('the progress card', () => {
  async function mount(running: WorkflowRunDetailData['nodes'][number]) {
    vi.resetModules()
    vi.doMock('../../lib/api', async (orig) => {
      const real = await orig<typeof import('../../lib/api')>()
      return { ...real, api: { ...real.api, workflowRun: async () => ({ ...snapshot(), nodes: [running] }) } }
    })
    const { WorkflowProgressCard } = await import('./WorkflowProgressCard')
    await act(async () => {
      render(<WorkflowProgressCard refObj={{ runId: 'r1', created: true }} />)
      await new Promise((res) => setTimeout(res, 0))
    })
    return screen.findByRole('link', { name: /Inspect the current step/ })
  }

  it('🔴 names the running step by its label, not its id', async () => {
    const link = await mount({ instance_path: 'root.children[0]', node_id: 'sweep', label: 'Sweep the sources', state: 'running' })
    expect(link.textContent).toBe('Sweep the sources')
    expect(link.getAttribute('aria-label')).toBe('Inspect the current step: Sweep the sources')
    // The destination is still the node's id — the inspector is keyed on it.
    expect(link.getAttribute('href')).toContain('node=sweep')
  })

  it('falls back to the id for a step with no label', async () => {
    const link = await mount({ instance_path: 'root.children[1]', node_id: 'judge', state: 'running' })
    expect(link.textContent).toBe('judge')
  })
})
