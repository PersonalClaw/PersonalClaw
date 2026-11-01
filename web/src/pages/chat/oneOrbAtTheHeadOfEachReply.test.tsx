import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act, within } from '@testing-library/react'
import { runState, runningState } from '../../test/runState'

// ── One live orb for the running turn, in its bare line at the end of the conversation ────────
//
// The chat's turn had two indicators and a gap between them: a "Thinking" label with rotating
// stock phrases until the reply's first word, and then nothing while the reply streamed, its tools
// ran or it waited on her. Then it had one orb at the head of its reply, which a long turn scrolled
// out of view while the transcript followed its end. Now the running turn has one orb, in its live
// line (`ui/TurnStatusRow`): bare, with no border, background or pill, the last thing in the turn
// column under the latest message, scrolling with the thread (#5867). It is driven only by the turn's
// `work_phase` frames: it changes with the phase, its line is the gateway's, and it goes when the turn
// ends. Nothing else in the transcript draws a live orb. Stopping stays in reach whatever the scroll:
// the composer's send button is "Stop this turn" while the turn runs, its tooltip shows Esc, and it
// stops the turn the way Esc does. The frames below are what the gateway sends for one turn.

const SESSION = 'chat-4-orb'

const h = vi.hoisted(() => {
  const idle = {
    key: 'chat-4-orb', title: 'chat-4-orb',
    messages: [
      { role: 'user', content: 'earlier question', ts: '2026-10-19T09:00:00Z' },
      { role: 'assistant', content: 'earlier answer', ts: '2026-10-19T09:00:05Z' },
    ] as unknown[],
    queue: [] as unknown[], task_mode: 'agent', approval: 'normal', memory_mode: 'persistent',
  }
  return { idle, detailCalls: [] as { resolve: (d: unknown) => void }[], chatSessionDetail: vi.fn(), stopChat: vi.fn() }
})

vi.mock('../../lib/api', () => {
  const ok = () => Promise.resolve([])
  const base: Record<string, unknown> = {
    chatSessionDetail: h.chatSessionDetail,
    stopChat: h.stopChat,
    sendChat: () => Promise.resolve({ ok: true, session: 'chat-4-orb' }),
    chatSessions: () => Promise.resolve([]),
    agents: () => Promise.resolve({ agents: [] }),
    agentProviders: () => Promise.resolve([]),
    models: () => Promise.resolve([]),
    chatSessionTemplates: () => Promise.resolve([]),
    sessionCost: () => Promise.resolve({ turns: 0, cost_usd: 0, input_tokens: 0, output_tokens: 0 }),
  }
  const api = new Proxy(base, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = (...__a: unknown[]) => ok())
    },
  })
  return { api, errText: (e: unknown) => String((e as Error)?.message ?? e) }
})

class FakeSocket {
  static last: FakeSocket | null = null
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1
  constructor(public url: string) { FakeSocket.last = this; setTimeout(() => this.onopen?.(), 0) }
  send(): void {}
  close(): void { this.readyState = 3 }
}

function pushFrame(type: string, data: Record<string, unknown>) {
  act(() => { FakeSocket.last?.onmessage?.({ data: JSON.stringify({ type, data: { session: SESSION, ...data } }) }) })
}

let ChatPage: typeof import('../ChatPage')['ChatPage']
let AppearanceProvider: typeof import('../../app/appearance')['AppearanceProvider']

beforeEach(async () => {
  vi.stubGlobal('WebSocket', FakeSocket as unknown as typeof WebSocket)
  Element.prototype.scrollIntoView ??= () => {}
  if (typeof globalThis.IntersectionObserver === 'undefined') {
    vi.stubGlobal('IntersectionObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
      takeRecords(): [] { return [] }
    })
  }
  FakeSocket.last = null
  h.detailCalls.length = 0
  h.stopChat.mockReset().mockResolvedValue({ stopped: true })
  h.chatSessionDetail.mockReset().mockImplementation(
    () => new Promise((resolve) => { h.detailCalls.push({ resolve }) }),
  )
  ;({ ChatPage } = await import('../ChatPage'))
  ;({ AppearanceProvider } = await import('../../app/appearance'))
})

afterEach(() => { vi.unstubAllGlobals() })

const orbs = () => document.querySelectorAll('[data-agent-orb]')
const head = () => document.querySelector('[data-turn-status]') as HTMLElement | null
const line = () => document.querySelector('[data-turn-state]')?.textContent

/** The chat open and idle, then a turn starting on it: her question, and the turn waiting to start. */
async function aTurnStarts() {
  render(
    <AppearanceProvider>
      <ChatPage sub={SESSION} navigate={() => {}} query={{}} setQuery={() => {}} />
    </AppearanceProvider>,
  )
  await waitFor(() => expect(h.detailCalls.length).toBe(1))
  await act(async () => { h.detailCalls[0].resolve({ ...h.idle, run_state: runState({ session: SESSION }) }) })
  await waitFor(() => expect(screen.queryByText('earlier answer')).not.toBeNull())
  // Nothing runs: nothing at the head of any reply, and nothing on the page animates.
  expect(orbs()).toHaveLength(0)
  pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'queued', line: 'Waiting to start' }) })
  await waitFor(() => expect(h.detailCalls.length).toBe(2))
  await act(async () => {
    h.detailCalls[1].resolve({
      ...h.idle,
      run_state: runningState({ session: SESSION, phase: 'queued', line: 'Waiting to start' }),
      messages: [...h.idle.messages, { role: 'user', content: 'which stage is the run on?', ts: '2026-10-19T09:05:00Z' }],
    })
  })
  await waitFor(() => expect(screen.queryByText('which stage is the run on?')).not.toBeNull())
}

