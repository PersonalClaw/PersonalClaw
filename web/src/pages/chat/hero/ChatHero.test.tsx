import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

// ── The new chat's hero, and the page for a chat that does not exist ────────────────────────────
//
// `ChatHero` is what a new chat opens on: a greeting by first name, the composer it is handed, and
// the owner's starters and suggestions, each of which fills the composer and sends nothing.
// `MissingChat` is what a dead link to a chat opens on: it says so, and carries a typed draft into a
// new chat.

const h = vi.hoisted(() => ({ suggestions: vi.fn(), sessionTemplates: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, suggestions: h.suggestions, sessionTemplates: h.sessionTemplates } }
})
vi.mock('../../../lib/useChatSocket', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/useChatSocket')>()
  return { ...real, useChatSocket: () => {} }
})

let ChatHero: typeof import('./ChatHero')['ChatHero']
let MissingChat: typeof import('../MissingChat')['MissingChat']

const TEMPLATE = { id: 't-1', name: 'Research deep dive', agent: 'researcher', model: '', reasoning_effort: 'high', first_prompt: 'Dig into' }

beforeEach(async () => {
  for (const f of Object.values(h)) f.mockReset()
  h.suggestions.mockResolvedValue({ suggestions: ['Summarise my week', 'Plan the release'], generated_at: 0, stale: false, refreshing: false, needs_model: '' })
  h.sessionTemplates.mockResolvedValue([TEMPLATE])
  ;({ ChatHero } = await import('./ChatHero'))
  ;({ MissingChat } = await import('../MissingChat'))
})
afterEach(() => cleanup())

describe('ChatHero', () => {
  it('greets the owner by first name around the composer it is handed', async () => {
    render(<ChatHero name="Ada Park" stage={<div>the composer</div>} onPickStarter={vi.fn()} onPickSuggestion={vi.fn()} />)
    expect(screen.getByRole('heading', { level: 1 }).textContent).toMatch(/^Good (morning|afternoon|evening), Ada$/)
    expect(screen.getByText('the composer')).toBeTruthy()
    await screen.findByText('Summarise my week')
  })

  it('opens on the agent orb at its 64 px size, still in the rest colour: nothing runs in a new chat', async () => {
    const { container } = render(<ChatHero name="Ada" stage={null} onPickStarter={vi.fn()} onPickSuggestion={vi.fn()} />)
    const orb = container.querySelector('[data-agent-orb]') as HTMLElement
    expect(orb.dataset.orbSize).toBe('64')
    expect(orb.dataset.orbPhase).toBe('idle')
    expect(orb.dataset.agentOrb, 'a still frame: no animation runs on a page with no turn').toBe('still')
    expect(orb.dataset.orbInkToken).toBe('--color-orb-rest')
    expect(screen.queryByRole('img', { name: 'PersonalClaw' }), 'the morphing brand blob is gone').toBeNull()
    await screen.findByText('Summarise my week')
  })

  it('fills the composer from a suggestion or a starter, and sends nothing', async () => {
    const onPickStarter = vi.fn(); const onPickSuggestion = vi.fn()
    render(<ChatHero name="Ada" stage={null} onPickStarter={onPickStarter} onPickSuggestion={onPickSuggestion} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Plan the release' }))
    expect(onPickSuggestion).toHaveBeenCalledWith('Plan the release')
    expect(screen.getByText('Your starters')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Research deep dive/ }))
    expect(onPickStarter).toHaveBeenCalledWith(TEMPLATE)
  })

  it('says it is still reading rather than showing an empty strip', () => {
    h.suggestions.mockReturnValue(new Promise(() => {}))
    h.sessionTemplates.mockReturnValue(new Promise(() => {}))
    render(<ChatHero name="Ada" stage={null} onPickStarter={vi.fn()} onPickSuggestion={vi.fn()} />)
    expect(screen.getAllByRole('status').map((s) => s.getAttribute('aria-busy'))).toEqual(['true', 'true'])
  })

  it('shows neither strip when there is nothing to offer', async () => {
    h.suggestions.mockResolvedValue({ suggestions: [], generated_at: 0, stale: false, refreshing: false, needs_model: '' })
    h.sessionTemplates.mockResolvedValue([])
    render(<ChatHero name="Ada" stage={null} onPickStarter={vi.fn()} onPickSuggestion={vi.fn()} />)
    await vi.waitFor(() => expect(screen.queryAllByRole('status')).toHaveLength(0))
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })
})

describe('MissingChat', () => {
  it('says the chat does not exist and carries a typed draft into a new chat', () => {
    const navigate = vi.fn()
    render(<MissingChat draft="  why does it refetch?  " navigate={navigate} />)
    expect(screen.getByText('This chat doesn’t exist')).toBeTruthy()
    expect(screen.getByText(/Your message was not sent/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Start a new chat/ }))
    expect(navigate).toHaveBeenCalledWith(`chat/new?seed=${encodeURIComponent('why does it refetch?')}`, { replace: true })
    fireEvent.click(screen.getByRole('button', { name: 'See all chats' }))
    expect(navigate).toHaveBeenCalledWith('chat/history')
  })

  it('opens a plain new chat when nothing was typed', () => {
    const navigate = vi.fn()
    render(<MissingChat draft="" navigate={navigate} />)
    expect(screen.queryByText(/Your message was not sent/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Start a new chat/ }))
    expect(navigate).toHaveBeenCalledWith('chat/new', { replace: true })
  })
})
