// @module-tag tree-scan
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { existsSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { filesUnder, readSource } from '../test/sourceTree'
import type { InboxItem, NotificationItem } from '../lib/api'
import type { Segment } from '../pages/chat/chatTypes'

// ── A code block offers Run in a chat's replies only ───────────────────────────────────────────
//
// 🔴 A shell code block offered Run beside Copy wherever it rendered: in a message in the Inbox and
// its thread, a notification, a tool's result in a chat, a scheduled run's result in its chat, a
// heartbeat's result in a chat, the list of saved prompts, a knowledge item, a skill or an app's text.
// It now offers Run only in the agent's own replies in a chat (`RunnableCode`); anywhere else it
// offers Copy.

const { API } = vi.hoisted(() => ({
  API: {
    notifications: vi.fn(),
    ackNotification: vi.fn(),
    autonomyLadder: vi.fn(),
  },
}))
vi.mock('../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    ...API,
    updateInboxItem: () => Promise.resolve({}),
    restoreInboxItem: () => Promise.resolve({}),
    favoriteInboxItem: () => Promise.resolve({}),
    draftInboxReply: () => Promise.resolve({}),
    sendInboxReply: () => Promise.resolve({}),
    sortInboxItem: () => Promise.resolve({}),
    // A drawn widget asks whether it is saved as an artifact.
    artifactExists: () => Promise.resolve(false),
  },
}))
vi.mock('./InvestigateButton', () => ({ InvestigateButton: () => null }))
vi.mock('../pages/inbox/WorkflowGateActions', () => ({ WorkflowGateActions: () => null }))

const { Markdown, RunnableCode } = await import('./Markdown')
const { InboxMessageBody } = await import('../pages/inbox/ForeignContent')
const { InboxDetail } = await import('../pages/inbox/InboxDetail')
const { NotificationsPage } = await import('../pages/notifications/NotificationsPage')
const { AssistantSegments } = await import('../pages/chat/transcript/AssistantSegments')
const { hydrateTurns } = await import('../pages/chat/chatTypes')

const OWNER = 'owner-a'
const SHELL = 'Try this:\n\n```sh\necho ready\n```'
const item = (owner_username: string, message: string) => ({
  id: 'C1_1', channel: 'C1', message, sender_id: 'U1', sender_name: 'Sender', owner_username,
  classification: 'needs_reply', status: 'pending', created_at: 1700000000, item_kind: 'message',
} as unknown as InboxItem)

/** The block offers Copy, and no Run. */
function copyOnly(root: HTMLElement) {
  expect(within(root).getByRole('button', { name: 'Copy code' })).toBeInTheDocument()
  expect(within(root).queryByRole('button', { name: /Run/ })).toBeNull()
}

/** The code block holding `text`, with its buttons. */
const block = (text: string) => Array.from(document.querySelectorAll('pre'))
  .find((pre) => (pre.textContent ?? '').includes(text))?.closest('div.group\\/code') as HTMLElement | undefined

/** A chat turn of `segments`, drawn as the transcript draws it. */
const turnOf = (segments: Segment[], isLast = false) => render(
  <AssistantSegments segments={segments} liveCards={new Set()} isLast={isLast} onApprove={vi.fn()} onAnswerQuestion={vi.fn()}
    onSwitchToAgent={vi.fn()} onOpenFile={vi.fn()} onSetupModel={vi.fn()} />,
)

describe('a message in the Inbox', () => {
  it("another owner's message offers Copy, and no Run", () => {
    const { container } = render(<InboxMessageBody item={item('owner-b', SHELL)} owner={OWNER} />)
    copyOnly(container)
  })

  it('a message the Inbox received for you offers Copy, and no Run', () => {
    const { container } = render(<InboxMessageBody item={item(OWNER, SHELL)} owner={OWNER} />)
    copyOnly(container)
  })

  it('a message on an install with no name set offers Copy, and no Run', () => {
    const { container } = render(<InboxMessageBody item={item(OWNER, SHELL)} owner="" />)
    copyOnly(container)
  })

  it("its thread offers Copy, and no Run", () => {
    render(<InboxDetail item={{ ...item(OWNER, 'hi'), thread_context: [{ sender_name: 'X', text: SHELL, ts: '1700000001' }] } as InboxItem}
      owner={OWNER} onChanged={() => {}} navigate={() => {}} />)
    const thread = screen.getByText('Thread context · 1', { exact: false }).closest('section, div')!.parentElement!
    copyOnly(thread)
  })
})

