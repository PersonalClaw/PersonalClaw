/**
 * The run header's status never shares the row with the run's controls (ledger 249), and a run a
 * person declined reads as their decision, not as a failure (ledger 247).
 *
 * Measured on a live gateway: on a run with a long title, "Needs you" painted over the Workspace
 * button. The status and the feed's liveness were `shrink-0` chips in the header ROW, beside a
 * title that truncates — so a title long enough to reach the controls left the chips nowhere to go
 * but on top of them. The TopBar's own rule: a chip in the row can only overlap the controls or
 * starve the title; a chip below it can only wrap. So they sit in its `below` line, and this pins
 * WHERE they render — jsdom lays nothing out, and the overlap itself is measured in a browser.
 *
 * The same browser measurement found the second half: on a live run the row held eight labelled
 * buttons in a fixed strip, which at 1280px took the whole band — the title read one letter and the
 * controls ran over it. They are the header's responsive cluster now (`HeaderActions`), which sheds
 * labels and then drops the panel toggles into a `…` menu; the last test forces a narrow header to
 * pin which controls stay.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowRunDetailData } from '../../lib/api'
import { WorkflowRunDetail } from './WorkflowRunDetail'

const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: () => Promise.resolve({ continuations: [] }),
      // The Review panel reads on mount; the narrow-header test only needs it to open.
      workflowReview: () => new Promise(() => {}),
    },
  }
})

vi.mock('./useWorkflowStream', () => ({ useWorkflowStream: () => ({ connected: true }) }))
vi.mock('./RunToolApprovals', () => ({ RunToolApprovals: () => null }))
vi.mock('./DeliverablePanel', () => ({ DeliverablePanel: () => null }))

const LONG = 'Read every statement from the last quarter and reconcile them against the ledger by hand'

function run(over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData {
  return {
    run_id: 'run-1',
    workflow: 'reconcile',
    title: LONG,
    status: 'needs_input',
    spec_version: 1,
    nodes: [{ instance_path: 'root.children[0]', node_id: 'approve', state: 'waiting' }],
    ...over,
  }
}

const mount = () => render(<WorkflowRunDetail runId="run-1" onBack={() => {}} onOpenRun={() => {}} />)

beforeEach(() => vi.clearAllMocks())

describe('the run header', () => {
  it('puts the status and the feed under the row, never in it beside the controls', async () => {
    workflowRun.mockResolvedValue(run())
    const { container } = mount()
    const status = await screen.findByText('Needs you')

    const row = container.querySelector('[data-header-left]')
    const below = container.querySelector('[data-header-below]')
    expect(row, 'the header row').toBeTruthy()
    expect(below, 'the line under the row').toBeTruthy()
    expect(below!.contains(status)).toBe(true)
    expect(row!.contains(status)).toBe(false)
    expect(below!.textContent).toContain('Streaming')
    // The row keeps the title and the controls; the title is what truncates there.
    expect(row!.textContent).toContain(LONG)
    expect(screen.getByRole('button', { name: /Workspace/ })).toBeTruthy()
  })

  it('reads a run a person declined as their decision, not as a failure', async () => {
    workflowRun.mockResolvedValue(
      run({
        status: 'declined',
        error: '“approve” was declined by Keyur, so nothing after it ran.',
        nodes: [{ instance_path: 'root.children[0]', node_id: 'approve', state: 'declined' }],
      }),
    )
    const { container } = mount()
    const sentence = await screen.findByText('“approve” was declined by Keyur, so nothing after it ran.')
    expect(sentence.className).not.toContain('text-danger')
    // The run's status says Declined, under the header row — the step's row says it too.
    expect(container.querySelector('[data-header-below]')?.textContent).toContain('Declined')
  })

  it('on a narrow header keeps the run’s own controls and drops the panel toggles into the … menu', async () => {
    // jsdom lays nothing out, so every width reads 0 and the cluster stays at FULL. These stubs are
    // a narrow header: a 320px row, 40px icon buttons, and a strip whose every tier is wider than
    // the row. What the cluster then keeps is decided by each control's priority alone.
    const spies = [
      vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(320),
      vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(40),
      vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(1200),
    ]
    try {
      workflowRun.mockResolvedValue(
        run({ status: 'running', nodes: [{ instance_path: 'root.children[0]', node_id: 'draft', state: 'running' }] }),
      )
      mount()
      const more = await screen.findByRole('button', { name: 'More actions' })
      // Pausing, steering and cancelling the run are what the page is for: they stay in the row.
      await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeTruthy())
      expect(screen.getByRole('button', { name: 'Steer' })).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeTruthy()
      // The panel toggles shed first, into the menu, and are still one click away there. By role:
      // the cluster's aria-hidden measurement probes hold every label as text too.
      expect(screen.queryByRole('button', { name: 'Review' })).toBeNull()
      fireEvent.click(more)
      const review = await screen.findByRole('button', { name: /^Review/ })
      expect(screen.getByRole('button', { name: /^Rails/ })).toBeTruthy()
      expect(screen.getByRole('button', { name: /^Introspect/ })).toBeTruthy()
      // And the row does what the button did: it opens the panel.
      fireEvent.click(review)
      expect(await screen.findByRole('region', { name: 'Review findings' })).toBeTruthy()
    } finally {
      spies.forEach((s) => s.mockRestore())
    }
  })
})
