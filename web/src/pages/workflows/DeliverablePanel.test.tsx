import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { WorkflowDeliverableAbsence, WorkflowRunDeliverable } from '../../lib/api'
import { ABSENT_COPY, DeliverablePanel } from './DeliverablePanel'

// ── PP-16 unit 1: a run has a document, and every way of NOT having one is rendered ──────────────
//
// The backend rail (`tests/test_pp16_run_deliverable.py`) proves the read reports a NAMED reason for
// each of five absences and never `content: ""` for a file that is not there. That guarantee is worth
// nothing if the panel then renders all five as the same blank box — which is the failure mode this
// file exists to prevent, and the one the loop side taught: a verifiable goal is FINISHED with no
// document, while a young open-ended goal is merely waiting for one, and a user who cannot tell the
// difference will either wait forever or stop trusting the surface.
//
// The absent case is the COMMON one, so it is tested first and hardest. Measured: no bundled template
// names its kind's document anywhere in its spec, so a fresh run's REPORT.md is absent because nothing
// asked for it — and the panel has to say that rather than "not written yet".

let payload: (id: string) => Promise<WorkflowRunDeliverable>

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: { ...actual.api, workflowRunDeliverable: (id: string) => payload(id) },
  }
})

/** A document slot with content. */
function present(name: string, content: string): WorkflowRunDeliverable['report'] {
  return {
    name, present: true, content, bytes: content.length, modified_at: 1_788_000_000,
    truncated: false, clipped_blobs: 0, found_in: 'run_dir', absent_reason: null,
  }
}

/** A document slot with no content and a NAMED reason. */
function absent(
  name: string | null,
  absent_reason: WorkflowDeliverableAbsence,
): WorkflowRunDeliverable['report'] {
  return {
    name, present: false, content: null, bytes: null, modified_at: null,
    truncated: false, clipped_blobs: 0, found_in: null, absent_reason,
  }
}

function body(over: Partial<WorkflowRunDeliverable> = {}): WorkflowRunDeliverable {
  return {
    run_id: 'r1',
    workflow: 'goal-pursuit-open-ended',
    report: absent('REPORT.md', 'not_written'),
    log: absent('FINDINGS.md', 'not_written'),
    derivation: {
      name: 'REPORT.md',
      reason: null,
      declared_by: { kind: 'goal', variant: 'open_ended', name: 'REPORT.md' },
    },
    roots: [{ kind: 'run_dir', path: '/tmp/home/workflows/runs/r1', exists: true }],
    instructed: false,
    ...over,
  }
}

describe('absent renders as absent — not an empty document, not an error', () => {
  it('a run with no document yet says the worker has not written it', async () => {
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(ABSENT_COPY.not_written)).toBeTruthy())
    // The name is still shown: the panel knows WHAT it is waiting for, and hiding that would make a
    // waiting run indistinguishable from one whose kind produces nothing.
    expect(screen.getAllByText(/REPORT\.md/).length).toBeGreaterThan(0)
  })

  it('and adds that nothing ever asked for it, when nothing did', async () => {
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/Waiting will not produce it/)).toBeTruthy())
  })

  it('but does NOT say that when the template does name the document', async () => {
    // Both directions. A note that always fires tells a user nothing, and would be wrong the day a
    // template starts asking for its document.
    payload = () => Promise.resolve(body({ instructed: true }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(ABSENT_COPY.not_written)).toBeTruthy())
    expect(screen.queryByText(/Waiting will not produce it/)).toBeNull()
  })

  it('a kind that produces no document says so instead of "not written"', async () => {
    payload = () =>
      Promise.resolve(
        body({
          workflow: 'goal-pursuit-verifiable',
          report: absent(null, 'kind_has_no_document'),
          derivation: {
            name: null,
            reason: 'kind_has_no_document',
            declared_by: { kind: 'goal', variant: 'verifiable', name: '' },
          },
        }),
      )
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(ABSENT_COPY.kind_has_no_document)).toBeTruthy())
    expect(screen.queryByText(ABSENT_COPY.not_written)).toBeNull()
  })

  it('renders a distinct sentence for every reason the backend can send', async () => {
    // The vocabulary rail: five DIFFERENT facts, five DIFFERENT sentences. A shared string would
    // pass every test above and lose the whole distinction.
    const reasons = Object.keys(ABSENT_COPY) as WorkflowDeliverableAbsence[]
    expect(reasons).toHaveLength(5)
    expect(new Set(Object.values(ABSENT_COPY)).size).toBe(5)
    for (const reason of reasons) {
      payload = () => Promise.resolve(body({ report: absent('REPORT.md', reason) }))
      const view = render(<DeliverablePanel runId="r1" />)
      await waitFor(() => expect(screen.getByText(ABSENT_COPY[reason])).toBeTruthy())
      view.unmount()
    }
  })

  it('names where it looked, so "not written" is checkable', async () => {
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/\/tmp\/home\/workflows\/runs\/r1/)).toBeTruthy())
  })
})

