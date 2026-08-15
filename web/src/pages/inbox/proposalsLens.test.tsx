import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { ProposalsLens } from './ProposalsLens'
import { canBatchApprove, groupKey, applyCase, proposalOf } from './proposalLens'
import type { InboxItem, InboxProposal } from '../../lib/api'

// ── INU-7 T7.3: a mixed sweep must be IMPOSSIBLE in the UI ────────────────────────────
//
// "Batch approve is offered only across same-(provenance, kind) groups" is a UI-enforced
// property, so no backend test can see it. The dangerous state is a user selecting a
// learning proposal and an app proposal, hitting one button, and applying two unrelated
// things they reviewed as one. These tests drive the real component and assert the CONTROL
// STATE for a mixed selection — not merely that a helper returns false.
//
// 🪤 The control is `aria-disabled`, NOT natively disabled (this kit's `disabledReason`
// convention): a natively disabled button leaves the tab order, so a keyboard user could
// never hear why the action is unavailable. So the assertion reads aria-disabled + the
// reason on `title`, which is what a user actually meets.

const applyInboxProposal = vi.fn((_id: string, _edited?: InboxProposal) =>
  Promise.resolve({ ok: true }),
)

vi.mock('../../lib/api', () => ({
  api: {
    applyInboxProposal: (id: string, edited?: InboxProposal) => applyInboxProposal(id, edited),
  },
}))

function makeItem(id: string, proposal: Partial<InboxProposal>, over: Partial<InboxItem> = {}) {
  return {
    id,
    channel: '',
    channel_name: '',
    message: proposal.title ?? 'A proposal',
    sender_id: '',
    sender_name: '',
    item_kind: 'proposal',
    status: 'pending',
    refs: {
      proposal: {
        title: 'A proposal',
        preview: '',
        preview_kind: 'text',
        provenance: 'learning',
        editable: false,
        apply: { workflow: { ref: 'nightly' } },
        ...proposal,
      },
    },
    ...over,
  } as InboxItem
}

const learningA = makeItem('lp-1', { title: 'Extract a skill', provenance: 'learning' })
const learningB = makeItem('lp-2', { title: 'Refine a skill', provenance: 'learning' })
const appOne = makeItem('ap-1', {
  title: 'Send the reply?',
  provenance: 'app:mail',
  apply: { app_callback: { app: 'mail', route: 'send' } },
})

function selectRow(title: RegExp) {
  fireEvent.click(screen.getByRole('checkbox', { name: title }))
}

// The batch control's name always carries "selected" or "N from <group>"; a per-row
// Approve is exactly "Approve", so this never matches a row button.
const batchButton = () => screen.getByRole('button', { name: /^Approve (\d+ from |selected)/ })

describe('INU-7 proposal lens grouping helpers', () => {
  it('groups on (provenance, item_kind) and refuses a mixed selection', () => {
    expect(groupKey(learningA)).toBe(groupKey(learningB))
    expect(groupKey(learningA)).not.toBe(groupKey(appOne))
    expect(canBatchApprove([learningA, learningB])).toBe(true)
    expect(canBatchApprove([learningA, appOne])).toBe(false)
    expect(canBatchApprove([])).toBe(false)
  })

  it('names exactly one apply case and refuses zero, two, or unknown', () => {
    const p = (apply: Record<string, Record<string, unknown>>) =>
      ({ ...proposalOf(learningA)!, apply }) as InboxProposal
    expect(applyCase(p({ workflow: { ref: 'x' } }))).toBe('workflow')
    expect(applyCase(p({}))).toBe('')
    expect(applyCase(p({ workflow: {}, action: {} }))).toBe('')
    expect(applyCase(p({ ghost: {} }))).toBe('')
  })
})

