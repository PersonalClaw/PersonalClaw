import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { WORK_PHASES, phaseFamily, phaseLabel, type PhaseFamily } from '../../lib/workPhase'

// ── Settings → Design: the agent orb's states, in the owner's own colours ────────────────────────
//
// The orb is the agent's working signal, tinted by the scheme, so an owner choosing a scheme sees
// what it does to the orb here: every phase of the contract at the two sizes the orb is drawn at,
// 20 px at the head of a reply and 64 px in an empty chat, grouped by the four families. It is
// closed until asked for, because the active family moves and a settings page with nothing
// running animates nothing. It is also the surface the visual suite photographs for every state.

vi.mock('../../lib/api', () => ({
  api: new Proxy({}, { get: (_t, key) => (key === 'themes' ? () => Promise.resolve([]) : () => Promise.resolve(null)) }),
}))

import { AgentOrbStates } from './AgentOrbStates'

const FAMILIES: PhaseFamily[] = ['active', 'waiting', 'blocked', 'quiet']

let MATCHING: string[] = []
const ORIGINAL_MATCH_MEDIA = window.matchMedia
beforeEach(() => {
  MATCHING = []
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      get matches() { return MATCHING.some((q) => query.includes(q)) },
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList,
  })
})
afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

const orbs = () => Array.from(document.querySelectorAll<HTMLElement>('[data-agent-orb]'))
const open = () => fireEvent.click(screen.getByRole('button', { name: 'Show its states' }))

describe('the agent orb’s states', () => {
  it('is closed until asked for, so the page animates nothing', () => {
    render(<AgentOrbStates />)
    expect(screen.getByRole('button', { name: 'Show its states' })).toHaveAttribute('aria-expanded', 'false')
    expect(orbs()).toHaveLength(0)
  })

  it('shows every phase of the contract at 20 and 64 px, each named, under its family', () => {
    render(<AgentOrbStates />)
    open()
    expect(screen.getByRole('button', { name: 'Hide its states' })).toHaveAttribute('aria-expanded', 'true')
    const gallery = document.querySelector('[data-orb-gallery]') as HTMLElement
    const groups = within(gallery).getAllByRole('group')
    expect(groups.map((g) => g.getAttribute('data-orb-family'))).toEqual(FAMILIES)
    for (const phase of WORK_PHASES) {
      const card = gallery.querySelector(`[data-orb-state="${phase}"]`) as HTMLElement
      expect(card, phase).toBeTruthy()
      expect(card.closest('[data-orb-family]')?.getAttribute('data-orb-family'), phase).toBe(phaseFamily(phase))
      const words = card.cloneNode(true) as HTMLElement
      for (const o of words.querySelectorAll('[data-agent-orb]')) o.remove()
      expect(words.textContent, 'its words name the phase with the orbs hidden').toBe(phaseLabel(phase))
      const sizes = Array.from(card.querySelectorAll<HTMLElement>('[data-agent-orb]')).map((o) => [o.dataset.orbPhase, o.dataset.orbSize])
      expect(sizes, phase).toEqual([[phase, '64'], [phase, '20']])
    }
    expect(orbs()).toHaveLength(WORK_PHASES.length * 2)
  })

  it('the active family moves, and nothing else does', () => {
    render(<AgentOrbStates />)
    open()
    for (const o of orbs()) {
      const phase = o.dataset.orbPhase as (typeof WORK_PHASES)[number]
      expect(o.dataset.agentOrb, phase).toBe(phaseFamily(phase) === 'active' ? 'animated' : 'still')
    }
  })

  it('under reduced motion every state is one painted frame', () => {
    MATCHING = ['prefers-reduced-motion: reduce']
    render(<AgentOrbStates />)
    open()
    expect(orbs().filter((o) => o.dataset.agentOrb === 'animated')).toHaveLength(0)
  })

  it('names each card once: the orbs are its picture, the words its name', () => {
    render(<AgentOrbStates />)
    open()
    expect(screen.queryAllByRole('img')).toHaveLength(0)
    for (const phase of WORK_PHASES) expect(screen.getAllByText(phaseLabel(phase)).length, phase).toBeGreaterThan(0)
  })
})

describe('Settings → Design', () => {
  it('carries the states beside the scheme preview', async () => {
    const { ThemeProvider } = await import('../../app/theme')
    const { AppearanceProvider } = await import('../../app/appearance')
    const { PersonalityProvider } = await import('../../app/personality')
    const { DesignPanel } = await import('./DesignPanel')
    localStorage.clear()
    render(<ThemeProvider><AppearanceProvider><PersonalityProvider><DesignPanel /></PersonalityProvider></AppearanceProvider></ThemeProvider>)
    expect(screen.getByRole('heading', { name: 'Agent orb' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Show its states' })).toBeTruthy()
  })
})
