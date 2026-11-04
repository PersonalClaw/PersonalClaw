import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { approvalSegmentOf } from './approvalSegment'
import { applyApprovalFrame } from './liveToolFrames'
import { hydrateTurns } from './chatTypes'
import type { ApprovalSegment, HistMsg } from './chatTypes'

// A file tool refused a project's secret file asks you for it (`secret_file_grants`). Its card has
// two answers, Allow for this session and Deny: no remember-scope picker, since no Trust, YOLO or
// saved grant ever answers such an ask, and its Allow lasts until the chat ends.

const REACH = "This is a project's secret file. Allow lets this chat read it until the chat is "
  + 'deleted or PersonalClaw restarts. Only your answer opens it: Trust, YOLO and saved grants never do.'

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'c1:secret-file:1', tool: 'read_file', input: '{"path": ".env"}',
  risk: 'safe', confirms: [], reach: REACH, secretFile: 'read', ...over,
})

describe('an ask for a project secret file', () => {
  it('offers Allow for this session and Deny, and no scope to remember', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    expect(screen.queryByRole('radiogroup')).toBeNull()
    expect(screen.getByRole('button', { name: 'Allow read_file for this session' }).textContent)
      .toContain('Allow for this session')
    expect(screen.getByRole('button', { name: 'Deny read_file' })).toBeTruthy()
    expect(container.textContent).toContain(REACH)
  })

  it('answers with the plain approve, which lasts for this chat, or the Deny', () => {
    const onAct = vi.fn()
    const { unmount } = render(<ApprovalCard seg={seg()} onAct={onAct} />)
    fireEvent.click(screen.getByRole('button', { name: 'Allow read_file for this session' }))
    expect(onAct).toHaveBeenLastCalledWith('c1:secret-file:1', 'approved')
    unmount()
    render(<ApprovalCard seg={seg()} onAct={onAct} />)
    fireEvent.click(screen.getByRole('button', { name: 'Deny read_file' }))
    expect(onAct).toHaveBeenLastCalledWith('c1:secret-file:1', 'rejected')
  })

  it('keeps the scope picker for every other call', () => {
    render(<ApprovalCard seg={seg({ secretFile: undefined, reach: '' })} onAct={() => {}} />)
    expect(screen.getByRole('radiogroup', { name: 'Remember this choice' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /for this session/ })).toBeNull()
  })

  it('is known from the live frame, the registry row and the reloaded transcript', () => {
    const [live] = applyApprovalFrame([], {
      request_id: 'c1:secret-file:1', tool: 'read_file', reach: REACH, secret_file: 'write',
    })
    expect((live as ApprovalSegment).secretFile).toBe('write')
    const row = {
      id: 'chat:s:c1:secret-file:1', request_id: 'c1:secret-file:1', tool: 'read_file',
      tool_input: '{}', tool_purpose: '', risk: 'safe', blast_radius: undefined, grant_agent: '',
      session: 's', source_label: '', reach: REACH, secret_file: 'read',
    }
    expect(approvalSegmentOf(row).secretFile).toBe('read')
    const permission: HistMsg = {
      role: 'permission', content: 'read_file', meta: { approval_id: 'c1:secret-file:1', secret_file: 'read' },
    }
    const reloaded = hydrateTurns([permission], false)[0].segments[0] as ApprovalSegment
    expect(reloaded.secretFile).toBe('read')
  })

  it('reads no ask from a word this build does not know', () => {
    const [live] = applyApprovalFrame([], { request_id: 'r', tool: 'read_file', secret_file: 'delete' })
    expect((live as ApprovalSegment).secretFile).toBeUndefined()
  })
})
