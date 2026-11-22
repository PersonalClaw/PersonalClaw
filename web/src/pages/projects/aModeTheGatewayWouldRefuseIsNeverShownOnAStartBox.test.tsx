import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { EditorView } from '@codemirror/view'
import { api, type ChatBoundary } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { clearNewChat, takeNewChat } from '../../lib/newChatHandoff'
import { mountPage, navigate, PID } from '../../test/projectPageHarness'

// ── A permission mode the gateway would refuse is never shown on a start box ────────────────────
//
// A project's start box (Home's launcher) holds its picks until the chat is made, and the new chat
// sets them as it is made. Under an operator ceiling that asks for every approval, the gateway
// refuses Trust, Trust reads and YOLO there, so a box that showed the pick showed a mode the chat
// never ran in. The box now asks the gateway's own rule as a mode is picked (the new chat's
// boundary reading says which modes the ceiling refuses, and why): a refused pick keeps Normal and
// says why, a pick the gateway did not answer for keeps Normal and says that, and a pick it would
// take is shown and handed to the chat.

Element.prototype.scrollIntoView ??= () => {}

const READING: ChatBoundary = {
  sandbox: { confined: true, label: 'Sandboxed', said: 'The commands it runs are sandboxed on this computer.' },
  reach: { tier: 'all', label: 'Network on', said: 'Its web tools reach public hosts.' },
}
const CEILING = 'An operator setting on this computer asks you before every tool call.'

let reading: () => Promise<ChatBoundary | null>
beforeEach(() => {
  resetDataStore(); localStorage.clear(); clearNewChat(); navigate.mockReset()
  vi.spyOn(api, 'replyStyles').mockResolvedValue({ styles: [] } as never)
  vi.spyOn(api, 'chatBoundary').mockImplementation(() => reading() as never)
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore(); clearNewChat() })

const startBox = () => document.querySelector('[data-start-box]') as HTMLElement
const control = () => within(startBox()).getByRole('button', { name: /^Execution boundary:/ })
const pickMode = async (label: string) => {
  await waitFor(() => expect(startBox()).toBeTruthy())
  await userEvent.click(control())
  await userEvent.click(await screen.findByRole('menuitemradio', { name: label }))
}
const sendDraft = async (text: string) => {
  await waitFor(() => expect(startBox().querySelector('.cm-editor')).toBeTruthy())
  const view = EditorView.findFromDOM(startBox().querySelector('.cm-editor') as HTMLElement)!
  act(() => { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }) })
  await userEvent.click(within(startBox()).getByRole('button', { name: 'Send message' }))
  const to = navigate.mock.calls.at(-1)?.[0] as string
  return takeNewChat(decodeURIComponent(/start=([^&]+)/.exec(to)![1]), PID)
}

describe("a start box's permission mode", () => {
  it('🔴 keeps Normal and says why when the operator ceiling refuses the mode picked', async () => {
    reading = async () => ({ ...READING, permission: { refused: ['trust_reads', 'trust', 'yolo'], said: CEILING } })
    mountPage({})

    for (const label of ['Trust', 'YOLO']) {
      await pickMode(label)
      await waitFor(() => expect(within(startBox()).getByText(CEILING)).toBeTruthy())
      expect(control()).toHaveAccessibleName('Execution boundary: Normal')
    }
    expect((await sendDraft('Tidy the beds'))?.picks.approval, 'nothing refused is handed to the chat').toBeUndefined()
  })

  it('🔴 keeps Normal, saying so, when the gateway does not answer whether it would take the mode', async () => {
    reading = async () => { throw new Error('Failed to fetch') }
    mountPage({})

    await pickMode('Trust')
    await waitFor(() => expect(within(startBox()).getByText(/^Couldn't check whether this computer allows this permission mode/)).toBeTruthy())
    expect(control()).toHaveAccessibleName('Execution boundary: Normal')
  })

  it('shows a mode the gateway would take, and hands it to the chat', async () => {
    reading = async () => ({ ...READING, permission: { refused: [], said: '' } })
    mountPage({})

    await pickMode('Trust')
    await waitFor(() => expect(control()).toHaveAccessibleName('Execution boundary: Trust'))
    expect(within(startBox()).queryByText(CEILING)).toBeNull()
    expect((await sendDraft('Tidy the beds'))?.picks.approval).toBe('trust')
  })

  it('Normal is never refused, and asks the gateway nothing', async () => {
    reading = async () => ({ ...READING, permission: { refused: ['trust_reads', 'trust', 'yolo'], said: CEILING } })
    mountPage({})
    await waitFor(() => expect(api.chatBoundary).toHaveBeenCalled())
    const asked = vi.mocked(api.chatBoundary).mock.calls.length

    await pickMode('Normal')
    expect(control()).toHaveAccessibleName('Execution boundary: Normal')
    expect(within(startBox()).queryByText(CEILING)).toBeNull()
    expect(vi.mocked(api.chatBoundary).mock.calls.length).toBe(asked)
  })
})
