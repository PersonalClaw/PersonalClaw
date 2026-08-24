import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ApiError, type NodeInspect, type WorkflowRunDetailData } from '../../lib/api'
import { NodeInspectorDrawer } from './NodeInspectorDrawer'
import { WorkflowRunDetail } from './WorkflowRunDetail'

// ── WV-10: the node-inspector drawer renders the §5 reconstructability set ────────
//
// The drawer fetches `api.workflowRunNodeInspect(runId, nodeId)` ON OPEN and renders the six
// fields WV-9 ships (resolved_prompt / resolved_inputs / output / attempts / ledger_events) plus a
// cached badge. These pins turn on the atom's done-when, from rendered DOM:
//   1. a full payload renders all six fields + the cached badge;
//   2. a `{ref}` prompt and an `{artifact_ref}` output render as monospace chips, NOT code blocks
//      (and never trigger a second fetch — the redaction pass spilled them on purpose);
//   3. a 409 (node not terminal) and a 404 (gone) render a calm inline message, never a crash;
//   4. a terminal node's row in the run view exposes an Inspect trigger that opens the drawer.
//
// The api module is mocked at the boundary — the four api methods the two components call are
// overridden while the REAL ApiError class is kept (the drawer branches on `instanceof ApiError`,
// so a fake class would make the 409/404 discrimination silently fall through to the generic path).

const workflowRunNodeInspect = vi.fn<(runId: string, nodeId: string) => Promise<NodeInspect>>()
const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowContinuations = vi.fn<(id: string) => Promise<{ continuations: [] }>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowRunNodeInspect: (r: string, n: string) => workflowRunNodeInspect(r, n),
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: (id: string) => workflowContinuations(id),
      workflowRunStreamUrl: () => 'http://localhost/stream',
    },
  }
})

function inspect(over: Partial<NodeInspect> = {}): NodeInspect {
  return {
    run_id: 'run-1', node_id: 'draft', instance_path: 'root', state: 'done',
    resolved_prompt: 'Write the intro section.',
    resolved_inputs: { topic: 'workflows', tone: 'plain' },
    output: 'The intro, in prose.',
    attempts: [{ status: 'ok' }],
    ledger_events: [{ kind: 'step_completed' }, { kind: 'output_written' }],
    cached: true,
    ...over,
  }
}

describe('NodeInspectorDrawer', () => {
  beforeEach(() => vi.clearAllMocks())

  it('fetches on open and renders all six fields plus the cached badge', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect())
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)

    // the fetch fired for THIS node…
    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'draft'))
    // …and the six reconstructability fields + the badge all landed.
    expect(await screen.findByTestId('resolved-prompt')).toHaveTextContent('Write the intro section.')
    expect(screen.getByTestId('resolved-inputs')).toHaveTextContent('topic')
    expect(screen.getByTestId('output')).toHaveTextContent('The intro, in prose.')
    expect(within(screen.getByTestId('attempts')).getAllByRole('listitem')).toHaveLength(1)
    expect(within(screen.getByTestId('ledger-events')).getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByTestId('cached-badge')).toHaveTextContent('cached')
  })

  it('renders "fresh" when the output was not cached', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect({ cached: false }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByTestId('cached-badge')).toHaveTextContent('fresh')
  })

  it('renders a ref chip for an offloaded prompt and an artifact chip for an offloaded output — no code block, no re-fetch', async () => {
    workflowRunNodeInspect.mockResolvedValue(inspect({
      resolved_prompt: { ref: 'root::prompt' },
      output: { artifact_ref: 'artifact://big-blob' },
    }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)

    const chips = await screen.findAllByTestId('ref-chip')
    expect(chips).toHaveLength(2)
    expect(screen.getByText('root::prompt')).toBeInTheDocument()
    expect(screen.getByText('artifact://big-blob')).toBeInTheDocument()
    // the pointers are labels, not fetchers — the inline code blocks are absent for these fields…
    expect(screen.queryByTestId('resolved-prompt')).not.toBeInTheDocument()
    expect(screen.queryByTestId('output')).not.toBeInTheDocument()
    // …and only the single inspect call happened (no blob dereference).
    expect(workflowRunNodeInspect).toHaveBeenCalledTimes(1)
  })

  it('shows a not-terminal message on a 409 without throwing', async () => {
    workflowRunNodeInspect.mockRejectedValue(new ApiError('node not terminal', 409))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByText(/has not finished yet/i)).toBeInTheDocument()
    // graceful: no field blocks rendered, but the panel (and its Close) is intact.
    expect(screen.queryByTestId('resolved-prompt')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Close')).toBeInTheDocument()
  })

  it('shows a not-found message on a 404', async () => {
    workflowRunNodeInspect.mockRejectedValue(new ApiError('gone', 404))
    render(<NodeInspectorDrawer runId="run-1" nodeId="draft" onClose={() => {}} />)
    expect(await screen.findByText(/could not be found/i)).toBeInTheDocument()
  })
})

