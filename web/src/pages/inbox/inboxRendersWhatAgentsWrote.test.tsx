import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import type { InboxItem, InboxProposal } from '../../lib/api'
import { HOSTILE, HOSTILE_BODY, MD_BLOCK, MD_LINE, inert, rendersBlock, rendersLine } from '../../test/markdownSinks'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'

// ── The Inbox renders what senders and agents wrote ────────────────────────────────────────────
//
// 🔴 The Inbox row stripped its message to plain text (issue 618's fix), so bold and code went
// missing rather than showing; a proposal's title and text preview, and a triage digest's titles and
// reasons, showed their marks. The row is now an excerpt of what the panel renders, its name still
// plain; a proposal's title is a line and its text preview a body (a diff stays a diff); the
// digest's titles and reasons are lines, named in plain text.

const SEEN = 'Skip model_calls in export_digest() in published.py'
const OWNER = 'keyur-golani'

const message = (over: Record<string, unknown> = {}) => ({
  id: 'C1_1', channel: 'C1', channel_name: '#ops', thread_ts: null,
  message: `${MD_LINE}\n\nmore below`, sender_id: 'U1', sender_name: 'Sender',
  classification: 'needs_reply', confidence: 'high', status: 'pending',
  created_at: 1700000000, item_kind: 'message', can_reply: true, owner_username: OWNER, ...over,
})

const DIGEST = {
  state: 'ready', enabled: true, installed: true, error: '', run_id: 'run-abc', permalink: '#/workflows/runs/run-abc',
  title: 'Morning triage', collected: 3, dropped: 0, auto_stage_ran: true, ledger_complete: true, ledger_rows: 1,
  auto_done: [{ ordinal: '2', source_id: 'gh_2', action_type: 'archive', provider: 'inbox-op', rule: 'policy:trivial-tier',
    reversal: '', undoable: false, permalink: '#/workflows/runs/run-abc', title: MD_LINE, source: 'inbox', item_permalink: '', materiality: 'none' }],
  // A proposal waiting on you, with the task a Yes would file; a run that ended; an item kept and
  // waiting; and a proposal no longer offered. Each title is an agent's line.
  pending: [{ ordinal: '1', action_type: 'create_task', tier: 'ask', pattern_key: 'p', clamped: false, action_config: { title: MD_LINE },
    reason: MD_LINE, rule: '', answered: false, answer: '', not_done: '', answer_not_done: '', permalink: '', title: MD_LINE, source: 'inbox',
    item_permalink: '', materiality: 'none' }],
  ran: [{ ordinal: '4', title: MD_LINE, source: 'inbox', item_permalink: '', materiality: 'none', needs_you: false }],
  waiting: [{ ordinal: '5', title: MD_LINE, source: 'inbox', item_permalink: '', materiality: 'none' }],
  no_longer_offered: [{ action_type: 'archive', title: MD_LINE, source: 'inbox', item_permalink: '', first_proposed_at: '', note: 'It waited a week.' }],
  journal: [{ kind: 'skip', seq: 1, ordinal: '3', reason: MD_LINE }],
}

const applyInboxProposal = vi.fn(async () => ({ ok: true }))
/** What the Inbox lists: one message of the owner's own, unless a test says otherwise. */
let ITEMS: unknown[] = []
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    inbox: () => Promise.resolve(ITEMS),
    inboxStatus: () => Promise.resolve({ enabled: true, health: {}, open_count: 1, total_count: 1, owner: OWNER, my_open_count: 1, my_total_count: 1 }),
    proactiveDigest: () => Promise.resolve(DIGEST),
    openInboxItem: () => Promise.resolve({ ok: true }),
    markInboxSeen: () => Promise.resolve({ ok: true, seen: 0 }),
    applyInboxProposal: () => applyInboxProposal(),
    // A widget's frame asks whether its artifact exists.
    artifactExists: () => Promise.resolve(false),
  },
}))

// The app registers its content types at startup (`main.tsx`), so a body that ran its widget blocks
// would draw them here as it does in the app.
registerBuiltinContentTypes()
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:inbox-renders-test'
  URL.revokeObjectURL = () => {}
}

const { InboxPage } = await import('./InboxPage')
const { ProposalsLens } = await import('./ProposalsLens')
const { TriageDigestCard } = await import('./TriageDigestCard')

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); ITEMS = [message()] })

const proposal = (id: string, p: Partial<InboxProposal>): InboxItem => ({
  id, channel: '', channel_name: '', message: String(p.title ?? ''), sender_id: '', sender_name: '', item_kind: 'proposal', status: 'pending',
  refs: { proposal: { title: '', preview: '', preview_kind: 'text', provenance: 'learning', editable: false, apply: { workflow: { ref: 'nightly' } }, ...p } },
} as unknown as InboxItem)

