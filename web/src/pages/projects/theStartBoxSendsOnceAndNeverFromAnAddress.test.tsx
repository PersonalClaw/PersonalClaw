import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { EditorView } from '@codemirror/view'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { clearNewChat, isStagedNewChat, stageNewChat, takeNewChat } from '../../lib/newChatHandoff'
import { chatLaunchPath, readChatLaunch } from '../../app/linkState'
import type { ChatTurn } from '../chat/chatTypes'
import type { ChatStream } from '../chat/useChatStream'
import { useChatComposer } from '../chat/composer/useChatComposer'
import { chat, mountPage, navigate, PID, project } from '../../test/projectPageHarness'
import { STARTER_PROMPTS } from './ProjectPage'

// ── The start box sends once, and never from an address (§5.3) ─────────────────────────────────
//
// Enter in a project's start box opens a new chat bound to the project and sends the draft there,
// once: the box stages the draft, its files and its picks in memory under a one-use token and opens
// `#/chat/new?project=<id>&start=<token>`; the new chat takes the stage once and sends it through
// its own first send. The text never rides the address, so reloading that address, or opening a
// copied one, sends nothing. A first run's "Plan the first tasks" puts a prompt in the box for the
// owner to send, and sends nothing itself. The box is off on an archived project, staging nothing.

Element.prototype.scrollIntoView ??= () => {}

beforeEach(() => {
  resetDataStore(); localStorage.clear(); clearNewChat(); navigate.mockReset()
  vi.spyOn(api, 'replyStyles').mockResolvedValue({ styles: [] } as never)
  vi.spyOn(api, 'chatBoundary').mockResolvedValue(null as never)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore(); clearNewChat() })

const box = async () => {
  await waitFor(() => expect(document.querySelector('[data-start-box] .cm-editor')).toBeTruthy())
  return EditorView.findFromDOM(document.querySelector('[data-start-box] .cm-editor') as HTMLElement)!
}
const write = async (text: string) => {
  const view = await box()
  act(() => { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }) })
}
const send = () => within(document.querySelector('[data-start-box]') as HTMLElement).getByRole('button', { name: 'Send message' })

describe("a project's start box", () => {
  it('opens a new chat in the project with a one-use token, and the draft stays off the address', async () => {
    mountPage({ sessions: [chat('c-1', 'Seed ideas', PID)] })
    await write('Plan the beds for spring')
    await userEvent.click(send())
    expect(navigate).toHaveBeenCalledTimes(1)
    const to = navigate.mock.calls[0][0] as string
    const m = /^chat\/new\?project=([^&]+)&start=([^&]+)$/.exec(to)
    expect(m, to).toBeTruthy()
    expect(decodeURIComponent(m![1])).toBe(PID)
    expect(to, 'the draft rides no address').not.toMatch(/Plan|seed=/)
    const token = decodeURIComponent(m![2])
    expect(isStagedNewChat(token)).toBe(true)
    expect(takeNewChat(token, PID)).toMatchObject({ text: 'Plan the beds for spring', files: [], project: PID })
    expect(takeNewChat(token, PID), 'the stage is taken once').toBeNull()
  })

  it('is off on an archived project, and stages nothing', async () => {
    mountPage({ project: project(PID, 'Garden', { status: 'archived', workspace_dir: '', folder: null }) })
    await screen.findByText(/is archived/)
    await write('Plan the beds for spring')
    await waitFor(() => expect(send().getAttribute('aria-disabled') === 'true' || (send() as HTMLButtonElement).disabled).toBe(true))
    await userEvent.click(send())
    expect(navigate).not.toHaveBeenCalled()
  })

  it("a first run's Plan the first tasks puts a prompt in the box, and sends nothing", async () => {
    mountPage({})
    await userEvent.click(await screen.findByRole('button', { name: 'Plan the first tasks' }))
    const view = await box()
    await waitFor(() => expect(view.state.doc.toString()).toBe(STARTER_PROMPTS.plan('Garden')))
    expect(navigate).not.toHaveBeenCalled()
  })
})

// ── The new chat's side: the composer takes the stage once, by its token, in its project ────────

