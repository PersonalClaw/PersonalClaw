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
}))

vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, chatSessionDetail: h.detail, sendChat: h.sendChat } }
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
  h.detail.mockReset(); h.sendChat.mockReset(); h.socket = null
  h.sendChat.mockResolvedValue({ ok: true, session: KEY })
  ;({ SessionPeekBody } = await import('./SessionPeek'))
})
afterEach(() => cleanup())

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
