import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StudiesPanel } from './StudiesPanel'
import type { StudyRow, StudyView } from '../../lib/api'

/** ES-5's study panel, on what a panel of measurements gets wrong.
 *
 *  1. **An UNMEASURED rate must not render as 0%.** `agreement: null` is precisely WHY a
 *     study is `judge_unreliable`; drawing "0%" would claim we measured a maximally
 *     position-biased judge, when the truth is that not one pair was judgeable.
 *  2. **`invalidated` and `judge_unreliable` must be as visible as a win**, and must not
 *     collapse into a shared "inconclusive" — one means the rubric moved, the other means the
 *     judge cannot be trusted, and they are fixed in different places.
 *  3. 🔴 **The locked checks and the rubric text are never rendered**, because the server never
 *     sends them (§2.2). The panel shows the count and the hash instead.
 *  4. A position-flipped pair must be shown as counting for NEITHER arm — a pair rendered as a
 *     win for whoever sat in slot A is the exact bias the swap exists to expose. */

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, evalStudy: vi.fn() } }
})

function row(over: Partial<StudyRow> = {}): StudyRow {
  return {
    study_id: 'st-3f2a91c4',
    kind: 'template_ab',
    subject: { template_id: 'wf-inbox-triage', old_version: 7, new_version: 8 },
    hypothesis: 'adding the verify gate at step 3 reduces failed runs',
    k: 5,
    registered_ts: 1_770_000_000,
    verdict: 'win',
    agreement: 0.9,
    agreement_floor: 0.6,
    win_rate: 0.8,
    low_power: false,
    fail_reason: '',
    locked_regressions: [],
    ...over,
  }
}

function view(over: Partial<StudyView> = {}): StudyView {
  return {
    study_id: 'st-3f2a91c4',
    kind: 'template_ab',
    subject: { template_id: 'wf-inbox-triage', old_version: 7, new_version: 8 },
    hypothesis: 'adding the verify gate at step 3 reduces failed runs',
    k: 2,
    inputs: ['case-1', 'case-2'],
    metric: 'primary: rubric median (pinned)',
    decision_rule: 'win_rate > 0.5; ANY locked-check regression = fail regardless',
    rubric_sha256: 'ab12cd34ef567890abcdef',
    registration_sha256: 'ffff0000',
    agreement_floor: 0.6,
    budget_usd: 2,
    registered_ts: 1_770_000_000,
    locked_check_count: 3,
    status: 'complete',
    verdict: {
      verdict: 'win',
      wins: 2,
      losses: 0,
      ties: 0,
      no_signal: 1,
      win_rate: 1,
      agreement: 0.9,
      agreement_floor: 0.6,
      judge_below_floor: false,
      low_power: false,
      fail_reason: '',
      detail: '',
      k: 2,
      decided_cases: 2,
      locked_regressions: [],
      ledger_row_written: true,
    },
    runs: [{
      case_id: 'case-1',
      outcome: 'new',
      pairs: [
        {
          case_id: 'case-1', trial: 0, slot_a_arm: 'old',
          direct_winner: 'new', swapped_winner: 'new', outcome: 'new',
          judgeable: true, agreed: true, position_flipped: false, cost_usd: 0.02,
        },
        {
          case_id: 'case-1', trial: 1, slot_a_arm: 'new',
          direct_winner: 'new', swapped_winner: 'old', outcome: 'no_signal',
          judgeable: true, agreed: false, position_flipped: true, cost_usd: 0.02,
        },
      ],
    }],
    evidence: { kind: 'study_pass' },
    ...over,
  }
}

