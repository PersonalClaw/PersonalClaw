import { describe, it, expect } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import type { ApprovalSegment } from './chatTypes'

// A shell command that would delete the home folder, the filesystem root or the chat's working
// folder is asked about whatever a standing grant says (`run_bounds`), so every standing grant the
// card offers says such a delete still asks: "This chat" must not promise a "without asking" that
// call never gets. (The call itself carries its `reach` line, which names the folder, and is
// offered Just this once alone, as `anOffListHostIsAskedEveryTime` shows for any such line.)

const seg = (over: Partial<ApprovalSegment> = {}): ApprovalSegment => ({
  kind: 'approval', id: 'a1', tool: 'bash', input: 'rm -rf build', risk: 'caution', ...over,
})

describe('a standing grant offered on the card', () => {
  it.each([['This chat'], ['This agent']])('%s says a delete of a protected folder still asks', (scope) => {
    const { container } = render(<ApprovalCard seg={seg()} onAct={() => {}} />)
    fireEvent.click(screen.getByRole('radio', { name: scope }))
    expect(container.textContent).toContain('deletes your home folder, the filesystem root or the working folder, still asks.')
  })
})