describe('a notification', () => {
  it('an alert that carries a message offers Copy, and no Run', async () => {
    const note: NotificationItem = {
      kind: 'inbox_alert', title: 'Sender in #general', body: 'Alert (keyword): hi\n```sh\necho ready\n```',
      ts: '2026-11-08T10:00:00+00:00', acked: false,
    }
    sessionStorage.clear()
    API.notifications.mockReset().mockResolvedValue({ notifications: [note] })
    API.ackNotification.mockReset().mockResolvedValue({ ok: true })
    API.autonomyLadder.mockReset().mockRejectedValue(new Error('no ladder in this test'))
    render(<NotificationsPage query={{ open: note.ts }} setQuery={vi.fn()} navigate={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument())
    copyOnly(document.body)
  })
})

describe('any other body', () => {
  it('a body rendered on its own offers Copy, and no Run', () => {
    const { container } = render(<Markdown>{SHELL}</Markdown>)
    copyOnly(container)
  })
})

describe('under RunnableCode', () => {
  it('a shell block offers Run, whatever case its language is written in', () => {
    render(<RunnableCode.Provider value><Markdown>{'```BASH\necho ready\n```'}</Markdown></RunnableCode.Provider>)
    expect(screen.getByRole('button', { name: /Run/ })).toBeInTheDocument()
  })
})

describe('a chat turn', () => {
  const FETCHED = 'A page\n\n```sh\necho fetched\n```'
  const segments: Segment[] = [
    { kind: 'tool', id: 't1', tool: 'web_fetch', inputObj: { url: 'https://example.com/page' }, input: '{"url":"https://example.com/page"}', output: FETCHED, done: true } as Segment,
    { kind: 'text', text: 'Here it is:\n\n```sh\necho reply\n```' } as Segment,
  ]
  it("the agent's reply offers Run, and a fetched page's card offers Copy and no Run", async () => {
    turnOf(segments)
    // The reply's own block.
    const reply = block('echo reply')!
    expect(within(reply).getByRole('button', { name: /Run/ })).toBeInTheDocument()
    // Open the turn's work, then the fetched page's card.
    for (let i = 0; i < 4 && !block('echo fetched'); i++) {
      const toggles = screen.getAllByRole('button').filter((b) => b.getAttribute('aria-expanded') === 'false')
      if (!toggles.length) break
      for (const t of toggles) fireEvent.click(t)
    }
    await waitFor(() => expect(block('echo fetched'), "the fetched page's block").toBeDefined())
    copyOnly(block('echo fetched')!)
  })
})

// A scheduled run's result opened as a chat is written to it as an assistant row the gateway took
// in from outside (`meta.from_outside`): no model of this chat wrote it. The check sits on the
// segment the row becomes, however the row reached the chat.
describe("a scheduled run's result in its chat", () => {
  const FROM_OUTSIDE = { what: 'The result of the scheduled run of j', source: 'trigger:j1', source_type: 'schedule_result', source_id: 'j1' }
  const result = (content: string) => ({ role: 'assistant', content, meta: { from_outside: FROM_OUTSIDE } })
  const reply = (content: string) => ({ role: 'assistant', content })

  it("its code offers Copy and no Run, and the agent's reply after it offers Run", () => {
    const [t] = hydrateTurns([result('# Cron Job Result: j\n\n```sh\necho theirs\n```'), reply('```sh\necho mine\n```')])
    turnOf(t.segments)
    copyOnly(block('echo theirs')!)
    expect(within(block('echo mine')!).getByRole('button', { name: /Run/ })).toBeInTheDocument()
  })

  it("it offers none of a reply's own actions; the same text as a reply does", () => {
    const [theirs] = hydrateTurns([reply('Here is the plan.'), result('# Cron Job Result: j\n\nDone.\n\n[SWITCH_TO_AGENT: go ahead]')])
    const first = turnOf(theirs.segments, true)
    expect(screen.queryByRole('button', { name: /Switch to Agent/ })).toBeNull()
    first.unmount()
    const [mine] = hydrateTurns([reply('Here is the plan.\n\n[SWITCH_TO_AGENT: go ahead]')])
    turnOf(mine.segments, true)
    expect(screen.getByRole('button', { name: /Switch to Agent/ })).toBeInTheDocument()
  })

  it('its text is drawn as written; the same text as a reply is drawn as a reply', async () => {
    // A widget block is drawn by the content type registered for it.
    const { registerBuiltinContentTypes } = await import('./content/registerBuiltins')
    registerBuiltinContentTypes()
    const [t] = hydrateTurns([result('# Cron Job Result: j\n\n<widget title="Theirs">A</widget>'), reply('<widget title="Mine">B</widget>')])
    const { container } = turnOf(t.segments)
    expect(container.textContent).toContain('<widget title="Theirs">')
    expect(container.textContent).not.toContain('<widget title="Mine">')
    // The reply's is drawn, and the result's is not.
    await waitFor(() => expect(container.querySelector('iframe[title="Mine"]')).not.toBeNull())
    expect(container.querySelector('iframe[title="Theirs"]')).toBeNull()
  })
})

// Run is opt-in, so where it is turned on is the whole of the rule: the reply's own prose, and the
// block Open beside carries out of it. A provider anywhere else, a sink no test here mounts
// included, would turn it on there.
describe('who turns Run on', () => {
  it("only a chat reply's own text provides RunnableCode, and only Markdown's beside block carries it over", () => {
    // The web app's sources, from the web folder or the repository's root.
    const SRC = [join(process.cwd(), 'src'), join(process.cwd(), 'web/src')].find((d) => existsSync(join(d, 'ui/Markdown.tsx')))!
    expect(SRC, 'the sources are found, so the scan reads something').toBeTruthy()
    const sources = filesUnder(SRC, (n) => /\.tsx?$/.test(n) && !/\.test\.|\.doc\.ts$/.test(n))
    expect(sources.filter((f) => readSource(f).includes('RunnableCode')).map((f) => relative(SRC, f)).sort()).toEqual(['pages/chat/transcript/AssistantSegments.tsx', 'ui/Markdown.tsx'])
    const segs = readSource(join(SRC, 'pages/chat/transcript/AssistantSegments.tsx'))
    expect(segs.match(/<RunnableCode\b/g), 'a provider, dotted or bare (React 19)').toHaveLength(1)
    expect(segs).toMatch(/<RunnableCode\.Provider key=\{i\} value=\{!seg\.outside\}>\s*<Markdown widgets=\{!seg\.outside\} beside=\{answer\}/)
    const md = readSource(join(SRC, 'ui/Markdown.tsx'))
    expect(md.match(/<RunnableCode\b/g), 'a provider, dotted or bare (React 19)').toHaveLength(1)
    expect(md).toContain('<RunnableCode.Provider value={runnable}><CodeBlock')
  })

  it("only the agent's own words opt in: a chat reply's text, and a peek's reply rows and live reply", () => {
    const SRC = [join(process.cwd(), 'src'), join(process.cwd(), 'web/src')].find((d) => existsSync(join(d, 'ui/Markdown.tsx')))!
    // Every `widgets` attribute on a Markdown, read from each file's syntax tree, so no attribute
    // before it, an arrow function's `=>` included, hides it.
    const widgetsOf = (path: string, text: string): string[] => {
      const file = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
      const found: string[] = []
      const visit = (node: ts.Node) => {
        if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && node.tagName.getText() === 'Markdown') {
          for (const a of node.attributes.properties) {
            if (ts.isJsxAttribute(a) && a.name.getText() === 'widgets') found.push(a.getText())
          }
        }
        node.forEachChild(visit)
      }
      visit(file)
      return found
    }
    const sources = filesUnder(SRC, (n) => /\.tsx$/.test(n) && !/\.test\./.test(n) && n !== 'Markdown.tsx')
    const sinks = Object.fromEntries(sources.map((f) => [relative(SRC, f), widgetsOf(f, readSource(f))]).filter(([, found]) => found.length))
    expect(sinks).toEqual({
      'pages/chat/history/SessionPeek.tsx': ['widgets={!m.meta?.from_outside}', 'widgets'],
      'pages/chat/transcript/AssistantSegments.tsx': ['widgets={!seg.outside}'],
    })
    // The peek's bare one is its live reply, still streaming.
    expect(readSource(join(SRC, 'pages/chat/history/SessionPeek.tsx'))).toContain('<Markdown widgets streaming ')
  })
})
