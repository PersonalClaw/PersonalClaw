import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { PromptItem } from '../../lib/api'

// ── c1b-105 / c1b-137 (day-7 validation): the user's prompts were buried under the system's ─────
//
// On a default install the Prompts page's default "User" tab listed 39 built-in INTERNAL prompts
// around the one the user wrote, and the chat prompt picker LED with "Eval Judge" and "Task Code
// Classify". Both surfaces filtered on `kind`, and `kind` is the role a prompt's text plays in a
// model call — 35 of the 42 prompts core ships are `kind: user` because the system sends them as
// the user turn of its own one-shot calls. Who ships a prompt is its ORIGIN, recorded as the
// `bundled` tag every shipped prompt is seeded with (`promptMeta.ts:isBundled`).
//
// The fixture is the real shape: the shipped rows carry `source: 'user'` (what the native provider
// stamps on every on-disk record) and the `system, bundled` tags; kinds are split both ways on BOTH
// origins, so a surface still filtering on kind alone cannot pass.

const shipped = (name: string, kind: 'user' | 'system', title: string): PromptItem => ({
  name, kind, title, description: `${title} (internal)`, source: 'user', tags: ['system', 'bundled'], variables: [],
})
const mine = (name: string, kind: 'user' | 'system', title: string): PromptItem => ({
  name, kind, title, description: `${title} (mine)`, source: 'user', tags: ['q4'], variables: [],
})

const PROMPTS: PromptItem[] = [
  shipped('eval-judge', 'user', 'Eval Judge'),
  shipped('task-code-classify', 'user', 'Task Code Classify'),
  shipped('system-chat', 'system', 'System Chat'),
  mine('q4-status-update', 'user', 'Q4 status update'),
  mine('evening-voice', 'system', 'Evening voice'),
]

const prompts = vi.fn<(kind?: string) => Promise<PromptItem[]>>()

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      ...real.api,
      prompts: (kind?: string) => prompts(kind),
      // Snippets still mix both origins — one shipped, one own.
      snippets: () => Promise.resolve([
        { name: 'safety-rules', source: 'user', tags: ['system', 'bundled'], variables: [] },
        { name: 'my-signoff', source: 'user', tags: [], variables: [] },
      ]),
      fileSearch: () => Promise.resolve({ results: [] }),
      knowledgeItems: () => Promise.resolve({ items: [] }),
    },
  }
})

// jsdom does not implement scrollIntoView, which the mention menu's cursor effect calls.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

beforeEach(async () => {
  const { invalidateKeys } = await import('../../lib/data')
  invalidateKeys('', true)
  sessionStorage.clear()
  prompts.mockReset()
  // The server's `?kind=` filter, faithfully: `kind` only, origin untouched.
  prompts.mockImplementation(async (kind) => (kind ? PROMPTS.filter((p) => p.kind === kind) : PROMPTS))
})

async function mountList() {
  const { PromptsListPage } = await import('./PromptsListPage')
  function Harness() {
    const [query, setQ] = useState<Record<string, string>>({})
    const setQuery = (patch: Record<string, string | null | undefined>) => setQ((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
      return next
    })
    return <PromptsListPage onCreate={() => {}} onOpen={() => {}} navigate={() => {}} query={query} setQuery={setQuery} />
  }
  render(<Harness />)
}

/** The prompt names the list is showing — a row's accessible name is its prompt name. */
function listed(): string[] {
  return PROMPTS.map((p) => p.name).filter((n) => screen.queryByRole('button', { name: n }))
}

describe('the Prompts page: your tabs hold your prompts, the shipped ones have their own', () => {
  it('opens on User with ONLY the user\'s own user-kind prompt', async () => {
    await mountList()
    await screen.findByRole('button', { name: 'q4-status-update' })
    expect(listed()).toEqual(['q4-status-update'])
  })

  it('System holds only the user\'s own system prompt', async () => {
    const user = userEvent.setup()
    await mountList()
    await screen.findByRole('button', { name: 'q4-status-update' })
    await user.click(screen.getByRole('radio', { name: /System/ }))
    await screen.findByRole('button', { name: 'evening-voice' })
    expect(listed()).toEqual(['evening-voice'])
  })

  it('Bundled holds every shipped prompt, of both kinds, and says what they are', async () => {
    const user = userEvent.setup()
    await mountList()
    await screen.findByRole('button', { name: 'q4-status-update' })
    await user.click(screen.getByRole('radio', { name: /Bundled/ }))
    await screen.findByRole('button', { name: 'eval-judge' })
    expect(listed()).toEqual(['eval-judge', 'task-code-classify', 'system-chat'])
    expect(screen.getByText(/Shipped with PersonalClaw and its apps, and run by them for their own work/)).toBeTruthy()
  })

  it('offers no Source filter where the tab already IS the origin — and keeps it on Snippets', async () => {
    // A "Bundled" source option on the User tab could only ever filter it to nothing.
    const user = userEvent.setup()
    await mountList()
    await screen.findByRole('button', { name: 'q4-status-update' })
    await user.click(screen.getByRole('button', { name: 'Filter & sort' }))
    expect(await screen.findByText('Sort by')).toBeTruthy()
    expect(screen.queryByText('All sources')).toBeNull()
    // Positive control: snippets still mix shipped and own, so their Source filter survives.
    await user.click(screen.getByRole('button', { name: 'Done' }))
    await user.click(screen.getByRole('radio', { name: /Snippets/ }))
    await user.click(await screen.findByRole('button', { name: 'Filter & sort' }))
    expect(await screen.findByText('All sources')).toBeTruthy()
  })
})

describe('the chat prompt pickers offer the user\'s own prompts', () => {
  it('the Insert-a-prompt palette lists no internal prompt', async () => {
    const { PromptPalette } = await import('../chat/PromptPalette')
    render(<PromptPalette onInsert={() => {}} onClose={() => {}} />)
    // Positive control first: the user's prompt IS listed, so an empty list cannot pass.
    expect(await screen.findByText('Q4 status update')).toBeTruthy()
    expect(screen.queryByText('Eval Judge')).toBeNull()
    expect(screen.queryByText('Task Code Classify')).toBeNull()
    expect(prompts).toHaveBeenCalledWith('user')
  })

  it('the leading-@ mention menu suggests the user\'s prompt, not a shipped one that also matches', async () => {
    const { MentionMenu } = await import('../../ui/composer/MentionMenu')
    const anchor = { current: document.createElement('div') } as React.RefObject<HTMLElement>
    // A query a shipped AND an own user-kind prompt both answer to, so origin is what decides.
    prompts.mockImplementation(async () => [
      shipped('eval-judge', 'user', 'Eval Judge'),
      mine('eval-notes', 'user', 'Eval notes'),
    ])
    render(<MentionMenu query="eval" anchorRef={anchor} open leading idPrefix="t-mention"
      onSelect={() => {}} onClose={() => {}} onActiveIndex={() => {}} />)
    const list = await screen.findByRole('listbox')
    await waitFor(() => expect(within(list).getByText('eval-notes')).toBeTruthy())
    expect(within(list).queryByText('eval-judge')).toBeNull()
  })
})
