/** The two non-chat genui HOSTS, at their real call sites (AS-6 §5.4).
 *
 *  A router that works while nothing declares a producer is the failure shape this repo keeps
 *  finding, so the producers are driven through the components that actually mount them:
 *
 *  * `WorkflowAsk` — the ONE gate renderer (run view + inbox). A gate whose prompt carries a
 *    genui tree must render that tree, not print its markup, and the tree's submit must answer
 *    THAT gate (run id + resume token) rather than open a chat.
 *  * `PinnedTiles` — the dashboard band. A tile whose rendered projection is a genui tree must
 *    render in the host tree and its control must re-fire THAT tile through the fenced
 *    endpoint.
 *
 *  Both include the negative that matters: no `[UI]` chat turn is published, because one click
 *  has exactly one destination. */
import { render, act, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { WorkflowAsk } from '../../pages/workflows/WorkflowAsk'
import { PinnedTiles } from '../../pages/dashboard/PinnedTiles'
import { registerCoreGenUiComponents } from './components'
import { WIDGET_ACTION_EVENT } from '../widget/actionTurn'
import type { WorkflowContinuation } from '../../lib/api'

const resumeWorkflowRun = vi.fn(async () => ({ ok: true, approved: true }))
const tileWidgetAction = vi.fn(async () => ({ ok: true }))

const TILE_BODY =
  'Sales are up.\n<widget kind="genui" title="Sales">\nb = Button(label: "Recheck sales", action: "refresh")\n</widget>'

vi.mock('../../lib/api', () => ({
  api: {
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...(a as [])),
    tileWidgetAction: (...a: unknown[]) => tileWidgetAction(...(a as [])),
    dashboardViews: vi.fn(async () => [
      { id: 'overview', tiles: [{ ref: 'artifact:sales', order: 0, added_by: 'user' }] },
    ]),
    artifact: vi.fn(async () => ({ slug: 'sales', name: 'Sales', content: TILE_BODY })),
    refreshTile: vi.fn(async () => ({ refreshed: false })),
    resolveTile: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    artifactExists: vi.fn(async () => false),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: vi.fn(), notify: vi.fn() }))

registerCoreGenUiComponents()

const published: unknown[] = []
const onPublished = (e: Event) => { published.push((e as CustomEvent).detail) }

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:genui-hosts-test'
    URL.revokeObjectURL = () => {}
  }
})
beforeEach(() => {
  published.length = 0
  resumeWorkflowRun.mockClear()
  tileWidgetAction.mockClear()
  localStorage.clear()
  window.addEventListener(WIDGET_ACTION_EVENT, onPublished)
})
afterEach(() => window.removeEventListener(WIDGET_ACTION_EVENT, onPublished))

const GATE_PROMPT =
  'Log the expense.\n\n<widget kind="genui" title="Expense">\nf = Form(fields: ["amount"], action: "log_expense", submit: "Log expense")\n</widget>'

function continuation(prompt: string): WorkflowContinuation {
  return {
    resume_token: 'tok-abc',
    node_id: 'ask',
    instance_path: 'root/ask',
    ask: { kind: 'form', prompt, fields: [{ name: 'amount' }] },
    handoff: {},
    expires_at: Date.now() / 1000 + 600,
    expired: false,
  }
}

describe('a workflow gate whose prompt is a genui tree', () => {
  it('renders the tree and the prose around it — never the raw markup', () => {
    const { getByText, container } = render(
      <WorkflowAsk continuation={continuation(GATE_PROMPT)} runId="run-7" busy={false} onAnswer={vi.fn()} />,
    )
    expect(getByText('Log the expense.')).toBeInTheDocument()
    expect(getByText('Log expense')).toBeInTheDocument()
    expect(container.textContent).not.toContain('<widget')
    expect(container.textContent).not.toContain('Form(')
  })

  it('answers THAT gate on submit, and publishes no chat turn', async () => {
    const { getByText } = render(
      <WorkflowAsk continuation={continuation(GATE_PROMPT)} runId="run-7" busy={false} onAnswer={vi.fn()} />,
    )
    act(() => { getByText('Log expense').closest('button')!.click() })
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledTimes(1))
    expect(resumeWorkflowRun).toHaveBeenCalledWith(
      'run-7',
      expect.objectContaining({ resume_token: 'tok-abc' }),
    )
    expect(published).toEqual([])
  })

  it('falls back to the typed controls when the prompt carries no tree', () => {
    // The gate panel's existing behavior is untouched for the 100% of gates that author prose.
    const { getByText, queryByText } = render(
      <WorkflowAsk continuation={continuation('Ship it?')} runId="run-7" busy={false} onAnswer={vi.fn()} />,
    )
    expect(getByText('Ship it?')).toBeInTheDocument()
    expect(queryByText('Log expense')).toBeNull()
  })
})

describe('a dashboard tile whose body is a genui tree', () => {
  it('renders it in the host tree and re-fires THAT tile', async () => {
    const { findByText } = render(<PinnedTiles />)
    const button = await findByText('Recheck sales')
    act(() => { button.closest('button')!.click() })
    await waitFor(() => expect(tileWidgetAction).toHaveBeenCalledTimes(1))
    expect(tileWidgetAction).toHaveBeenCalledWith('overview', {
      ref: 'artifact:sales',
      action: 'refresh',
      payload: undefined,
    })
    // One click, one destination: a tile action must not ALSO stage a chat turn.
    expect(published).toEqual([])
  })
})
