import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import type { ReviewFinding, WorkflowReviewPayload, WorkflowRunDetailData, WorkflowTriageResult } from '../../lib/api'
import { ReviewTriagePanel, anchorExplanation } from './ReviewTriagePanel'
import { WorkflowRunDetail } from './WorkflowRunDetail'

// ── EI-9: the reviewer-comment triage panel (EXECUTION-ISOLATION §7, criterion 9) ────────────
//
// What these pin, in the order the criterion states them:
//   1. five findings render with their severity and their RESOLVED anchor (the diff's own
//      path:line), not the reviewer's claimed location;
//   2. an UNANCHORED finding is not offered an Accept at all, and says why in plain words — the
//      backend refuses one anyway, so an available button would teach the user the UI lies;
//   3. accepting two of five and rejecting three sends exactly those five decisions, with the two
//      accepts marked accept and nothing else;
//   4. NOTHING is submitted until Dispatch is pressed — clicking Accept posts no request. The
//      vacuity leg for that is the dispatch test above it, which asserts the SAME mock DOES get
//      called through the same button;
//   5. `nothing_accepted` renders as the correct outcome of a full rejection, not as a failure;
//   6. a failed read renders an error with a retry, never an empty state — "no findings" and "we
//      could not read the findings" are opposite facts about the same screen;
//   7. the run cockpit actually mounts the panel. A suite that only rendered the component in
//      isolation would stay green if the page stopped rendering it.

const workflowReview = vi.fn<(id: string) => Promise<WorkflowReviewPayload>>()
const workflowReviewTriage = vi.fn<(id: string, body: unknown) => Promise<WorkflowTriageResult>>()
const workflowRun = vi.fn<(id: string) => Promise<WorkflowRunDetailData>>()
const workflowContinuations = vi.fn<(id: string) => Promise<{ continuations: [] }>>()

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      workflowReview: (id: string) => workflowReview(id),
      workflowReviewTriage: (id: string, body: unknown) => workflowReviewTriage(id, body),
      workflowRun: (id: string) => workflowRun(id),
      workflowContinuations: (id: string) => workflowContinuations(id),
      workflowRunStreamUrl: () => 'http://localhost/stream',
    },
  }
})

function finding(over: Partial<ReviewFinding> = {}): ReviewFinding {
  return {
    key: 'k0', severity: 'Major', location: 'src/app.py:11', problem: 'a problem', why: '',
    recommended_fix: '', status: 'Open', auto_fixable: false, line_text: '',
    origin_run_id: 'run-1', origin_node_id: 'review', origin_session_key: '',
    anchor_state: 'anchored', anchor_reason: '',
    resolved_path: 'src/app.py', resolved_line: 11, diff_line_text: 'if not token:',
    ...over,
  }
}

/** Three anchored, two not — the criterion's five, in the shape the backend returns. */
function payload(over: Partial<WorkflowReviewPayload> = {}): WorkflowReviewPayload {
  const findings = [
    finding({ key: 'k1', severity: 'Critical', problem: 'token check swallows the log', resolved_line: 11 }),
    finding({ key: 'k2', severity: 'Minor', problem: 'CACHE is read without expanduser', auto_fixable: true, resolved_path: 'src/util.py', resolved_line: 5, location: 'src/util.py:5' }),
    finding({ key: 'k3', severity: 'Nit', problem: 'render(token) could name its argument', resolved_line: 14, location: 'src/app.py:14' }),
    finding({ key: 'k4', severity: 'Major', problem: 'the retry loop never backs off', location: 'src/app.py:999', anchor_state: 'unanchored', anchor_reason: 'line_not_in_diff', resolved_path: 'src/app.py', resolved_line: 0 }),
    finding({ key: 'k5', severity: 'Major', problem: 'the hardcoded /tmp ignores XDG', location: 'src/util.py:4', anchor_state: 'unanchored', anchor_reason: 'content_moved', resolved_path: 'src/util.py', resolved_line: 4 }),
  ]
  return {
    run_id: 'run-1', workspace: '/tmp/worktrees/run-1', diff: 'diff --git a/x b/x\n', diff_truncated: false,
    findings, counts: { total: 5, anchored: 3, unanchored: 2 }, terminal: false,
    ...over,
  }
}

