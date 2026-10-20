import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { approvalSegmentOf } from './approvalSegment'
import { hydrateTurns, type ApprovalSegment, type HistMsg } from './chatTypes'
import { applyApprovalFrame } from './liveToolFrames'

// ── A Deny that ends the agent's turn says so before it is pressed ──────────────────────────────
//
// An agent CLI asking for an escalation can offer only one refusal, and that refusal ends its
// turn. Measured: she denied a read-only `git show` on such a card, and the agent stopped without
// the review she asked for; nothing on the card had said a Deny would do more than decline the
// call. The gateway now says it on the card (`turn_endings.deny_effect`), and the card shows that
// sentence above both verbs, wherever the card is drawn: live from the `approval` frame, after a
// reload from the persisted permission row, and from the approvals registry on every other surface.

const EFFECT =
  'Codex offers no way to skip only this step: Deny ends its turn, and PersonalClaw then asks it to carry on without it.'

const frame = { request_id: '900', tool: 'Run command', tool_input: 'git show --stat HEAD', risk: 'safe', deny_effect: EFFECT }

describe('the sentence reaches the card from every place a card is drawn', () => {
  it('is on the live card, the reloaded card and a registry row alike', () => {
    const live = applyApprovalFrame([], frame)[0] as ApprovalSegment
    const rows: HistMsg[] = [
      { role: 'user', content: 'review the last commit' },
      {
        role: 'permission', content: 'Run command',
        meta: { approval_id: '900', tool_input: 'git show --stat HEAD', risk: 'safe', deny_effect: EFFECT },
      },
    ]
    const reloaded = hydrateTurns(rows, true).flatMap((t) => t.segments).find((s) => s.kind === 'approval') as ApprovalSegment
    const listed = approvalSegmentOf({
      id: 'dashboard:chat-2:900', request_id: '900', tool: 'Run command', tool_input: 'git show --stat HEAD',
      tool_purpose: '', risk: 'safe', blast_radius: null, grant_agent: '', deny_effect: EFFECT,
      session: 'chat-2', source_label: 'chat “Review”',
    })
    expect([live.denyEffect, reloaded.denyEffect, listed.denyEffect]).toEqual([EFFECT, EFFECT, EFFECT])
  })

  it('is absent where a Deny only declines the call', () => {
    const live = applyApprovalFrame([], { ...frame, deny_effect: '' })[0] as ApprovalSegment
    expect(live.denyEffect).toBeUndefined()
  })
})

describe('the card says it before either verb', () => {
  it('shows the sentence, and the Deny says it to a reader that only hears the button', () => {
    const onAct = vi.fn()
    const seg: ApprovalSegment = { kind: 'approval', id: '900', tool: 'Run command', input: 'git show --stat HEAD', risk: 'safe', denyEffect: EFFECT }
    const { container } = render(<ApprovalCard seg={seg} onAct={onAct} />)

    expect(container.textContent).toContain(EFFECT)
    const deny = screen.getByRole('button', { name: `Deny Run command — nothing is remembered. ${EFFECT}` })
    // Read before the verbs: the sentence precedes the action row in the card's order.
    const said = screen.getByText(EFFECT)
    expect(said.compareDocumentPosition(deny) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(deny)
    expect(onAct).toHaveBeenCalledWith('900', 'rejected')
  })

  it('says nothing more on a card whose Deny only declines the call', () => {
    const seg: ApprovalSegment = { kind: 'approval', id: '901', tool: 'Run command', risk: 'safe' }
    const { container } = render(<ApprovalCard seg={seg} onAct={() => {}} />)
    expect(container.textContent).not.toMatch(/ends its turn/)
    expect(screen.getByRole('button', { name: 'Deny Run command — nothing is remembered' })).toBeTruthy()
  })

  it('says it on a queue card too, which answers this call alone', () => {
    const seg: ApprovalSegment = { kind: 'approval', id: '900', tool: 'Run command', denyEffect: EFFECT }
    render(<ApprovalCard seg={seg} onAct={() => {}} answers="once" />)
    expect(screen.getByRole('button', { name: `Deny Run command. ${EFFECT}` })).toBeTruthy()
  })
})
