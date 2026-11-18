import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { WORK_PHASES, phaseLabel } from '../../lib/workPhase'
import { ORB_SCENES, ORB_SCENE_OF, ORB_STAGES, ORB_STAGE_LABELS, SCENE_MOVES, markSceneOfPhase, type OrbScene } from '../../lib/orbState'
import type { WorkPhase } from '../../lib/workPhase'

// ── Settings → Design: the agent orb's states, in the owner's own colours ────────────────────────
//
// The orb is the agent's working signal, tinted by the scheme, so an owner choosing a scheme sees
// what it does to the orb here: every state it can show, each with what drives it (a phase of the
// contract or a page's stage), at the three sizes the orb is drawn at, 64 px in an empty chat, 20 px
// in a turn's live line and 12 px as a list row's still mark, grouped by family
// (`docs/design/agent-orb.md`). It is closed until asked for, because most states move and a
// settings page with nothing running animates nothing. It is also the surface the visual suite
// photographs for every state.

vi.mock('../../lib/api', () => ({
  api: new Proxy({}, { get: (_t, key) => (key === 'themes' ? () => Promise.resolve([]) : () => Promise.resolve(null)) }),
}))

import { AgentOrbStates } from './AgentOrbStates'

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

  it('shows every phase of the contract and every stage at 64, 20 and 12 px, each named, under its state and family', () => {
    render(<AgentOrbStates />)
    open()
    expect(screen.getByRole('button', { name: 'Hide its states' })).toHaveAttribute('aria-expanded', 'true')
    const gallery = document.querySelector('[data-orb-gallery]') as HTMLElement
    const families = Array.from(gallery.querySelectorAll('[data-orb-sheet-family]')).map((g) => g.getAttribute('data-orb-sheet-family'))
    expect(families).toEqual(['Quiet', 'Thinking', 'Working', 'Needs you', 'Voice', 'Stopped'])
    for (const family of gallery.querySelectorAll<HTMLElement>('[data-orb-sheet-family]')) {
      expect(within(family).getByText(family.dataset.orbSheetFamily!, { selector: 'h3, h2' })).toBeTruthy()
    }
    // A tool step is shown for each call that draws a state of its own: one that reads, one that
    // edits. One that runs is drawn as a step that says nothing is, so it is the phase's own card.
    const drivers: [string, OrbScene, string, OrbScene][] = [
      ...WORK_PHASES.map((p) => [p, ORB_SCENE_OF[p], phaseLabel(p), markSceneOfPhase(p)] as [string, OrbScene, string, OrbScene]),
      ['tool-read', 'reading', `${phaseLabel('tool')}: a call that reads`, markSceneOfPhase('tool')],
      ['tool-edit', 'editing', `${phaseLabel('tool')}: a call that edits`, markSceneOfPhase('tool')],
      ...ORB_STAGES.map((s) => [s, s, ORB_STAGE_LABELS[s], markSceneOfPhase('idle' as WorkPhase, s)] as [string, OrbScene, string, OrbScene]),
    ]
    for (const [driver, scene, label, mark] of drivers) {
      const [phase, act] = driver.split('-')
      const card = gallery.querySelector(act ? `[data-orb-sheet-phase="${phase}"][data-orb-sheet-act="${act}"]` : `[data-orb-sheet-phase="${driver}"]:not([data-orb-sheet-act])`) as HTMLElement
      expect(card, driver).toBeTruthy()
      expect(card.getAttribute('data-orb-sheet-state'), driver).toBe(scene)
      expect(card.textContent, 'its words name the driver with the orbs hidden').toContain(label)
      const sizes = Array.from(card.querySelectorAll<HTMLElement>('[data-agent-orb]')).map((o) => [o.dataset.orbScene, o.dataset.orbSize])
      // The 12 px mark is the sphere's own form for it.
      expect(sizes, driver).toEqual([[scene, '64'], [scene, '20'], [mark, '12']])
    }
    expect(orbs()).toHaveLength(drivers.length * 3)
    // Every one of the twenty states is driven by something the gallery shows.
    const shown = new Set(orbs().filter((o) => o.dataset.orbSize !== '12').map((o) => o.dataset.orbScene))
    expect(ORB_SCENES.filter((s) => !shown.has(s))).toEqual([])
  })

  it('every state but rest, the waits, done and the stops moves at 64 and 20 px, and nothing else does', () => {
    render(<AgentOrbStates />)
    open()
    for (const o of orbs()) {
      const scene = o.dataset.orbScene as OrbScene
      const moves = SCENE_MOVES[scene] && o.dataset.orbSize !== '12'
      expect(o.dataset.agentOrb, `${scene} at ${o.dataset.orbSize}`).toBe(moves ? 'animated' : 'still')
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

describe('Settings → Appearance → Design', () => {
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
