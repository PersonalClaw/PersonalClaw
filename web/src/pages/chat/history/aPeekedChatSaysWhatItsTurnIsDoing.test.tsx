import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { RunState } from '../../../lib/api'
import type { WsMessage } from '../../../lib/useChatSocket'
import { phaseLabel } from '../../../lib/workPhase'
import { endedState, runState, runningState } from '../../../test/runState'

// ── The history page's peek says what the peeked chat's turn is doing ───────────────────────────
//
// The peek panel showed "Thinking…" with an opacity pulse that never stopped while a reply it had
// sent had no words yet, and nothing at all for a turn started anywhere else, or while the turn ran
// a tool or waited on her OK. It shows the chat's own head of the reply now: the agent orb and the
// gateway's line for the turn's phase, from the chat's run state and its `work_phase` frames, still
// when the owner is needed, settling on the ending and fading when the turn ends.

const KEY = 'chat-peek-1'

const h = vi.hoisted(() => ({
  detail: vi.fn(),
  sendChat: vi.fn(),
  socket: null as null | { onMessage: (m: WsMessage) => void; onReconnect: (() => void) | null },
  blooms: [] as Array<[string | undefined, boolean]>,
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  // A drawn widget asks whether it is saved as an artifact.
  return { ...real, api: { ...real.api, chatSessionDetail: h.detail, sendChat: h.sendChat, artifactExists: () => Promise.resolve(false) } }
})
vi.mock('../../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/useChatSocket')>()
  return {
    ...real,
    useChatSocket: (onMessage: (m: WsMessage) => void, onReconnect: (() => void) | null) => {
      h.socket = { onMessage, onReconnect }
    },
  }
})

// The head's orb as it is, recording whether it may bloom: jsdom draws no canvas, so the bloom
// itself is not seen here (`ui/theLiveLineShowsHowTheTurnEnded.test.tsx` watches it play).
vi.mock('../../../ui/AgentOrb', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../ui/AgentOrb')>()
  const Recorded = (p: Parameters<typeof real.AgentOrb>[0]) => { h.blooms.push([p.phase ?? undefined, p.bloom ?? true]); return <real.AgentOrb {...p} /> }
  return { ...real, AgentOrb: Recorded }
})

let SessionPeekBody: typeof import('./SessionPeek')['SessionPeekBody']

const detailWith = (run_state: RunState) => ({
  key: KEY, title: 'Release notes',
  messages: [{ role: 'user', content: 'Draft the release notes' }],
  run_state,
})
const frame = (rs: RunState) => act(() => { h.socket!.onMessage({ type: 'work_phase', data: rs as unknown as Record<string, unknown> }) })

const head = () => document.querySelector('[data-turn-head]') as HTMLElement | null
const orb = () => document.querySelector('[data-agent-orb]') as HTMLElement | null
const line = () => document.querySelector('[data-turn-line]')?.textContent

async function peek(rs: RunState) {
  h.detail.mockResolvedValue(detailWith(rs))
  render(<SessionPeekBody sessionKey={KEY} onOpen={vi.fn()} />)
  await screen.findByText('Draft the release notes')
}

beforeEach(async () => {
  // The panel keeps its tail in view; jsdom lays nothing out, so it has no scrolling to do.
  Element.prototype.scrollIntoView = vi.fn()
  h.detail.mockReset(); h.sendChat.mockReset(); h.socket = null; h.blooms = []
  h.sendChat.mockResolvedValue({ ok: true, session: KEY })
  ;({ SessionPeekBody } = await import('./SessionPeek'))
})
afterEach(() => cleanup())

// A scheduled run's result opened as a chat is a row the gateway took in from outside
// (`meta.from_outside`): a peek at that chat draws it as the result it is, as the chat does.
describe("a scheduled run's result in a peeked chat", () => {
  it('is drawn as written; the same text as a reply is drawn as a reply', async () => {
    h.detail.mockResolvedValue({
      key: KEY, title: 'Cron: j', run_state: runState({ session: KEY }),
      messages: [
        { role: 'user', content: 'Draft the release notes' },
        { role: 'assistant', content: '# Cron Job Result: j\n\n<widget title="Theirs">A</widget>', meta: { from_outside: { what: 'The result of the scheduled run of j', source: 'trigger:j1', source_type: 'schedule_result', source_id: 'j1' } } },
        { role: 'assistant', content: '<widget title="Mine">B</widget>' },
      ],
    })
    // A widget block is drawn by the content type registered for it.
    const { registerBuiltinContentTypes } = await import('../../../ui/content/registerBuiltins')
    registerBuiltinContentTypes()
    const { container } = render(<SessionPeekBody sessionKey={KEY} onOpen={vi.fn()} />)
    await screen.findByText('Draft the release notes')
    expect(container.textContent).toContain('<widget title="Theirs">')
    expect(container.textContent).not.toContain('<widget title="Mine">')
    // The reply's is drawn, and the result's is not.
    await waitFor(() => expect(container.querySelector('iframe[title="Mine"]')).not.toBeNull())
    expect(container.querySelector('iframe[title="Theirs"]')).toBeNull()
  })
})

