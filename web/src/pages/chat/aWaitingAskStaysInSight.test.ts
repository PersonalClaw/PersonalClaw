import { describe, expect, it } from 'vitest'
import { waitsPastItsTurn } from './approvalSegment'
import type { ApprovalSegment } from './chatTypes'

// ── An ask that waits past its turn stays in sight ──────────────────────────────────────────────
//
// 🔴 A batch's ask, like a subagent's, is raised by work the chat started, and it outlives the turn
// that started it. Its card sat in that turn's work, which folds into "Worked through 2 steps" once
// the turn has answered, so the one card in the chat she could answer it from was folded out of
// sight while it still waited. The turn now keeps it out of the fold until it is answered.

const ask: ApprovalSegment = {
  kind: 'approval', id: 'batch:subagent-batch-1790000000000-a1b2c3', tool: 'subagent_run',
  purpose: 'Starts 2 tasks at once, each of which only reads.', queued: true,
}

describe('a card that waits past its turn', () => {
  it('🔴 is an ask that work the chat started still waits on', () => {
    expect(waitsPastItsTurn(ask)).toBe(true)
  })

  it('folds with its turn once it is answered', () => {
    expect(waitsPastItsTurn({ ...ask, resolved: 'approved' })).toBe(false)
  })

  it('the control: the chat’s own ask, which its turn waits on, folds as it did', () => {
    expect(waitsPastItsTurn({ ...ask, queued: undefined })).toBe(false)
    expect(waitsPastItsTurn({ kind: 'text', text: 'I asked to start the two tasks.' })).toBe(false)
  })
})
