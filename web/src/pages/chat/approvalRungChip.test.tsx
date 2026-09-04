import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { ApprovalSegment } from './chatTypes'

// ── AUTONOMY-GUARDRAILS §4.3: the rung chip in the approval dialog ──
//
// A permission prompt is the exact moment the user decides whether to widen an
// automation's leash, so the chip that answers "what may this already do on its own?"
// must render THERE, not only on the trigger rows. Both legs are asserted because each
// guards a different failure:
//   • positive — a governed action type's ask carries its rung, WORDED BY THE SERVER
//     (`rung_meta` on the wire), so the dialog cannot drift from the ladder panel and
//     the inbox proposal that offered the promotion.
//   • vacuity — a chat tool no action type governs (bash) shows NO rung chip. A guessed
//     chip would claim governance that does not exist, which is worse than absence.
//
// The lookup is `providerRungIndex`, the same index the trigger rows use — one mapping,
// two surfaces, so they cannot disagree about which type owns a provider.

const LADDER = {
  rungs: ['draft_only', 'one_tap', 'auto_with_undo', 'autonomous'],
  rung_meta: [
    { key: 'draft_only', label: 'Drafts only', hint: '' },
    { key: 'one_tap', label: 'One tap', hint: '' },
    { key: 'auto_with_undo', label: 'Runs with undo', hint: 'Acts on its own; every act keeps a reversal handle.' },
    { key: 'autonomous', label: 'Runs on its own', hint: '' },
  ],
  types: [
    {
      key: 'action.notify',
      floor: 'draft_only',
      ceiling: 'autonomous',
      leaves_machine: false,
      providers: ['notify'],
      resolved_rung: 'auto_with_undo',
      granted_rung: 'auto_with_undo',
      held_by_incident: false,
      authority: 'Granted by you on Sep 1.',
      granted_at: '2026-09-01T00:00:00Z',
    },
  ],
}

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    autonomyLadder: () => Promise.resolve(LADDER),
  },
}))

const { ApprovalCard } = await import('./ApprovalCard')

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', ...over,
})

beforeEach(() => { sessionStorage.clear() })

describe('the rung chip in the approval dialog', () => {
  it('renders the governing rung, in the server\u2019s words, for a governed action type', async () => {
    render(<ApprovalCard seg={seg({ tool: 'notify', input: '{"title":"Digest"}' })} onAct={() => {}} />)
    // The label is the wire's `rung_meta` wording — the same string the ladder panel and
    // the promotion proposal use — not a client-side paraphrase.
    await waitFor(() => expect(screen.getByText('Runs with undo')).toBeInTheDocument())
  })

  it('shows no rung chip for a tool no action type governs', async () => {
    const { container } = render(
      <ApprovalCard seg={seg({ tool: 'bash', input: 'echo hi', risk: 'caution' })} onAct={() => {}} />,
    )
    // The risk chip still renders (the two chips are independent facts) …
    await waitFor(() => expect(screen.getByText('Caution')).toBeInTheDocument())
    // … but no rung wording appears anywhere on the card: absence is the honest answer.
    for (const m of LADDER.rung_meta) {
      expect(container.textContent).not.toContain(m.label)
    }
  })
})
