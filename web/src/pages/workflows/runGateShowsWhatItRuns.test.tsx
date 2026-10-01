import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PendingApproval } from '../../lib/api'

// ── The run page's approval shows what the step would run ───────────────────────────────────────
//
// A research run's step asked to run a shell command, and its run page said only "This step needs
// your approval to run bash" with Approve: bash and Reject: bash. The command, its risk and the step
// that asked were on the Inbox row and nowhere on the page the run's watcher was looking at, so she
// approved it blind. An MCP tool's question read the same: "Approve: mcp/deepwiki/read_wiki_contents"
// with no repository and no question.
//
// The page now renders the one approval card: the tool and its risk, what it can touch, the whole
// input a click away, and the step it came from.

const approvals = vi.fn<() => Promise<PendingApproval[]>>()
const resolveApproval = vi.fn<(id: string, action: 'approve' | 'reject') => Promise<{ ok: boolean }>>()

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, approvals, resolveApproval } }
})

const COMMAND = 'cd /home/user/projects/feedsmith && git log --oneline -20 && cat RESEARCH.md | head -50'

const asked = (over: Partial<PendingApproval> = {}): PendingApproval => ({
  id: 'subagent:9f2c:call', request_id: 'subagent:9f2c:call', source: 'subagent', tool: 'bash',
  tool_input: `{"command": "${COMMAND}"}`, tool_purpose: '',
  session: 'workflow:4e60549e:sweep', ts: 0, session_title: '', agent: '', risk: 'destructive',
  is_read_only: false, grant_agent: '', source_label: 'workflow “deep-research” · step “sweep”',
  blast_radius: { writes: false, network: false, shell: true, saysReadOnly: false, readOnly: false },
  ...over,
})

async function mount(queue: PendingApproval[]) {
  approvals.mockReset(); resolveApproval.mockReset()
  approvals.mockResolvedValue(queue)
  resolveApproval.mockResolvedValue({ ok: true })
  const { RunToolApprovals } = await import('./RunToolApprovals')
  await act(async () => {
    render(<RunToolApprovals runId="4e60549e" />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return screen.findByRole('group', { name: /^Permission needed to run / })
}

beforeEach(() => { cleanup() })

describe('the run page’s approval shows what the step would run', () => {
  it('🔴 shows the command, its risk and the step that asked', async () => {
    const card = await mount([asked()])
    // The start of the command is on the card, cut at a word, and the rest one click away.
    expect(within(card).getByText(/^bash\(\{"command": "cd \/home\/user\/projects\/feedsmith/)).toBeTruthy()
    expect(within(card).getByText('Destructive')).toBeTruthy()
    expect(within(card).getByRole('list', { name: /What this can touch/ }).textContent).toBe('Runs a command')
    expect(within(card).getByText('workflow “deep-research” · step “sweep”')).toBeTruthy()
    await userEvent.click(within(card).getByRole('button', { name: 'Show all of what bash would run' }))
    expect(within(card).getByRole('group', { name: 'Tool arguments' }).textContent)
      .toBe(`{"command": "${COMMAND}"}`)
  })

  it('shows an MCP tool’s whole question, not only the tool’s name', async () => {
    const question = '{"repoName": "example/feedsmith", "question": "Where is the retry policy configured?"}'
    const card = await mount([asked({ tool: 'mcp/deepwiki/read_wiki_contents', tool_input: question, risk: 'caution' })])
    await userEvent.click(within(card).getByRole('button', { name: /^Show all of what mcp\/deepwiki/ }))
    expect(within(card).getByRole('group', { name: 'Tool arguments' }).textContent).toBe(question)
    expect(within(card).getByText('Caution')).toBeTruthy()
  })

  it('Allow answers that approval, named by what it runs and who asked', async () => {
    await mount([asked()])
    await userEvent.click(screen.getByRole('button', { name: /^Allow bash — workflow “deep-research”/ }))
    await waitFor(() => expect(resolveApproval).toHaveBeenCalledWith('subagent:9f2c:call', 'approve'))
  })
})