function triageResult(over: Partial<WorkflowTriageResult> = {}): WorkflowTriageResult {
  return {
    run_id: 'run-1', dry_run: false,
    accepted: [], rejected: [], refused: [], untriaged: [],
    receipt: { delivered: true, reason: '', target: 'run-1', brief: 'do the thing', count: 2 },
    calibrated: 3, auto_apply_candidates: [],
    ...over,
  }
}

describe('ReviewTriagePanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders five findings against the diff’s own resolved anchors', async () => {
    workflowReview.mockResolvedValue(payload())
    render(<ReviewTriagePanel runId="run-1" />)

    await waitFor(() => expect(workflowReview).toHaveBeenCalledWith('run-1'))
    expect(await screen.findByText(/5 findings · 3 anchored · 2 unverifiable/)).toBeInTheDocument()
    // the RESOLVED anchor, not the claimed location — the resolved one is the verified one.
    expect(screen.getByTitle('src/util.py:5')).toHaveTextContent('src/util.py:5')
    expect(screen.getByText('Critical')).toBeInTheDocument()
    // `auto_fixable` is shown as a property of the finding, never as an action taken.
    expect(screen.getByTitle(/mechanical edit/i)).toBeInTheDocument()
  })

  it('offers no Accept for an unanchored finding and says why in plain words', async () => {
    workflowReview.mockResolvedValue(payload())
    render(<ReviewTriagePanel runId="run-1" />)

    await screen.findByText(/5 findings/)
    // Three anchored rows → three Accept controls. Not five.
    expect(screen.getAllByTitle(/^Accept —/)).toHaveLength(3)
    // Both unanchored reasons are rendered as sentences, not as tokens.
    expect(screen.getByText(/that line is not in the current diff/)).toBeInTheDocument()
    expect(screen.getByText(/the line moved/)).toBeInTheDocument()
    // …and every finding can still be REJECTED, including the unanchored pair.
    expect(screen.getAllByTitle(/^Reject —/)).toHaveLength(5)
  })

  it('an unmapped anchor reason falls through to the raw value rather than a friendly default', () => {
    // A default sentence would report a NEW failure mode as one of the known four.
    expect(anchorExplanation('content_moved')).toMatch(/the line moved/)
    expect(anchorExplanation('some_future_reason')).toBe('some_future_reason')
  })

  it('sends exactly the two accepts and three rejects the user chose', async () => {
    workflowReview.mockResolvedValue(payload())
    workflowReviewTriage.mockResolvedValue(triageResult())
    render(<ReviewTriagePanel runId="run-1" />)

    await screen.findByText(/5 findings/)
    const accepts = screen.getAllByTitle(/^Accept —/)
    fireEvent.click(accepts[0])
    fireEvent.click(accepts[1])
    const rejects = screen.getAllByTitle(/^Reject —/)
    fireEvent.click(rejects[2])
    fireEvent.click(rejects[3])
    fireEvent.click(rejects[4])
    expect(screen.getByText(/2 to send · 3 to record/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Dispatch decisions/i }))
    await waitFor(() => expect(workflowReviewTriage).toHaveBeenCalled())
    const body = workflowReviewTriage.mock.calls[0][1] as { decisions: Array<{ key: string; outcome: string }> }
    expect(body.decisions).toHaveLength(5)
    expect(body.decisions.filter((d) => d.outcome === 'accept')).toHaveLength(2)
    expect(body.decisions.filter((d) => d.outcome === 'reject')).toHaveLength(3)
    expect(await screen.findByText(/sent to the worker/i)).toBeInTheDocument()
    expect(screen.getByText(/3 rejections recorded in the calibration record/i)).toBeInTheDocument()
  })

  it('accepting a finding submits NOTHING until Dispatch is pressed', async () => {
    // The load-bearing negative on this surface. The vacuity leg is the test above: the same mock,
    // reached through the same button, DOES get called once Dispatch is pressed.
    workflowReview.mockResolvedValue(payload())
    render(<ReviewTriagePanel runId="run-1" />)

    await screen.findByText(/5 findings/)
    const accepts = screen.getAllByTitle(/^Accept —/)
    fireEvent.click(accepts[0])
    fireEvent.click(accepts[1])
    fireEvent.click(accepts[2])
    expect(workflowReviewTriage).not.toHaveBeenCalled()
  })

  it('reports a full rejection as the correct outcome, not as a failure', async () => {
    workflowReview.mockResolvedValue(payload())
    workflowReviewTriage.mockResolvedValue(triageResult({
      receipt: { delivered: false, reason: 'nothing_accepted', target: 'run-1', brief: '', count: 0 },
      calibrated: 5,
    }))
    render(<ReviewTriagePanel runId="run-1" />)

    await screen.findByText(/5 findings/)
    screen.getAllByTitle(/^Reject —/).forEach((b) => fireEvent.click(b))
    fireEvent.click(screen.getByRole('button', { name: /Dispatch decisions/i }))

    expect(await screen.findByText(/Nothing was accepted, so nothing was sent to the worker/i)).toBeInTheDocument()
    expect(screen.getByText(/5 rejections recorded in the calibration record/i)).toBeInTheDocument()
  })

  it('says a finished run parked the brief rather than starting a run unasked', async () => {
    workflowReview.mockResolvedValue(payload({ terminal: true }))
    workflowReviewTriage.mockResolvedValue(triageResult({
      receipt: { delivered: false, reason: 'handoff_parked', target: 'run-1', brief: 'b', count: 2 },
    }))
    render(<ReviewTriagePanel runId="run-1" />)

    await screen.findByText(/5 findings/)
    fireEvent.click(screen.getAllByTitle(/^Accept —/)[0])
    fireEvent.click(screen.getByRole('button', { name: /Dispatch decisions/i }))
    expect(await screen.findByText(/saved for a follow-up run/i)).toBeInTheDocument()
  })

  it('warns when an accepted finding went stale between render and submit', async () => {
    workflowReview.mockResolvedValue(payload())
    workflowReviewTriage.mockResolvedValue(triageResult({
      receipt: { delivered: false, reason: 'nothing_accepted', target: 'run-1', brief: '', count: 0 },
      refused: [{ ...finding({ key: 'k1', anchor_state: 'unanchored', anchor_reason: 'content_moved' }), refused_reason: 'content_moved' }],
      calibrated: 0,
    }))
    render(<ReviewTriagePanel runId="run-1" />)

    await screen.findByText(/5 findings/)
    fireEvent.click(screen.getAllByTitle(/^Accept —/)[0])
    fireEvent.click(screen.getByRole('button', { name: /Dispatch decisions/i }))
    expect(await screen.findByText(/could no longer be\s+anchored/i)).toBeInTheDocument()
  })

  it('a failed read renders an error with a retry, never an empty state', async () => {
    workflowReview.mockRejectedValueOnce(new Error('boom'))
    render(<ReviewTriagePanel runId="run-1" />)
    expect(await screen.findByText('boom')).toBeInTheDocument()
    expect(screen.queryByText(/No review findings yet/)).not.toBeInTheDocument()

    workflowReview.mockResolvedValue(payload())
    fireEvent.click(screen.getByTitle(/Try reading the findings again/i))
    expect(await screen.findByText(/5 findings/)).toBeInTheDocument()
  })

  it('renders an explicit empty state when a run has no findings', async () => {
    workflowReview.mockResolvedValue(payload({ findings: [], counts: { total: 0, anchored: 0, unanchored: 0 } }))
    render(<ReviewTriagePanel runId="run-1" />)
    expect(await screen.findByText(/No review findings yet/)).toBeInTheDocument()
  })
})

