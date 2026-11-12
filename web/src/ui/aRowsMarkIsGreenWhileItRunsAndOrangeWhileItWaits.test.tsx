import { describe, it, expect, afterEach, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ThreadRail } from './ThreadRail'
import type { Thread, ThreadPip } from '../lib/useThreads'
import { ORB_STATE_OF_PIP } from '../lib/orbState'

// ── A sidebar row's mark is green while it runs, and orange while it waits on you ────────────────
//
// The owner's colours for the sidebar's states: a running chat is green; a chat that asks, or
// stopped for your input, is orange; an unread chat (it finished, and nothing waits on you) is grey;
// a failed one is red. Each is drawn as the orb's 12 px still mark of its state (#6117): a running
// row is working's two rings, a waiting row the clasp, an unread row done's mark, and a failed row
// done's mark with its ×. The mark never moves, at any setting, so the shape and the words tell the
// states apart without the glow the dots had. Only the rail's marks take these colours: the orb,
// the buddy and the project page keep the state tokens.

const t = (key: string, pip: ThreadPip | null): Thread => ({
  key, kind: 'chat', id: key, title: `Chat ${key}`, projectId: '', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})
/** pip, the word in its name, its colour token, its state's form and its badge. */
const RULES: Array<[ThreadPip, string, string, string, string | null]> = [
  ['running', 'running', '--color-ok', 'working', null],
  ['waiting', 'waiting on you', '--color-state-waiting', 'waiting', null],
  ['unread', 'unread', '--color-state-quiet', 'done', null],
  ['failed', 'failed', '--color-state-blocked', 'done', '×'],
]

let frames = 0
function rail() {
  vi.stubGlobal('requestAnimationFrame', () => { frames += 1; return frames })
  const threads = [...RULES.map(([pip]) => t(pip, pip)), t('calm', null)]
  return render(<ThreadRail rows={threads} selectedKey="" status="ready"
    onOpen={() => {}} onRetry={() => {}} />)
}
const row = (pip: string, word: string) => screen.getByRole('button', { name: `Chat ${pip}, ${word}` })
/** What a row's tooltip, the app's own, says: it shows at once on keyboard focus. */
function tipOf(el: Element): string | null {
  act(() => { fireEvent.focus(el) })
  const said = document.querySelector('[data-tooltip-bubble]')?.textContent ?? null
  act(() => { fireEvent.blur(el) })
  return said
}
const markOf = (el: HTMLElement) => el.querySelector<HTMLElement>('[data-orb-mark]')

function prefer(query: string) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes(query), media: q, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  }))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); frames = 0 })

describe("a sidebar row's mark", () => {
  it('is green while it runs, orange while it waits on you, grey when unread and red when failed', () => {
    rail()
    for (const [pip, word, tone] of RULES) {
      expect(markOf(row(pip, word))?.dataset.orbInkToken, pip).toBe(tone)
    }
    expect(markOf(screen.getByRole('button', { name: 'Chat calm' })), 'a calm row has no mark').toBeNull()
  })

  it('is the orb\'s still mark of its state: the rings, the clasp, or done\'s mark, the failure with its ×', () => {
    rail()
    for (const [pip, word, tone, form, glyph] of RULES) {
      expect(ORB_STATE_OF_PIP[pip].state, pip).toBe(form)
      const mark = markOf(row(pip, word))!
      expect(mark.dataset.orbMark, pip).toBe(form)
      expect(mark.querySelector('canvas')?.getAttribute('aria-hidden'), `${pip}: the mark is decoration`).toBe('true')
      expect(mark.style.width, pip).toBe('12px')
      const badge = mark.querySelector<HTMLElement>('[data-orb-badge]')
      expect(badge?.textContent ?? null, pip).toBe(glyph)
      if (badge) expect(badge.style.borderColor, `${pip}: a border survives forced colours`).toBe(`var(${tone})`)
    }
  })

  it('never moves: no glow, no pulse and no frame asked for, with motion allowed', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    rail()
    vi.advanceTimersByTime(2000)
    expect(frames, 'the rail asked for a frame').toBe(0)
    expect(document.querySelector('.status-pulse'), 'a row glows').toBeNull()
    for (const el of document.querySelectorAll('[data-state-family] *')) {
      expect(el.getAttribute('class') ?? '', 'a mark animates').not.toMatch(/animate-|pulse|spin/)
    }
  })

  it('in forced colours takes the system\'s text colour, and its shape still tells the states apart', () => {
    prefer('forced-colors: active')
    rail()
    const forms = RULES.map(([pip, word]) => {
      const mark = markOf(row(pip, word))!
      expect(mark.dataset.orbInkToken, pip).toBe('CanvasText')
      return `${mark.dataset.orbMark}${mark.querySelector('[data-orb-badge]')?.textContent ?? ''}`
    })
    expect(new Set(forms).size, 'two states drew the same mark').toBe(RULES.length)
  })

  it('keeps the word in its tooltip and the row\'s name', () => {
    rail()
    for (const [pip, word] of RULES) {
      expect(tipOf(row(pip, word)), pip).toBe(`Chat ${pip} — ${word.charAt(0).toUpperCase() + word.slice(1)}`)
    }
  })
})
