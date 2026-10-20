import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { approvalSegmentOf } from './approvalSegment'
import { applyApprovalFrame } from './liveToolFrames'
import { hydrateTurns } from './chatTypes'
import type { ApprovalSegment, HistMsg } from './chatTypes'

// A call in a turn someone other than you asked for (a colleague in a shared thread) is asked of
// you whatever your Trust or YOLO says (`approval_grants`, rule 4). The card names who asked and
// offers your answer for that call alone: "This chat" would promise a "runs without asking" that
// their next call never gets.

const ASKED_FOR = 'Jonas (U0JONASCOL) on teamchat asked for this, not you. Your Trust, Trust reads, '
  + "YOLO and an agent's Always allow answer only what you ask for, so this call waits for your answer."

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'write_file', input: '{"path": "notes.md"}', risk: 'caution', ...over,
})

describe('an approval for a call someone else asked for', () => {
  it('names who asked on the card', () => {
    const { container } = render(<ApprovalCard seg={seg({ askedFor: ASKED_FOR })} onAct={() => {}} />)
    expect(container.textContent).toContain(ASKED_FOR)
  })

  it('offers only Just this once', () => {
    render(<ApprovalCard seg={seg({ askedFor: ASKED_FOR })} onAct={() => {}} />)
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Just this once'])
  })

  it('keeps every scope for a call in your own turn, and names nobody', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(container.textContent).not.toContain('asked for this, not you')
  })

  it('carries who asked from the live frame, the registry row and the reloaded transcript', () => {
    const [live] = applyApprovalFrame([], { request_id: 'req-1', tool: 'write_file', asked_for: ASKED_FOR })
    expect((live as ApprovalSegment).askedFor).toBe(ASKED_FOR)
    const row = {
      id: 'chat:s:req-1', request_id: 'req-1', tool: 'write_file', tool_input: '{}',
      tool_purpose: '', risk: 'caution', blast_radius: undefined, grant_agent: '', session: 's',
      source_label: '', reach: '', asked_for: ASKED_FOR,
    }
    expect(approvalSegmentOf(row).askedFor).toBe(ASKED_FOR)
    const permission: HistMsg = {
      role: 'permission', content: 'write_file', meta: { approval_id: 'req-1', asked_for: ASKED_FOR },
    }
    const reloaded = hydrateTurns([permission], false)[0].segments[0] as ApprovalSegment
    expect(reloaded.askedFor).toBe(ASKED_FOR)
  })
})
