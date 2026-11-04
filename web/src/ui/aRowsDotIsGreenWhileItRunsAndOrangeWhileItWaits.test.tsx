import { describe, it, expect, afterEach, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ThreadRail } from './ThreadRail'
import type { Thread, ThreadPip } from '../lib/useThreads'

// ── A sidebar row's dot is green while it runs, and orange while it waits on you ─────────────────
//
// The owner's rule for the sidebar's state dots. A running chat's dot is green and glows; a chat
// that asks, or stopped for your input, is orange and glows the same way; an unread chat (it
// finished, and nothing waits on you) is a steady grey dot; a failed one is a steady red dot. The
// two that glow are the two where something is happening, and under reduced motion each glow is a
// still ring in its own colour, so both still read apart from the steady dots. Only the rail's dots
// take these colours: the orb, the buddy and the project page keep the state tokens.

const t = (key: string, pip: ThreadPip | null): Thread => ({
  key, kind: 'chat', id: key, title: `Chat ${key}`, projectId: '', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})
/** pip, the word in its name, its colour, and whether it glows. */
const RULES: Array<[ThreadPip, string, string, boolean]> = [
  ['running', 'running', 'var(--color-ok)', true],
  ['waiting', 'waiting on you', 'var(--color-state-waiting)', true],
  ['unread', 'unread', 'var(--color-state-quiet)', false],
  ['failed', 'failed', 'var(--color-state-blocked)', false],
]

function rail() {
  const threads = [...RULES.map(([pip]) => t(pip, pip)), t('calm', null)]
  return render(<ThreadRail groups={[{ id: 'folder:', name: 'No folder', threads }]} selectedKey="" status="ready"
    onOpen={() => {}} onRetry={() => {}} onNewThread={() => {}} />)
}
const row = (pip: string, word: string) => screen.getByRole('button', { name: `Chat ${pip}, ${word}` })
/** The dot itself: the innermost mark, painted in the row's colour. */
const fill = (el: HTMLElement) => el.querySelector<HTMLElement>('[data-state-family] > span:last-child')!

function reduceMotion() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('prefers-reduced-motion'), media: q, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  }))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe("a sidebar row's dot", () => {
  it('is green while it runs, orange while it waits on you, grey when unread and red when failed', () => {
    rail()
    for (const [pip, word, tone] of RULES) {
      expect(fill(row(pip, word)).getAttribute('style'), pip).toContain(tone)
    }
    expect(screen.getByRole('button', { name: 'Chat calm' }).querySelector('[data-state-family]'), 'a calm row has no dot').toBeNull()
  })

  it('glows, in its own colour, while it runs or waits on you, and holds steady otherwise', () => {
    rail()
    for (const [pip, word, tone, glows] of RULES) {
      const glow = row(pip, word).querySelector<HTMLElement>('.status-pulse')
      expect(glow !== null, pip).toBe(glows)
      if (glow) expect(glow.getAttribute('style'), pip).toContain(tone)
      expect(row(pip, word).querySelector('[data-state-family]')?.getAttribute('data-state-dot'), pip).toBe(glows ? 'glowing' : 'still')
    }
  })

  it('under reduced motion, each glow is a still ring in its own colour, and the steady dots have none', () => {
    reduceMotion()
    rail()
    for (const [pip, word, tone, glows] of RULES) {
      const r = row(pip, word)
      expect(r.querySelector('.status-pulse'), pip).toBeNull()
      const ring = r.querySelector<HTMLElement>('[data-still-glow]')
      expect(ring !== null, pip).toBe(glows)
      if (ring) {
        expect(ring.getAttribute('style'), pip).toContain(tone)
        expect(ring.className, pip).not.toMatch(/animate-|pulse|spin/)
      }
    }
  })

  it('keeps the word in its tooltip and the row\'s name', () => {
    rail()
    for (const [pip, word] of RULES) {
      expect(row(pip, word).querySelector('[data-state-family]')?.getAttribute('title'), pip).toBe(word.charAt(0).toUpperCase() + word.slice(1))
    }
  })
})
