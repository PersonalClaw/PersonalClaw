import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComposerData } from './types'

// ── The agent-and-model pill's menu works from the keyboard on every row ─────────────────────────
//
// The pill's flyout declared no popup role, so `ui/Popover` moved focus to its first row and closed
// it on Tab, and nothing handled the arrows. A keyboard user could pick the first agent and nothing
// else: no other agent, no model, no effort. It is now a `menu` of radio items on the shared cursor
// (`lib/useMenuCursor`), opening on the chat's agent, as the composer's other pick-one menus do.

const { AgentModelPill } = await import('./controls')

const DATA = {
  agents: [{ name: 'writer' }, { name: 'researcher' }, { name: 'reviewer' }],
  providers: [],
  models: [
    { name: 'Acme:deep', model_name: 'Deep', provider: 'Acme', description: '' },
    { name: 'Acme:flat', model_name: 'Flat', provider: 'Acme', description: '' },
  ],
  discovered: {},
} as unknown as ComposerData

/** Twelve agents, so the menu shows its search field (over eight). */
const MANY = {
  ...DATA,
  agents: Array.from({ length: 12 }, (_, i) => ({ name: `agent-${i + 1}` })),
} as unknown as ComposerData

const trigger = () => screen.getByRole('button', { name: /^Agent and model: / })
const focused = () => document.activeElement as HTMLElement

async function open(data: ComposerData, agent: string, onSelect = vi.fn()) {
  const user = userEvent.setup()
  render(<AgentModelPill data={data} agent={agent} model="Auto" effort={'' as never} onSelect={onSelect} />)
  trigger().focus()
  await user.keyboard('{Enter}')
  await screen.findByText(data.agents[0].name)
  return { user, onSelect }
}

describe("the pill's menu, from the keyboard", () => {
  it("opens on the chat's agent", async () => {
    await open(DATA, 'researcher')
    await waitFor(() => expect(focused().textContent).toBe('researcher'))
    expect(focused().getAttribute('role')).toBe('menuitemradio')
    expect(focused().getAttribute('aria-checked')).toBe('true')
    expect(focused().closest('[role="menu"]')?.getAttribute('aria-label')).toBe('Agent and model')
  })

  it('moves with the arrows from the agents into the models, and Enter picks the row', async () => {
    const { user, onSelect } = await open(DATA, 'researcher')
    await waitFor(() => expect(focused().textContent).toBe('researcher'))
    await user.keyboard('{ArrowDown}')
    expect(focused().textContent).toBe('reviewer')
    await user.keyboard('{ArrowDown}{ArrowDown}')
    expect(focused().textContent).toMatch(/^Deep/)
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith({ model: 'Acme:deep' })
    // Picking closes the menu and hands focus back to the pill.
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
    expect(focused()).toBe(trigger())
  })

  it('is one tab stop, and Tab closes it', async () => {
    const { user } = await open(DATA, 'writer')
    const rows = ['writer', 'researcher', 'reviewer'].map((n) => screen.getByText(n).closest('button')!)
    expect(rows.map((r) => r.tabIndex)).toEqual([0, -1, -1])
    await user.keyboard('{ArrowDown}')
    await user.tab()
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull())
  })

  it('with the search field shown, starts in the field and Arrow Down moves into the rows', async () => {
    const { user, onSelect } = await open(MANY, 'agent-3')
    expect(focused()).toBe(screen.getByPlaceholderText('Search agents'))
    await user.keyboard('agent-1')
    // The filter leaves agent-1, agent-10, agent-11 and agent-12; the first arrow lands on the first.
    await user.keyboard('{ArrowDown}')
    expect(focused().textContent).toBe('agent-1')
    await user.keyboard('{ArrowDown}{Enter}')
    expect(onSelect).toHaveBeenCalledWith({ agent: 'agent-10' })
  })

  it('Tab from the search field reaches a row and keeps the menu open', async () => {
    const { user } = await open(MANY, 'agent-3')
    expect(focused()).toBe(screen.getByPlaceholderText('Search agents'))
    await user.tab()
    expect(screen.getByText('agent-3')).toBeTruthy()
    expect(focused().textContent).toBe('agent-3')
  })
})