describe('INU-7 Proposals lens', () => {
  beforeEach(() => applyInboxProposal.mockClear())

  it('batch approve is unavailable — and says why — for a MIXED selection', () => {
    render(<ProposalsLens items={[learningA, appOne]} onChanged={() => {}} />)
    selectRow(/Extract a skill/)
    selectRow(/Send the reply\?/)
    const btn = batchButton()
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    expect(btn.getAttribute('title')).toMatch(/different sources or kinds/i)
    // And it does nothing if clicked anyway: no apply may escape a mixed selection.
    fireEvent.click(btn)
    expect(applyInboxProposal).not.toHaveBeenCalled()
  })

  it('batch approve is available for a SINGLE-group selection and applies each item', async () => {
    render(<ProposalsLens items={[learningA, learningB, appOne]} onChanged={() => {}} />)
    selectRow(/Extract a skill/)
    selectRow(/Refine a skill/)
    const btn = batchButton()
    expect(btn.getAttribute('aria-disabled')).not.toBe('true')
    expect(btn.textContent).toMatch(/Approve 2 from learning/)
    fireEvent.click(btn)
    // N individual applies, one per selected item — never one bulk call.
    await waitFor(() => expect(applyInboxProposal).toHaveBeenCalledTimes(2))
    expect(applyInboxProposal.mock.calls.map((c) => c[0])).toEqual(['lp-1', 'lp-2'])
  })

  it('an empty selection cannot batch approve', () => {
    render(<ProposalsLens items={[learningA, learningB]} onChanged={() => {}} />)
    const btn = batchButton()
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    expect(btn.getAttribute('title')).toMatch(/select one or more/i)
  })

  it('a failed apply is reported against the row and says it is still pending', async () => {
    applyInboxProposal.mockResolvedValueOnce({ ok: false, error: 'no workflow' } as never)
    render(<ProposalsLens items={[learningA]} onChanged={() => {}} />)
    fireEvent.click(screen.getAllByRole('button', { name: /^Approve$/ })[0])
    const msg = await waitFor(() => screen.getByText(/Not applied/))
    expect(msg.textContent).toMatch(/no workflow/)
    expect(msg.textContent).toMatch(/Still pending/)
  })

  it('edit-then-approve is offered ONLY for an editable payload, and sends the edited apply', async () => {
    const editable = makeItem('ed-1', {
      title: 'Draft reply',
      provenance: 'app:mail',
      editable: true,
      apply: { app_callback: { app: 'mail', route: 'send' } },
    })
    render(<ProposalsLens items={[learningA, editable]} onChanged={() => {}} />)
    // Non-editable row offers no Edit; the editable one does.
    expect(screen.getAllByRole('button', { name: /^Edit$/ })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }))
    const box = screen.getByRole('textbox', { name: /apply payload/i })
    fireEvent.change(box, {
      target: { value: JSON.stringify({ app_callback: { app: 'mail', route: 'send_edited' } }) },
    })
    fireEvent.click(screen.getByRole('button', { name: /Approve edited/ }))
    await waitFor(() => expect(applyInboxProposal).toHaveBeenCalled())
    const [id, edited] = applyInboxProposal.mock.calls[0]
    expect(id).toBe('ed-1')
    // What apply RECEIVES is the edited payload — not the stored one.
    expect(edited?.apply).toEqual({ app_callback: { app: 'mail', route: 'send_edited' } })
  })

  it('invalid JSON blocks the edited approve rather than posting garbage', async () => {
    const editable = makeItem('ed-2', { title: 'Draft reply', editable: true })
    render(<ProposalsLens items={[editable]} onChanged={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }))
    fireEvent.change(screen.getByRole('textbox', { name: /apply payload/i }), {
      target: { value: '{not json' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Approve edited/ }))
    expect(await waitFor(() => screen.getByText(/Not valid JSON/))).toBeTruthy()
    expect(applyInboxProposal).not.toHaveBeenCalled()
  })

  it('a proposal declaring no runnable action cannot be approved', () => {
    const dead = makeItem('dead-1', { title: 'Nothing to do', apply: {} })
    render(<ProposalsLens items={[dead]} onChanged={() => {}} />)
    const btn = screen.getByRole('button', { name: /^Approve$/ })
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    expect(btn.getAttribute('title')).toMatch(/no runnable action/i)
  })
})
