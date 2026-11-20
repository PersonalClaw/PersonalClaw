import { describe, expect, it, vi, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ManualAutomationCard, liveManualAutomationCards, manualAutomationFromTool } from './ManualAutomationCard'
import type { ChatTurn, Segment, ToolSegment } from './chatTypes'

// ── "Make me a button that runs …" is shown with a button that runs it ──────────────────────────
//
// Asked for a button that runs the kitchen-quote comparison, the agent drew a widget whose button
// only posted "[UI] run_comparison" back into the chat, and said it had made a button that runs
// the comparison. Nothing ran. The chat now makes a manual automation for it (`automation_create`,
// `kind: "manual"`), and its result is shown with Run now: the Triggers page's run, where she asked.

const runStoreTrigger = vi.fn()
// The Triggers page's list, which the card reads its current approval from.
const storeTriggers = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    runStoreTrigger: (...a: unknown[]) => runStoreTrigger(...a),
    storeTriggers: () => storeTriggers(),
    artifactExists: () => Promise.resolve(false),
  },
}))
// The gateway's frames: the card re-reads on `crons`, which a change to the triggers sends.
const socket = vi.hoisted(() => ({ handlers: [] as Array<(m: unknown) => void> }))
vi.mock('../../lib/useChatSocket', async () => ({
  useChatSocket: (fn: (m: unknown) => void) => { socket.handlers.push(fn) },
  refreshKinds: (await import('../../lib/wsFrames')).refreshKinds,
}))

const ROW = {
  id: 'manual:kitchen-quote-comparison', name: 'Kitchen quote comparison', kind: 'manual', enabled: true,
  workflow: { inline: { provider: 'run-prompt', config: { message: 'Compare the three quotes.' } } },
}
const created = (row: object, needs: string[] = ['Run Prompt']) =>
  'Created “Kitchen quote comparison”.\n  it runs only when you run it\n\n'
  + `<automation-data>${JSON.stringify({ trigger: row, needs_grant: needs })}</automation-data>`

/** The Triggers page's list holding the row, needing *needs* allowed now. */
const listed = (needs: string[]) => [{ kind: 'store', id: `store:${ROW.id}`, raw_id: ROW.id, name: ROW.name, enabled: true, needs_grant: needs }]

afterEach(() => { cleanup(); runStoreTrigger.mockReset(); storeTriggers.mockReset(); socket.handlers.length = 0 })

describe('an automation made to run when she runs it', () => {
  it('is recognized from what automation_create answered, and nothing else is', () => {
    expect(manualAutomationFromTool('automation_create', created(ROW))).toEqual({
      id: 'manual:kitchen-quote-comparison', name: 'Kitchen quote comparison', needsGrant: ['Run Prompt'],
    })
    // The same tool reached through an agent's MCP client carries its server's prefix.
    expect(manualAutomationFromTool('mcp__personalclaw__automation_create', created(ROW))?.id)
      .toBe('manual:kitchen-quote-comparison')
    // Another kind is its own automation, run by what it waits for; another tool is not this one.
    expect(manualAutomationFromTool('automation_create', created({ ...ROW, kind: 'clock' }))).toBeNull()
    expect(manualAutomationFromTool('automation_update', created(ROW))).toBeNull()
    expect(manualAutomationFromTool('automation_create', 'Created it.')).toBeNull()
    expect(manualAutomationFromTool('automation_create', '<automation-data>{not json</automation-data>')).toBeNull()
  })

  it('🔑 Run now runs it, and says it started', async () => {
    storeTriggers.mockResolvedValue(listed([]))
    runStoreTrigger.mockResolvedValue({ ok: true, name: ROW.name, status: 'launched' })
    render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: [] }} />)
    expect(screen.getByText('It runs only when you run it.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Run Kitchen quote comparison now' }))
    await waitFor(() => expect(runStoreTrigger).toHaveBeenCalledWith('manual:kitchen-quote-comparison'))
    expect(await screen.findByRole('status')).toHaveTextContent('Started: launched.')
  })

  it('one not yet allowed says so before it is pressed, and pressing it says what it needs', async () => {
    storeTriggers.mockResolvedValue(listed(['Run Prompt']))
    runStoreTrigger.mockResolvedValue({
      ok: false, name: ROW.name, refused: 'Kitchen quote comparison is not allowed to use Run Prompt yet. Allow it on the Triggers page.',
    })
    render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: ['Run Prompt'] }} />)
    expect(screen.getByText(/not until you allow it on the Triggers page/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open/ })).toHaveAttribute('href', '#/triggers?open=manual%3Akitchen-quote-comparison')

    fireEvent.click(screen.getByRole('button', { name: 'Run Kitchen quote comparison now' }))
    expect(await screen.findByRole('status')).toHaveTextContent('is not allowed to use Run Prompt yet')
  })
})