/** The running turn's line is the last thing in the turn column: bare, after every turn, with
 *  nothing after it but the end the view follows and the screen reader's live regions; and its orb
 *  is the transcript's one live orb. */
function expectTheLastLine() {
  const row = head()!
  const transcript = document.querySelector('[data-transcript-scroll]')!
  const turns = [...document.querySelectorAll<HTMLElement>('[data-transcript-turn]')]
  const column = turns[0].parentElement!
  expect(transcript.contains(row), 'the line is in the transcript, so it scrolls with the thread').toBe(true)
  expect(row.parentElement, 'the line sits in the turn column').toBe(column)
  const after = [...column.children].slice([...column.children].indexOf(row) + 1)
  expect(after.every((el) => el.childElementCount === 0 && (el.textContent ?? '').trim() === '' || el.classList.contains('sr-only')),
    'nothing but the end and the live regions comes after the line').toBe(true)
  for (const t of turns) {
    expect(t.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING, 'the line comes after every turn').toBeTruthy()
  }
  expect(row.className, 'the line is bare').not.toMatch(/(^|\s)(border\S*|bg-\S+|rounded-\S+|px-\S+|py-\S+)(\s|$)/)
  const live = transcript.querySelectorAll('[data-agent-orb]')
  expect(live, 'one live orb in the transcript').toHaveLength(1)
  expect(row.contains(live[0]), 'and it is the line\'s').toBe(true)
}

describe('a running turn\'s Stop is the composer\'s', () => {
  it('while the turn runs the send button is Stop this turn, its tooltip shows Esc, and it stops the turn as Esc does', async () => {
    await aTurnStarts()
    await waitFor(() => expect(line()).toBe('Waiting to start'))
    expectTheLastLine()
    const stage = document.querySelector('[data-tour="chat"]')!
    const stop = within(stage as HTMLElement).getByRole('button', { name: 'Stop this turn' })
    expect(stop.getAttribute('title'), 'its tooltip shows the key that stops the turn too').toBe('Stop this turn (Esc)')
    expect(head()!.querySelector('button'), 'the line has no button of its own').toBeNull()
    await act(async () => { stop.click() })
    await waitFor(() => expect(h.stopChat).toHaveBeenCalledTimes(1))
    expect(h.stopChat).toHaveBeenCalledWith(SESSION)
  })
})

describe('a running turn has one live orb, in its bare line at the end of the conversation', () => {
  it('follows the turn’s phases from waiting to start to its ending, and goes', async () => {
    await aTurnStarts()
    await waitFor(() => expect(line()).toBe('Waiting to start'))
    expect(orbs()).toHaveLength(1)

    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'thinking', line: 'Thinking', seq: 2 }) })
    await waitFor(() => expect(line()).toBe('Thinking'))

    // The first word of the reply: the reply streams in the transcript, and the orb stays in its
    // line, the last thing in the turn column, under the reply.
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'streaming', line: 'Writing', seq: 3 }) })
    pushFrame('chat_chunk', { content: 'The run is on the deploy stage.' })
    await waitFor(() => expect(screen.queryByText('The run is on the deploy stage.')).not.toBeNull())
    expect(line()).toBe('Writing')
    expect(orbs()).toHaveLength(1)
    expectTheLastLine()

    // A step, and the wait on her OK: the same orb, its line the gateway's, still while it waits.
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', line: 'Terminal git log', seq: 4 }) })
    await waitFor(() => expect(line()).toBe('Terminal git log'))
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'approval', line: 'Needs your OK: shell', seq: 5 }) })
    await waitFor(() => expect(line()).toBe('Needs your OK: shell'))
    expect((orbs()[0] as HTMLElement).dataset.agentOrb).toBe('still')
    expect(orbs()).toHaveLength(1)

    // The turn's last frame says how it ended: the turn is over, and its row goes.
    pushFrame('work_phase', { ...runState({ session: SESSION, phase: 'done', line: 'Done in 9 seconds', last_outcome: 'complete', seq: 6 }) })
    await waitFor(() => expect(head()).toBeNull())
    pushFrame('chat_done', { outcome: 'complete' })
    expect(orbs()).toHaveLength(0)
  })

  it('the Stop control’s sweep goes still with the orb while the turn waits on her, and turns again after', async () => {
    await aTurnStarts()
    const sweep = () => screen.getByRole('button', { name: /^Stop this turn$/ }).closest('[data-stop-sweep]')?.getAttribute('data-stop-sweep')
    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'tool', line: 'Terminal git log', seq: 2 }) })
    await waitFor(() => expect(line()).toBe('Terminal git log'))
    // Positive control: a running step sweeps, so "still" below is the wait's and nothing else's.
    expect(sweep()).toBe('animated')

    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'approval', line: 'Needs your OK: shell', seq: 3 }) })
    await waitFor(() => expect(line()).toBe('Needs your OK: shell'))
    expect((orbs()[0] as HTMLElement).dataset.agentOrb).toBe('still')
    expect(sweep(), 'the Stop control kept sweeping while the turn waited on her OK').toBe('still')

    pushFrame('work_phase', { ...runningState({ session: SESSION, phase: 'streaming', line: 'Writing', seq: 4 }) })
    await waitFor(() => expect(line()).toBe('Writing'))
    expect(sweep(), 'the sweep stayed still once the turn ran again').toBe('animated')
  })

  it('never shows the old indicator’s guessed label beside it', async () => {
    await aTurnStarts()
    await waitFor(() => expect(orbs()).toHaveLength(1))
    expect(document.querySelector('[data-phase-label]')).toBeNull()
  })
})