describe('a written document reads as a document', () => {
  it('renders the markdown body and where it came from', async () => {
    payload = () =>
      Promise.resolve(body({ report: present('REPORT.md', '# What I found\n\nIt works.\n') }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('What I found')).toBeTruthy())
    expect(screen.getByText(/read from this run’s run directory/)).toBeTruthy()
    // And no absence copy anywhere: present and absent are mutually exclusive on screen too.
    for (const copy of Object.values(ABSENT_COPY)) expect(screen.queryByText(copy)).toBeNull()
  })

  it('the log is a separate slot, reachable from the deliverable', async () => {
    payload = () =>
      Promise.resolve(body({ log: present('FINDINGS.md', 'cycle 1: looked around\n') }))
    render(<DeliverablePanel runId="r1" />)
    // The deliverable is absent, so the panel points at the log rather than leaving the user to
    // discover it — the absent slot is the one a user lands on.
    await waitFor(() => expect(screen.getByText(/The working log has content/)).toBeTruthy())
    // `Segmented` renders its options as `role="tab"` inside a `role="tablist"` — the tab-strip
    // contract this control has honoured since the tablist a11y fix, so the slot switch is a tab.
    fireEvent.click(screen.getByRole('tab', { name: /Log · FINDINGS\.md/ }))
    await waitFor(() => expect(screen.getByText(/looked around/)).toBeTruthy())
  })

  it('says so when a blob-shaped run was clipped', async () => {
    // Clipping is REPORTED for the same reason truncation is: a document that silently loses a
    // chunk is indistinguishable from one that never had it. The backend clips because the redactor
    // is quadratic in unbroken-token length (one 512 KB blob measured 111s on a GET).
    payload = () =>
      Promise.resolve(body({ report: { ...present('REPORT.md', 'before after'), clipped_blobs: 2 } }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/2 blobs clipped/)).toBeTruthy())
  })

  it('says so when the body was truncated for display', async () => {
    // A document that stops mid-sentence is indistinguishable from a worker that stopped writing.
    payload = () =>
      Promise.resolve(
        body({ report: { ...present('REPORT.md', 'long…'), truncated: true, bytes: 900_000 } }),
      )
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/truncated for display/)).toBeTruthy())
  })
})

describe('the filename is shown with its provenance', () => {
  it('names the kind and variant that declared it', async () => {
    // A filename with no provenance is a claim. Naming the kind is what makes it a reading of the
    // alias table — and it is the visible half of "the mapping is driven, not asserted".
    payload = () => Promise.resolve(body())
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText(/declared by the/)).toBeTruthy())
    expect(screen.getByText('goal')).toBeTruthy()
    expect(screen.getByText('open_ended')).toBeTruthy()
  })

  it('shows a dash rather than a blank when a slot has no name', async () => {
    payload = () => Promise.resolve(body({ log: absent(null, 'no_root') }))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: /Log · —/ })).toBeTruthy(),
    )
  })
})

describe('a read failure is a failure, not an empty document', () => {
  it('surfaces the error and offers a retry', async () => {
    payload = () => Promise.reject(new Error('gateway said no'))
    render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('gateway said no')).toBeTruthy())
    // And it does NOT fall through to any absence copy: "we could not ask" is not "there is none".
    for (const copy of Object.values(ABSENT_COPY)) expect(screen.queryByText(copy)).toBeNull()
  })
})

describe('the panel carries no money and no ROI axis', () => {
  it('renders no cost figure — issue #2566', async () => {
    // A loop's ledger carries no money keys, so the shared totals read $0.00 for a loop-backed run,
    // and PP-16 is what sends loop-backed runs through this surface. The backend serves no money
    // field; this asserts the FE does not invent one from elsewhere.
    payload = () => Promise.resolve(body({ report: present('REPORT.md', 'done') }))
    const { container } = render(<DeliverablePanel runId="r1" />)
    await waitFor(() => expect(screen.getByText('done')).toBeTruthy())
    expect(container.textContent ?? '').not.toMatch(/\$|cost|token|marginal|quality/i)
  })
})