describe('the run cockpit mounts the review triage panel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workflowContinuations.mockResolvedValue({ continuations: [] })
    workflowReview.mockResolvedValue(payload())
  })

  it('opens the panel from the run header on a terminal run', async () => {
    // The CALL SITE. Rendering the component alone would stay green if the page dropped it.
    workflowRun.mockResolvedValue({
      run_id: 'run-1', workflow: 'demo', status: 'complete', spec_version: 1,
      nodes: [{ instance_path: 'root', node_id: 'build', state: 'done' }],
    })
    render(<WorkflowRunDetail runId="run-1" onBack={() => {}} />)

    const trigger = await screen.findByTitle(/Review — accept or reject/i)
    // Nothing is fetched until the panel is opened: the read costs a live `git diff`.
    expect(workflowReview).not.toHaveBeenCalled()

    fireEvent.click(trigger)
    await waitFor(() => expect(workflowReview).toHaveBeenCalledWith('run-1'))
    // `SidePanel` is a named landmark region titled by its heading — assert the NAMED region, so
    // the pin fails if the panel is mounted somewhere unlabelled.
    const panel = await screen.findByRole('region', { name: 'Review findings' })
    expect(within(panel).getByText(/5 findings · 3 anchored/)).toBeInTheDocument()
  })
})