// ── where the card is, and what it says now ────────────────────────────────────────────────────
//
// 🔴 The reply said to use the Run now "shown with this reply", but the card was drawn inside the
// folded "Worked through N steps": no button under the reply until she opened the steps. And once
// she allowed the automation on the Triggers page, the card still said "not until you allow it",
// what it needed when the chat made it.

const tool = (id: string, output: string): ToolSegment => ({ kind: 'tool', id, tool: 'automation_create', output, done: true })
const turn = (...segments: Segment[]): ChatTurn => ({ role: 'assistant', segments })

describe("a manual automation's card", () => {
  it('as a live card, is drawn under the reply with its steps still folded', async () => {
    // The transcript makes it one (`useTranscript.test.tsx`), and a live card is drawn outside the
    // work fold, which renders its steps only once it is opened.
    storeTriggers.mockResolvedValue(listed([]))
    const { AssistantSegments } = await import('./transcript/AssistantSegments')
    const segments: Segment[] = [
      { kind: 'tool', id: 'r1', tool: 'read_file', output: 'quotes.csv', done: true } as ToolSegment,
      tool('c1', created(ROW, [])),
      { kind: 'text', text: 'Done: use its Run now button, shown with this reply.' },
    ]
    await act(async () => {
      render(
        <AssistantSegments segments={segments} liveCards={liveManualAutomationCards([turn(...segments)])} isLast
          onApprove={vi.fn()} onAnswerQuestion={vi.fn()} onSwitchToAgent={vi.fn()} onOpenFile={vi.fn()} onSetupModel={vi.fn()} />,
      )
    })
    expect(screen.getByRole('button', { name: /^Worked through/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: 'Run Kitchen quote comparison now' })).toBeInTheDocument()
  })

  it('is shown once per automation, where the chat first names it', () => {
    const first = tool('c1', created(ROW))
    const again = tool('c2', created(ROW, []))
    const other = tool('c3', created({ ...ROW, id: 'manual:weekly-digest', name: 'Weekly digest' }))
    const failed: ToolSegment = { ...tool('c4', created({ ...ROW, id: 'manual:failed-one' })), ok: false }
    expect([...liveManualAutomationCards([turn(first), turn(again, other, failed)])]).toEqual([first, other])
  })

  it('🔴 says what it needs allowed now, and again once she allows it', async () => {
    storeTriggers.mockResolvedValue(listed(['Run Prompt']))
    await act(async () => { render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: ['Run Prompt'] }} />) })
    expect(screen.getByText(/not until you allow it on the Triggers page/)).toBeInTheDocument()

    // She allows it on the Triggers page, and the gateway says the triggers changed.
    storeTriggers.mockResolvedValue(listed([]))
    await act(async () => { for (const on of socket.handlers) on({ type: 'refresh', data: { kinds: ['crons'] } }) })
    expect(await screen.findByText('It runs only when you run it.')).toBeInTheDocument()
  })

  it('made when it needed an Allow, and allowed since, says so when it is shown', async () => {
    storeTriggers.mockResolvedValue(listed([]))
    await act(async () => { render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: ['Run Prompt'] }} />) })
    expect(await screen.findByText('It runs only when you run it.')).toBeInTheDocument()
  })

  it('one deleted since says so, and offers no Run now', async () => {
    storeTriggers.mockResolvedValue([])
    await act(async () => { render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: [] }} />) })
    expect(await screen.findByText('This automation was deleted.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Run .* now/ })).toBeNull()
    expect(screen.queryByRole('link', { name: /Open/ })).toBeNull()
  })

  it('a read that fails keeps what the chat was told', async () => {
    storeTriggers.mockRejectedValue(new Error('offline'))
    await act(async () => { render(<ManualAutomationCard refObj={{ id: ROW.id, name: ROW.name, needsGrant: ['Run Prompt'] }} />) })
    expect(screen.getByText(/not until you allow it on the Triggers page/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run Kitchen quote comparison now' })).toBeInTheDocument()
  })
})
