/** Every surface asks for the second confirmation the approval row names, and one that names
 *  none fails closed: it asks unless its risk is an established read.
 *
 *  The row's `confirms` is worked out once, where the approval is recorded, so the chat's card, a
 *  loop's, the queue's (Home, the Inbox, the run page, a room) and a rehydrated transcript ask for
 *  the same calls. A row without the field says nothing about what it may do, so it is taken to be
 *  able to do any of the four. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { approvalSegmentOf, confirmsOf } from './approvalSegment'
import { applyApprovalFrame } from './liveToolFrames'
import { PendingApprovalCard } from '../../app/PendingApprovalCard'
import { hydrateTurns, type ApprovalSegment, type HistMsg } from './chatTypes'

afterEach(cleanup)

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', input: 'rm -rf build', risk: 'destructive', confirms: ['deletes'], ...over,
})

const allow = () => screen.getByRole('button', { name: /^Allow/ })

describe('the row names the confirm', () => {
  it('reads the row, and fails closed on a row that names none', () => {
    expect(confirmsOf(['deletes', 'sends'], 'destructive')).toEqual(['deletes', 'sends'])
    expect(confirmsOf([], 'caution')).toEqual([])
    expect(confirmsOf(undefined, 'caution')).toEqual(['undeclared'])
    expect(confirmsOf('deletes', 'caution')).toEqual(['undeclared'])
    expect(confirmsOf([3, 'sends'], 'caution')).toEqual(['undeclared'])
    expect(confirmsOf(undefined, 'safe')).toEqual([])
  })

  it('a queue row and a live frame carry it onto the card', () => {
    const row = { id: 'r1', request_id: 'r1', tool: 'bash', risk: 'caution', session: 's', confirms: ['outside_workspace'] }
    expect(approvalSegmentOf(row as never).confirms).toEqual(['outside_workspace'])
    const [live] = applyApprovalFrame([], { request_id: 'q1', tool: 'notify', risk: 'caution', confirms: ['sends'] })
    expect((live as ApprovalSegment).confirms).toEqual(['sends'])
    const [old] = applyApprovalFrame([], { request_id: 'q2', tool: 'mcp/srv/do', risk: 'caution' })
    expect((old as ApprovalSegment).confirms).toEqual(['undeclared'])
  })
})

describe('a reloaded transcript', () => {
  const reloaded = (meta: Record<string, unknown>) => {
    const rows: HistMsg[] = [
      { role: 'user', content: 'tidy the build folder' },
      { role: 'permission', content: 'bash', meta: { approval_id: 'p1', tool_input: 'rm -rf build', ...meta } },
    ]
    return hydrateTurns(rows, true).flatMap((t) => t.segments).find((sg) => sg.kind === 'approval') as ApprovalSegment
  }

  it('carries the confirm its permission row names', () => {
    expect(reloaded({ risk: 'destructive', confirms: ['deletes'] }).confirms).toEqual(['deletes'])
    expect(reloaded({ risk: 'caution', confirms: [] }).confirms).toEqual([])
  })

  it('fails closed on a row an older build wrote without one', () => {
    expect(reloaded({ risk: 'caution' }).confirms).toEqual(['undeclared'])
    expect(reloaded({ risk: 'safe' }).confirms).toEqual([])
  })
})

describe("the chat's card", () => {
  it('asks the confirm on a segment built without the field, unless the call only reads', () => {
    const onAct = vi.fn()
    const { confirms: _omitted, ...bare } = seg({ risk: 'caution' })
    render(<ApprovalCard seg={bare} onAct={onAct} />)
    fireEvent.click(allow())
    expect(onAct).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /Yes, run it/ })).toBeTruthy()
  })

  it('a new request drawn where a decided one was is answerable: a decision never carries over', async () => {
    const onAct = vi.fn().mockResolvedValue(true)
    const view = render(<ApprovalCard seg={seg({ id: 'a1', risk: 'caution', confirms: [] })} onAct={onAct} />)
    await act(async () => { fireEvent.click(allow()) })
    view.rerender(<ApprovalCard seg={seg({ id: 'a2', risk: 'caution', confirms: [] })} onAct={onAct} />)
    await act(async () => { fireEvent.click(allow()) })
    expect(onAct.mock.calls.map((c) => c[0])).toEqual(['a1', 'a2'])
  })

  it('holds Allow for a second confirmation, then posts the scope once', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg()} onAct={onAct} />)
    fireEvent.click(allow())
    expect(onAct).not.toHaveBeenCalled()
    expect(screen.getByText(/It deletes files\./)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Yes, run it/ }))
    expect(onAct).toHaveBeenCalledTimes(1)
    expect(onAct).toHaveBeenCalledWith('a1', 'approved')
  })

  it('decides on the first press when the row names none', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg({ risk: 'caution', confirms: [] })} onAct={onAct} />)
    fireEvent.click(allow())
    expect(onAct).toHaveBeenCalledTimes(1)
  })

  it('never holds Deny: a decline is never asked twice', () => {
    const onAct = vi.fn()
    render(<ApprovalCard seg={seg()} onAct={onAct} />)
    fireEvent.click(screen.getByRole('button', { name: /^Deny/ }))
    expect(onAct).toHaveBeenCalledWith('a1', 'rejected')
  })
})

describe('the queue card', () => {
  it('asks the confirm the row names before it answers', () => {
    const onDecide = vi.fn()
    render(<PendingApprovalCard onDecide={onDecide} approval={{
      id: 'r1', request_id: 'r1', tool: 'mcp/srv/create_page', risk: 'caution', session: 'workflow:ab12cd34:step',
      confirms: ['undeclared'],
    } as never} />)
    fireEvent.click(allow())
    expect(onDecide).not.toHaveBeenCalled()
    expect(screen.getByText(/Its tool doesn't say what it does/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Yes, run it/ }))
    expect(onDecide).toHaveBeenCalledWith('approve')
  })
})