describe('a turn that runs in the peeked chat', () => {
  it('a turn already running when the peek opens shows its phase and the gateway’s line', async () => {
    await peek(runningState({ session: KEY, phase: 'searching', line: 'Searching your knowledge' }))
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('searching'))
    expect(line()).toBe('Searching your knowledge')
    expect(screen.getByRole('img')).toHaveAccessibleName(phaseLabel('searching'))
    expect(screen.queryByText(/Thinking…/)).toBeNull()
  })

  it('follows the chat’s phase frames, and goes still with a badge when it needs her OK', async () => {
    await peek(runState({ session: KEY }))
    frame(runningState({ session: KEY, phase: 'tool', line: 'Terminal git log', seq: 3 }))
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('tool'))
    expect(line()).toBe('Terminal git log')
    expect(orb()!.dataset.agentOrb).toBe('animated')

    frame(runningState({ session: KEY, phase: 'approval', line: 'Needs your OK: shell', seq: 4 }))
    expect(orb()!.dataset.orbPhase).toBe('approval')
    expect(orb()!.dataset.agentOrb).toBe('still')
    expect(orb()!.querySelector('[data-orb-badge]')?.textContent).toBe('!')
    expect(line()).toBe('Needs your OK: shell')
  })

  it('a frame for another chat, or one older than what it holds, changes nothing', async () => {
    await peek(runningState({ session: KEY, phase: 'tool', line: 'Terminal ls', seq: 5 }))
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('tool'))
    frame(runningState({ session: 'another-chat', phase: 'approval', line: 'Needs your OK: shell', seq: 9 }))
    frame(runningState({ session: KEY, phase: 'thinking', line: 'Thinking', seq: 4 }))
    expect(orb()!.dataset.orbPhase).toBe('tool')
    expect(line()).toBe('Terminal ls')
  })

  it('a helper’s phase, though it names this chat, is not the chat’s turn', async () => {
    await peek(runningState({ session: KEY, phase: 'tool', line: 'Terminal ls', seq: 5 }))
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('tool'))
    frame({ work: 'subagent:h1', kind: 'subagent', session: KEY, phase: 'done', line: 'Done in 3 seconds', since: null, seq: 9 } as unknown as RunState)
    expect(orb()!.dataset.orbPhase).toBe('tool')
    expect(line()).toBe('Terminal ls')
  })

  it('a phase with no line reads the phase’s own word', async () => {
    await peek(runningState({ session: KEY, phase: 'planning', line: '' }))
    await waitFor(() => expect(line()).toBe(phaseLabel('planning')))
  })

  it('the turn’s last frame settles it on the ending, and then it is gone', async () => {
    await peek(runningState({ session: KEY, phase: 'streaming', line: 'Writing', seq: 2 }))
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('streaming'))
    frame(endedState('complete', { session: KEY, phase: 'done', line: 'Done in 3 seconds', seq: 3 }))
    expect(orb()!.dataset.orbPhase).toBe('done')
    expect(line()).toBe('Done in 3 seconds')
    await waitFor(() => expect(head()).toBeNull())
  })

  it.each([
    ['complete', 'Done in 3 seconds', true],
    ['stopped', 'Stopped', false],
    ['error', 'Claude stopped after you denied shell.', false],
  ] as const)('a turn that ended %s (“%s”) gives its head’s orb leave to bloom: %s', async (outcome, words, blooms) => {
    await peek(runningState({ session: KEY, phase: 'streaming', line: 'Writing', seq: 2 }))
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('streaming'))
    expect(h.blooms.at(-1)?.[1], 'the control: a running turn’s orb has its own rule').toBe(true)
    frame(endedState(outcome, { session: KEY, phase: 'done', line: words, seq: 3 }))
    expect(line()).toBe(words)
    expect(h.blooms.at(-1)?.[1]).toBe(blooms)
  })
})

describe('a message sent from the peek', () => {
  it('is waiting to start until the gateway says what its turn is doing: no stock “Thinking…”', async () => {
    await peek(runState({ session: KEY }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Quick reply' }), { target: { value: 'And the upgrade steps' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    const scrolled = vi.mocked(Element.prototype.scrollIntoView).mock.calls.length
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('queued'))
    expect(line()).toBe(phaseLabel('queued'))
    expect(vi.mocked(Element.prototype.scrollIntoView).mock.calls.length, 'the head is brought into view when it appears').toBeGreaterThan(scrolled)
    expect(screen.queryByText(/Thinking…/)).toBeNull()
    frame(runningState({ session: KEY, phase: 'thinking', line: 'Thinking', seq: 2 }))
    expect(orb()!.dataset.orbPhase).toBe('thinking')
  })

  it.each([
    ['complete', 'Done in 3 seconds', true],
    ['stopped', 'Stopped', false],
    ['error', 'Claude stopped after you denied shell.', false],
  ] as const)('a turn it sent that ended %s (“%s”), before its chat_done, gives its head’s orb leave to bloom: %s', async (outcome, words, blooms) => {
    await peek(runState({ session: KEY }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Quick reply' }), { target: { value: 'And the upgrade steps' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(orb()?.dataset.orbPhase).toBe('queued'))
    frame(runningState({ session: KEY, phase: 'streaming', line: 'Writing', seq: 2 }))
    // The ending lands while the peek still waits on its own send's chat_done.
    frame(endedState(outcome, { session: KEY, phase: 'done', line: words, seq: 3 }))
    expect(line()).toBe(words)
    const atDone = h.blooms.find(([phase]) => phase === 'done')
    expect(atDone, 'the control: the head showed the ending').toBeDefined()
    expect(atDone![1]).toBe(blooms)
    act(() => { h.socket!.onMessage({ type: 'chat_done', data: { session: KEY, outcome } }) })
  })
})

describe('with nothing running', () => {
  it('a chat at rest shows no orb, so nothing on the panel animates', async () => {
    await peek(endedState('complete', { session: KEY, phase: 'done', line: 'Done in 2 seconds' }))
    await new Promise((r) => setTimeout(r, 200))
    expect(orb()).toBeNull()
    expect(head()).toBeNull()
  })

  it('the old pulse is gone from the panel’s source', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/chat/history/SessionPeek.tsx'), 'utf8')
    expect(src).not.toMatch(/repeat:\s*Infinity|Thinking…/)
  })
})
