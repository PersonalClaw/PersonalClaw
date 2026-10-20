import { describe, it, expect } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { approvalSegmentOf } from './approvalSegment'
import type { ApprovalSegment } from './chatTypes'

// A shell command that reaches a host off the allowed hosts is asked about whatever a standing
// grant says (`run_bounds`). The card names the host, and offers no standing grant for the call:
// "This chat" would promise a "runs without asking" that this call never gets.

const REACH = 'It reaches pkgs.example.com, which is not on Allowed hosts in Settings → Security → Network egress.'

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', input: 'curl -s https://pkgs.example.com/simple/', risk: 'caution', ...over,
})

describe('an approval for a host off the allowed hosts', () => {
  it('names the host on the card', () => {
    const { container } = render(<ApprovalCard seg={seg({ reach: REACH })} onAct={() => {}} />)
    expect(container.textContent).toContain('pkgs.example.com, which is not on Allowed hosts')
  })

  it('offers only Just this once', () => {
    render(<ApprovalCard seg={seg({ reach: REACH })} onAct={() => {}} />)
    expect(screen.getAllByRole('radio').map((r) => r.textContent)).toEqual(['Just this once'])
  })

  it('keeps every scope for a call with no reach, and says what a grant still asks about', () => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    expect(screen.getAllByRole('radio')).toHaveLength(3)
    expect(container.textContent).not.toContain('which is not on Allowed hosts')
    fireEvent.click(screen.getByRole('radio', { name: 'This chat' }))
    expect(container.textContent).toContain('A command that reaches a host off your allowed hosts, or that deletes your home folder, the filesystem root or the working folder, still asks.')
  })

  it('carries the reach from a registry row to the card', () => {
    const row = {
      id: 'chat:s:req-1', request_id: 'req-1', tool: 'bash', tool_input: 'curl https://pkgs.example.com/',
      tool_purpose: '', risk: 'caution', blast_radius: undefined, grant_agent: '', session: 's',
      source_label: '', reach: REACH,
    }
    expect(approvalSegmentOf(row).reach).toBe(REACH)
  })
})