// ── SELF-VERIFICATION Success Criterion #6: the skip is VISIBLE IN THE RUNS SURFACE ──────
//
// #6 asks for "a ledger-only skip record with a one-line rationale (visible in the runs surface,
// no full run spent)". The write half shipped with SV-9; the rendering half did not — the ledger
// list showed each row's `kind` and dropped its `sha`/`impact`/`rationale`, so the Self-QA
// companion's per-commit skips arrived as N identical words. These pins are on RENDERED DOM,
// because "the field reached the component" is exactly the assertion that passed while the
// surface said nothing.
//
// The load-bearing case is SEVERAL rows under ONE node id — the companion writes one per
// test-only commit, all with the same `node_id` and the same `instance_path`. A surface that
// showed only the latest, or folded them by content, would lose skips while still looking
// populated, and a fixture with a single row cannot see that.

/** Three skips + the node's own completion, exactly as `record_triage` then the engine write
 *  them: one `step_skipped` per test-only commit under ONE instance path, then `step_completed`.
 *  Two of the three share an impact class, so a fold keyed on anything but identity drops one. */
const TRIAGE_ROWS = [
  {
    kind: 'step_skipped', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-1',
    sha: 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678', impact: 'test',
    rationale: 'assertion maintenance only — 3 test file(s), no shipped code',
  },
  {
    kind: 'step_skipped', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-2',
    sha: 'bb11223344556677889900aabbccddeeff112233', impact: 'none',
    rationale: 'no runtime surface — 2 doc/CI file(s) only',
  },
  {
    kind: 'step_skipped', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-3',
    sha: 'cc99887766554433221100ffeeddccbbaa998877', impact: 'test',
    rationale: 'assertion maintenance only — 1 test file(s), no shipped code',
  },
  { kind: 'step_completed', node_id: 'triage', instance_path: 'root.children[0]', event_id: 'r-evt-4', state: 'done' },
]

