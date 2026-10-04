/** A document's "Send to a new chat" opens a NEW chat each time, titled after the document.
 *
 *  The title used to be sent as the chat's NAME, the key its transcript is saved under, so a
 *  second comment on the same document went into the first comment's chat rather than a new
 *  one. The chat is now named by the gateway, as every new chat is, and only titled here. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { newSessionTarget } from './commentTarget'

const calls: { created: unknown[]; renamed: [string, string][]; sent: [string, string, unknown][] } = {
  created: [],
  renamed: [],
  sent: [],
}
let renameFails = false
const told: [string, string | undefined][] = []

vi.mock('../../app/appSdk', () => ({
  notify: (message: string, kind?: string) => { told.push([message, kind]) },
}))
let nextKey = 0

vi.mock('../../lib/api', () => ({
  api: {
    createChatSession: vi.fn(async (opts?: unknown) => {
      calls.created.push(opts)
      nextKey += 1
      return { key: `chat-${nextKey}-1791067183` }
    }),
    renameSession: vi.fn(async (key: string, title: string) => {
      calls.renamed.push([key, title])
      if (renameFails) throw new Error('not found')  // the gateway's answer, as `api` throws it
      return { ok: true, title }
    }),
    sendChat: vi.fn(async (message: string, key: string, meta?: unknown) => {
      calls.sent.push([message, key, meta])
    }),
  },
}))

beforeEach(() => {
  calls.created.length = 0
  calls.renamed.length = 0
  calls.sent.length = 0
  renameFails = false
  told.length = 0
  nextKey = 0
})

describe('a document comment sent to a new chat', () => {
  it('opens a chat the gateway names, titled after the document, every time', async () => {
    const went: string[] = []
    const target = newSessionTarget((path) => went.push(path), { title: 'Comments: report.md' })

    await target.submit({ message: 'Tighten the intro.', docPaths: ['/notes/report.md'] })
    await target.submit({ message: 'And the summary.', docPaths: [] })

    // No name is sent: a name chosen from the title is what made two comments one chat.
    expect(calls.created).toEqual([undefined, undefined])
    expect(calls.renamed).toEqual([
      ['chat-1-1791067183', 'Comments: report.md'],
      ['chat-2-1791067183', 'Comments: report.md'],
    ])
    expect(calls.sent).toEqual([
      ['Tighten the intro.', 'chat-1-1791067183', { files: ['/notes/report.md'] }],
      ['And the summary.', 'chat-2-1791067183', undefined],
    ])
    expect(went).toEqual(['chat/chat-1-1791067183', 'chat/chat-2-1791067183'])
    expect(told).toEqual([])
  })

  it('says so when the title does not land, and still sends the comment', async () => {
    renameFails = true
    const went: string[] = []
    await newSessionTarget((path) => went.push(path)).submit({ message: 'A note.', docPaths: [] })

    expect(calls.renamed).toEqual([['chat-1-1791067183', 'Document comments']])
    expect(told).toEqual([["Couldn't title the new chat after the document: not found", 'error']])
    expect(calls.sent).toEqual([['A note.', 'chat-1-1791067183', undefined]])
    expect(went).toEqual(['chat/chat-1-1791067183'])
  })
})