function mountChat({ startToken, projectId = PID }: { startToken: string; projectId?: string }) {
  const create = vi.spyOn(api, 'createChatSession').mockResolvedValue({ key: 'chat-new-1' } as never)
  const sendChat = vi.spyOn(api, 'sendChat').mockResolvedValue({ status: 'started', run_id: 'r-1' } as never)
  const setTaskMode = vi.spyOn(api, 'setTaskMode').mockResolvedValue({} as never)
  const hook = renderHook(() => {
    const [turns, setTurns] = useState<ChatTurn[]>([])
    const stream = {
      sessionRef: { current: null }, turns, setTurns,
      patchLastAssistant: () => {}, dropTextRun: vi.fn(), streamingRef: { current: false }, markStreaming: vi.fn(), sayTurnEnded: vi.fn(),
      turnSeqRef: { current: 0 }, stoppedTurnRef: { current: false }, takesSteersRef: { current: false },
      setMissing: vi.fn(), setSteered: vi.fn(), followups: [], setFollowups: vi.fn(), followupsNeed: '', setFollowupsNeed: vi.fn(),
      checkWorkOffer: null, setCheckWorkOffer: vi.fn(), sessionBindingRef: { current: null }, bindingToldRef: { current: false }, bindingNonce: 0,
    } as unknown as ChatStream
    return useChatComposer({
      sessionId: null, navigate: vi.fn(), seed: '', startToken, initialProjectId: projectId, initialAgent: '', pendingRouting: null,
      setRoutingSuggestion: vi.fn(), setLiveRun: vi.fn(), started: turns.length > 0, stream, followNewTurn: vi.fn(), oweSpokenReply: vi.fn(),
    })
  })
  return { ...hook, create, sendChat, setTaskMode }
}

describe("the new chat a start box opens", () => {
  it('sends the staged draft once, in the project, with the picks made in the box', async () => {
    const token = stageNewChat({ text: 'Plan the beds for spring', files: [], picks: { taskMode: 'plan' }, project: PID })
    const m = mountChat({ startToken: token })
    await waitFor(() => expect(m.sendChat).toHaveBeenCalledTimes(1))
    expect(m.create).toHaveBeenCalledWith(expect.objectContaining({ project_id: PID }))
    expect(m.sendChat.mock.calls[0]).toContain('Plan the beds for spring')
    expect(m.setTaskMode, 'the box\'s task mode is the chat\'s').toHaveBeenCalledWith('plan', 'chat-new-1')
    expect(isStagedNewChat(token)).toBe(false)
    // The same address again (a reload, Back): nothing is staged, so nothing is sent.
    m.unmount()
    const again = mountChat({ startToken: token })
    await act(async () => { await new Promise((r) => setTimeout(r, 30)) })
    expect(again.sendChat).toHaveBeenCalledTimes(1)
    expect(again.result.current.input).toBe('')
  })

  it('sends nothing from a copied address, or from a token staged for another project', async () => {
    const copied = mountChat({ startToken: 'not-a-staged-token' })
    await act(async () => { await new Promise((r) => setTimeout(r, 30)) })
    expect(copied.sendChat).not.toHaveBeenCalled()
    copied.unmount()
    const token = stageNewChat({ text: 'Water the beds', files: [], picks: {}, project: 'p-other' })
    const elsewhere = mountChat({ startToken: token })
    await act(async () => { await new Promise((r) => setTimeout(r, 30)) })
    expect(elsewhere.sendChat).not.toHaveBeenCalled()
    expect(isStagedNewChat(token), 'a stage for another project stays where it is').toBe(true)
  })
})

describe('a staged draft is taken only by its own token', () => {
  it('an address naming another token sends nothing and leaves the stage; its own token takes it once', async () => {
    const token = stageNewChat({ text: 'Plan the beds for spring', files: [], picks: {}, project: PID })
    const guessed = mountChat({ startToken: `${token}-not` })
    await act(async () => { await new Promise((r) => setTimeout(r, 30)) })
    expect(guessed.sendChat, 'another token in the same project sends the staged draft').not.toHaveBeenCalled()
    expect(guessed.result.current.input).toBe('')
    expect(isStagedNewChat(token), 'the stage stays for its own token').toBe(true)
    guessed.unmount()
    const own = mountChat({ startToken: token })
    await waitFor(() => expect(own.sendChat).toHaveBeenCalledTimes(1))
    expect(isStagedNewChat(token)).toBe(false)
  })
})

describe("the start key, in the new chat's one reader", () => {
  it('is written only for the staged token, never beside attach, and read only without attach', () => {
    expect(chatLaunchPath({ project: PID, start: 'made-up' })).toBe(`chat/new?project=${PID}`)
    const token = stageNewChat({ text: 'x', files: [], picks: {}, project: PID })
    expect(chatLaunchPath({ project: PID, start: token })).toBe(`chat/new?project=${PID}&start=${token}`)
    expect(chatLaunchPath({ project: PID, start: token, attach: ['knowledge:k-1'] })).toBe(`chat/new?project=${PID}&attach=knowledge%3Ak-1`)
    expect(readChatLaunch({ project: PID, start: token }).start).toBe(token)
    expect(readChatLaunch({ project: PID, start: token, attach: 'knowledge:k-1' }).start).toBe('')
  })
})