describe('StudiesPanel', () => {
  it('renders the verdict and the agreement rate beside the floor it was judged against', () => {
    render(<StudiesPanel studies={[row()]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText('candidate wins')).toBeTruthy()
    expect(screen.getByText(/agreement 90% \(floor 60%\)/)).toBeTruthy()
    expect(screen.getByText(/win rate 80%/)).toBeTruthy()
    expect(screen.getByText(/wf-inbox-triage v7 → v8/)).toBeTruthy()
  })

  it('renders an UNMEASURED agreement as "not measured", never as 0%', () => {
    render(
      <StudiesPanel
        studies={[row({ verdict: 'judge_unreliable', agreement: null, win_rate: null })]}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('judge unreliable')).toBeTruthy()
    expect(screen.getByText(/agreement not measured/)).toBeTruthy()
    expect(screen.queryByText(/agreement 0%/)).toBeNull()
    expect(screen.queryByText(/win rate 0%/)).toBeNull()
  })

  it('names invalidated and judge_unreliable distinctly rather than as one "inconclusive"', () => {
    render(
      <StudiesPanel
        studies={[
          row({ study_id: 'st-a', verdict: 'invalidated', fail_reason: 'live_rubric_edited' }),
          row({ study_id: 'st-b', verdict: 'judge_unreliable', agreement: 0.2 }),
        ]}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('invalidated — rubric moved')).toBeTruthy()
    expect(screen.getByText('judge unreliable')).toBeTruthy()
    expect(screen.queryByText(/inconclusive/i)).toBeNull()
  })

  it('falls through to the raw verdict rather than a reassuring default it never heard of', () => {
    render(
      <StudiesPanel studies={[row({ verdict: 'some_future_verdict' })]} error={undefined} onRetry={() => {}} />,
    )
    expect(screen.getByText('some_future_verdict')).toBeTruthy()
  })

  it('distinguishes "registered, not run" from a verdict', () => {
    render(
      <StudiesPanel
        studies={[row({ verdict: null, agreement: null, win_rate: null })]}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    expect(screen.getByText('not run yet')).toBeTruthy()
  })

  it('renders an empty list as guidance, not as nothing at all', () => {
    render(<StudiesPanel studies={[]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No study has been registered/)).toBeTruthy()
  })

  it('surfaces a real read failure instead of rendering "no studies"', () => {
    render(
      <StudiesPanel studies={undefined} error={new Error('studies_unreadable')} onRetry={() => {}} />,
    )
    expect(screen.queryByText(/No study has been registered/)).toBeNull()
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
  })

  it('drills down to the per-run pairs, and shows a flipped pair as counting for neither arm',
    async () => {
      const { api } = await import('../../lib/api')
      vi.mocked(api.evalStudy).mockResolvedValue(view())
      render(<StudiesPanel studies={[row()]} error={undefined} onRetry={() => {}} />)
      await userEvent.click(screen.getByRole('button', { expanded: false }))
      await waitFor(() => expect(screen.getByText(/Per-run pairs/)).toBeTruthy())
      // The randomized slot assignment is published — that is what makes blinding auditable.
      expect(screen.getByRole('columnheader', { name: /Slot A held/ })).toBeTruthy()
      // A flipped pair is NOT a win for whoever sat in slot A.
      expect(screen.getByText(/no signal — flipped with position/)).toBeTruthy()
      expect(screen.getByText(/Position-swap agreement 90% against a 60% floor/)).toBeTruthy()
    })

  it('never renders the locked checks or the rubric text — only the count and the hash',
    async () => {
      const { api } = await import('../../lib/api')
      vi.mocked(api.evalStudy).mockResolvedValue(view())
      const { container } = render(
        <StudiesPanel studies={[row()]} error={undefined} onRetry={() => {}} />,
      )
      await userEvent.click(screen.getByRole('button', { expanded: false }))
      await waitFor(() => expect(screen.getByText(/Per-run pairs/)).toBeTruthy())
      const text = container.textContent ?? ''
      expect(text).toContain('3')
      expect(text).toContain('ab12cd34ef567890')
      // The StudyView type has no field for either, so there is nothing to render — asserted
      // here so a future handler that started sending them fails a test rather than a review.
      expect(text).not.toMatch(/required_phrases|expect_exit_code|locked\//)
    })

  it('says so when a verdict never reached results.tsv rather than implying it did',
    async () => {
      const { api } = await import('../../lib/api')
      const unpinned = view()
      vi.mocked(api.evalStudy).mockResolvedValue({
        ...unpinned,
        verdict: { ...unpinned.verdict!, ledger_row_written: false },
      })
      render(<StudiesPanel studies={[row()]} error={undefined} onRetry={() => {}} />)
      await userEvent.click(screen.getByRole('button', { expanded: false }))
      await waitFor(() => expect(screen.getByText(/is not in/)).toBeTruthy())
    })

  it('shows a locked-check regression as a fail regardless of the win rate', async () => {
    const { api } = await import('../../lib/api')
    const regressed = view()
    vi.mocked(api.evalStudy).mockResolvedValue({
      ...regressed,
      verdict: {
        ...regressed.verdict!,
        verdict: 'loss',
        win_rate: 1,
        fail_reason: 'locked_check_regression',
        locked_regressions: ['case-1/trial0/reply_file_exists'],
      },
    })
    render(
      <StudiesPanel
        studies={[row({ verdict: 'loss', win_rate: 1, fail_reason: 'locked_check_regression' })]}
        error={undefined}
        onRetry={() => {}}
      />,
    )
    await userEvent.click(screen.getByRole('button', { expanded: false }))
    await waitFor(() =>
      expect(screen.getByText(/a fail regardless of the win rate/)).toBeTruthy())
    expect(screen.getByText('case-1/trial0/reply_file_exists')).toBeTruthy()
  })
})