describe('the Inbox row', () => {
  it('is an excerpt of the message: its marks render inline, and its name is plain', async () => {
    render(<InboxPage query={{}} setQuery={() => {}} navigate={vi.fn()} />)
    await waitFor(() => expect(document.querySelector('p.truncate code')).not.toBeNull())
    const excerpt = document.querySelector('p.truncate code')!.closest('p')!
    expect(rendersLine(excerpt)).toEqual([])
    expect(excerpt.textContent).toContain(SEEN)
    // The row's name is the same message as plain text (and capped, as every row name is).
    for (const el of document.querySelectorAll('[aria-label]')) {
      expect(el.getAttribute('aria-label'), 'a name carries a mark').not.toMatch(/`|\*\*/)
    }
  })
})

describe('a hostile message is inert in its row', () => {
  it("the row's excerpt", async () => {
    ITEMS = [message({ message: HOSTILE })]
    render(<InboxPage query={{}} setQuery={() => {}} navigate={vi.fn()} />)
    const excerpt = () => Array.from(document.querySelectorAll('p.truncate')).find((el) => (el.textContent ?? '').includes('open the report'))
    await waitFor(() => expect(excerpt()).toBeDefined())
    expect(inert(excerpt()!)).toEqual([])
  })
})

describe("another owner's thread context stays inside the fence", () => {
  it('each message of the thread renders, inside the quoted group, and stays inert', async () => {
    ITEMS = [message({ owner_username: 'someone-else', thread_context: [
      { sender_name: 'Ana', text: MD_BLOCK, ts: '1700000001' },
      { sender_name: 'Bo', text: `${HOSTILE}\n\n${HOSTILE_BODY}`, ts: '1700000002' },
    ] })]
    render(<InboxPage query={{ open: 'C1_1' }} setQuery={() => {}} navigate={vi.fn()} />)
    const thread = await screen.findByText('Thread context · 2', { exact: false })
    const section = thread.closest('section, div')!.parentElement!
    const fence = within(section).getByRole('group', { name: /^Content from .* — quoted, not your own$/ })
    expect(within(fence).getByText('Ana')).toBeInTheDocument()
    expect(within(fence).getByText('Bo')).toBeInTheDocument()
    const ana = within(fence).getByText('Ana').parentElement!
    expect(rendersBlock(ana)).toEqual([])
    expect(inert(fence, { body: true })).toEqual([])
  })
})

describe("a proposal's title and preview", () => {
  it('the title renders as a line; the checkbox is named in plain text', () => {
    render(<ProposalsLens items={[proposal('lp-1', { title: MD_LINE })]} onChanged={() => {}} />)
    expect(screen.getByRole('checkbox', { name: `Select proposal: ${SEEN}` })).toBeInTheDocument()
    expect(rendersLine(screen.getByTitle(SEEN))).toEqual([])
  })

  it('a text preview renders as a body, and a diff stays a diff', () => {
    const { container } = render(<ProposalsLens items={[
      proposal('lp-1', { title: 'Extract a skill', preview: MD_BLOCK, preview_kind: 'text' }),
      proposal('lp-2', { title: 'Refine a skill', preview: '-old\n+new **bold**', preview_kind: 'diff' }),
    ]} onChanged={() => {}} />)
    const body = container.querySelector('.max-h-40:not(pre)')!
    expect(rendersBlock(body)).toEqual([])
    expect(container.querySelector('pre.font-mono')?.textContent).toBe('-old\n+new **bold**')
  })

  it('a hostile title is inert', () => {
    render(<ProposalsLens items={[proposal('lp-3', { title: HOSTILE })]} onChanged={() => {}} />)
    for (const link of screen.getAllByText(/open the report/)) expect(inert(link.closest('[title]')!)).toEqual([])
  })
})

describe("the triage digest's titles and reasons", () => {
  it('render as lines, named in plain text', async () => {
    render(<TriageDigestCard />)
    await waitFor(() => expect(document.body.textContent).toContain(SEEN))
    const lines = Array.from(document.querySelectorAll('p, span')).filter((el) =>
      (el.textContent ?? '').trim().endsWith(SEEN) && el.querySelector('code') && !el.querySelector('p'))
    expect(lines.length).toBeGreaterThan(0)
    for (const line of lines) expect(line.querySelector('code')?.textContent).toBe('model_calls')
    expect(document.body.textContent).not.toMatch(/`|\*\*published/)
    for (const el of document.querySelectorAll('[aria-label]')) {
      expect(el.getAttribute('aria-label'), 'a name carries a mark').not.toMatch(/`|\*\*/)
    }
  })
})