describe('the ledger list surfaces a triage skip (SELF-VERIFICATION SC#6)', () => {
  beforeEach(() => vi.clearAllMocks())

  async function openWith(rows: Array<Record<string, unknown>>) {
    workflowRunNodeInspect.mockResolvedValue(inspect({ node_id: 'triage', ledger_events: rows }))
    render(<NodeInspectorDrawer runId="run-1" nodeId="triage" onClose={() => {}} />)
    return within(await screen.findByTestId('ledger-events'))
  }

  it('renders EVERY row under one node id — three skips are three rows, not one', async () => {
    const list = await openWith(TRIAGE_ROWS)
    // Four rows in, four rows out. Nothing collapsed, nothing deduped, nothing latest-only.
    expect(list.getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getAllByTestId('ledger-event')).toHaveLength(4)
    // The count beside the label is the user-visible cross-check on the same claim.
    expect(screen.getByText('Ledger events (4)')).toBeInTheDocument()
    // Three distinct skips, each with its OWN sha — the fold-detector: two of the three share an
    // impact class, so any content-keyed collapse would render two shas here instead of three.
    expect(screen.getAllByTestId('ledger-sha')).toHaveLength(3)
    for (const row of TRIAGE_ROWS.slice(0, 3)) {
      expect(screen.getByText(row.sha as string)).toBeInTheDocument()
    }
  })

  it('renders each row\'s sha, impact and rationale — the three fields the skip record carries', async () => {
    await openWith(TRIAGE_ROWS)

    const rationales = screen.getAllByTestId('ledger-rationale').map((n) => n.textContent)
    // Each rationale is present IN FULL. An exact equality (not a substring match) is the pin
    // against a clamp: a one-line reason clipped mid-sentence reads as content and answers
    // nothing, which is the same silence the row exists to break.
    expect(rationales).toEqual([
      'assertion maintenance only — 3 test file(s), no shipped code',
      'no runtime surface — 2 doc/CI file(s) only',
      'assertion maintenance only — 1 test file(s), no shipped code',
    ])
    // The sha is passed through WHOLE — a 40-char hex a user can paste into `git show`.
    expect(screen.getByText(TRIAGE_ROWS[0].sha as string).textContent).toHaveLength(40)
    // The impact class is rendered per row, so `test` and `none` are told apart on the surface
    // rather than both reading as "skipped".
    expect(screen.getAllByTestId('ledger-impact').map((n) => n.textContent)).toEqual(['test', 'none', 'test'])
  })

  it('tells a skip apart from a node that ran — the "no full run spent" half', async () => {
    await openWith(TRIAGE_ROWS)
    // The kind vocabulary stays verbatim on the surface, so the three ledger-only skips and the
    // one row for work that actually executed are distinguishable without opening the file.
    expect(screen.getAllByTestId('ledger-kind').map((n) => n.textContent)).toEqual([
      'step_skipped', 'step_skipped', 'step_skipped', 'step_completed',
    ])
    // And a skip row carries a reason while the completion does not — `within` scopes each
    // assertion to its own row, so a rationale rendered on the wrong row fails here.
    const rows = screen.getAllByTestId('ledger-event')
    expect(within(rows[0]).getByTestId('ledger-rationale')).toBeInTheDocument()
    expect(within(rows[3]).queryByTestId('ledger-rationale')).not.toBeInTheDocument()
  })

  // VACUITY FLOOR. Every assertion above queries a testid; a renderer that emitted those elements
  // unconditionally (or a query that matched anything) would pass them all just as happily. This
  // is the same fixture shape the drawer has always been given — plain rows carrying only a kind —
  // and it must produce ZERO of the three new elements. If this test ever goes green alongside a
  // broken renderer, the pins above were measuring their own scaffolding.
  it('renders NO sha/impact/rationale element for rows that carry none', async () => {
    const list = await openWith([{ kind: 'step_completed' }, { kind: 'output_written' }])
    expect(list.getAllByRole('listitem')).toHaveLength(2)
    // the kind still renders — so the list is genuinely populated, and the absences below are
    // about the fields, not about an empty surface.
    expect(screen.getAllByTestId('ledger-kind').map((n) => n.textContent)).toEqual([
      'step_completed', 'output_written',
    ])
    expect(screen.queryAllByTestId('ledger-sha')).toHaveLength(0)
    expect(screen.queryAllByTestId('ledger-impact')).toHaveLength(0)
    expect(screen.queryAllByTestId('ledger-rationale')).toHaveLength(0)
  })
})

describe('WorkflowRunDetail node rows expose the Inspect affordance', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowContinuations.mockResolvedValue({ continuations: [] })
    workflowRunNodeInspect.mockResolvedValue(inspect())
  })

  function runWith(nodeState: string): WorkflowRunDetailData {
    // A COMPLETE (terminal) run does not open an SSE stream, so no EventSource is needed in jsdom.
    return {
      run_id: 'run-1', workflow: 'demo', status: 'complete', spec_version: 1,
      nodes: [{ instance_path: 'root', node_id: 'draft', state: nodeState }],
    }
  }

  it('offers Inspect on a terminal node and clicking it opens the drawer (fetches that node)', async () => {
    workflowRun.mockResolvedValue(runWith('done'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    const trigger = await screen.findByTitle(/Inspect this node/i)
    fireEvent.click(trigger)

    await waitFor(() => expect(workflowRunNodeInspect).toHaveBeenCalledWith('run-1', 'draft'))
    // the drawer's body is now on screen with the fetched fields.
    expect(await screen.findByTestId('node-inspector-body')).toBeInTheDocument()
    expect(await screen.findByTestId('resolved-prompt')).toBeInTheDocument()
  })

  it('does NOT offer Inspect on a non-terminal node (the endpoint would 409)', async () => {
    workflowRun.mockResolvedValue(runWith('running'))
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    // the row rendered…
    await waitFor(() => expect(workflowRun).toHaveBeenCalled())
    await screen.findByText('draft')
    // …but a running node carries no Inspect trigger.
    expect(screen.queryByTitle(/Inspect this node/i)).not.toBeInTheDocument()
    expect(workflowRunNodeInspect).not.toHaveBeenCalled()
  })
})
