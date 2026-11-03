import { describe, it, expect, afterEach, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ThreadRail } from './ThreadRail'
import type { Thread, ThreadPip } from '../lib/useThreads'

// ── A sidebar row's state is its dot, and the glow while its work runs ───────────────────────────
//
// Each row of the sidebar's chat history said its state in words after its title: "unread",
// "running", "waiting on you", "failed". The words took the room the title needed and repeated
// what a coloured dot says at a glance. A row now shows its state as a dot in the state's family
// colour (the shared `--color-state-*` tokens), with the running glow around it while its work
// runs. The state stays in the row's name and in the dot's tooltip, so colour is never the only
// cue, and under reduced motion the glow is a still ring around the dot: a running row's colour is
// close to a failed one's, so the ring is what keeps them apart when nothing moves.

const t = (key: string, pip: ThreadPip | null): Thread => ({
  key, kind: 'chat', id: key, title: `Chat ${key}`, projectId: '', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})
const STATES: Array<[ThreadPip, string, string]> = [
  ['unread', 'quiet', 'unread'],
  ['running', 'active', 'running'],
  ['waiting', 'waiting', 'waiting on you'],
  ['failed', 'blocked', 'failed'],
]

function rail() {
  const threads = [...STATES.map(([pip]) => t(pip, pip)), t('calm', null)]
  return render(<ThreadRail groups={[{ id: 'folder:', name: 'No folder', threads }]} selectedKey="" status="ready"
    onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />)
}
const row = (pip: string, word: string) => screen.getByRole('button', { name: `Chat ${pip}, ${word}` })
const dotOf = (el: HTMLElement) => el.querySelector<HTMLElement>('[data-state-family]')

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("a sidebar row's state", () => {
  it('says no state in words on the row', () => {
    rail()
    for (const [pip, , word] of STATES) {
      expect(row(pip, word).textContent).toBe(`Chat ${pip}`)
    }
  })

  it("is a dot in its family's colour, with the state as its tooltip", () => {
    rail()
    for (const [pip, family, word] of STATES) {
      const dot = dotOf(row(pip, word))!
      expect(dot.dataset.stateFamily, pip).toBe(family)
      expect(dot.getAttribute('title'), pip).toBe(word.charAt(0).toUpperCase() + word.slice(1))
      expect(dot.querySelector('[style*="--color-state-"]')?.getAttribute('style'), pip).toContain(`var(--color-state-${family})`)
    }
    expect(dotOf(screen.getByRole('button', { name: 'Chat calm' })), 'a calm row has no dot').toBeNull()
  })

  it('keeps the state in the row\'s name', () => {
    rail()
    for (const [pip, , word] of STATES) expect(row(pip, word)).toBeTruthy()
  })

  it('glows only while its work runs', () => {
    rail()
    for (const [pip, , word] of STATES) {
      expect(row(pip, word).querySelector('.status-pulse') !== null, pip).toBe(pip === 'running')
      expect(row(pip, word).querySelector('[data-still-glow]'), pip).toBeNull()
    }
  })

  it('under reduced motion, the glow is a still ring around the dot, on the running row only', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('prefers-reduced-motion'), media: q, onchange: null,
      addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }))
    rail()
    const running = row('running', 'running')
    expect(running.querySelector('.status-pulse')).toBeNull()
    expect(dotOf(running)?.dataset.stateFamily).toBe('active')
    const ring = running.querySelector<HTMLElement>('[data-still-glow]')
    expect(ring?.getAttribute('style')).toContain('var(--color-state-active)')
    expect(ring?.className).not.toMatch(/animate-|pulse|spin/)
    for (const [pip, , word] of STATES) {
      if (pip !== 'running') expect(row(pip, word).querySelector('[data-still-glow]'), pip).toBeNull()
    }
  })
})
