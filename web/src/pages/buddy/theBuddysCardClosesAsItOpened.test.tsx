// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { A1, at, run, SITUATION_STATES, W1, W2 } from '../../test/buddyMachine'
import { reduce } from '../../lib/buddy/machine'

// ── The buddy's card closes as it opened (the owner's run, 2026-11-25) ─────────────────────────
//
// A click on a floating orb opened its card ("Nothing running"), and nothing closed it again: a
// second click only opened it, Esc folded only the docked card, and a click outside did nothing.
// Now a click on the orb closes the card it opened, Esc closes a floating card, and the buddy's
// window losing the focus (a click outside) closes an idle card. An alert's card stays until its
// controls answer it: a click outside never takes one away.

const idleFloating = () => at({ presence: 'floating', docked: false, live: [W1, W2], focus: 'w1', card: 'open' })
const floatingAlert = () => at({ presence: 'floating', docked: false, live: [W1, W2], focus: 'w1', queue: [A1], shown: 'a1', beneath: 'floating', card: 'open' })

describe("the owner's sequence: resting, a click, and the card would not close", () => {
  // The orb floated and rested (sleeping: nothing live). A click woke it with its card open on
  // "Nothing running". In 0.4.1 a click on a floating orb only ever opened its card, and only a
  // docked card had a collapse fold, so nothing but a drag closed it: a press that moves the orb
  // at all is a drag, whose start closes the card, which is why some clicks seemed to close it.
  const resting = () => at({ presence: 'sleeping', docked: false, live: [] })

  it('the click that wakes it opens the card, and starts its collapse fold', () => {
    const { state, effects } = run(resting(), [{ type: 'click' }])
    expect(state.presence).toBe('floating')
    expect(state.card).toBe('open')
    expect(effects).toContainEqual({ kind: 'timer', name: 'collapse', start: true })
  })

  it('a second click closes it, a click outside closes it, and the fold closes it when nothing else does', () => {
    const opened = run(resting(), [{ type: 'click' }]).state
    expect(run(opened, [{ type: 'click' }]).state.card).toBe(null)
    expect(run(opened, [{ type: 'blur' }]).state.card).toBe(null)
    expect(run(opened, [{ type: 'timer', name: 'collapse' }]).state.card).toBe(null)
  })

  it('after new work comes and goes, a click opens and closes it the same way', () => {
    const opened = run(resting(), [{ type: 'click' }]).state
    const after = run(opened, [{ type: 'click' }, { type: 'start', work: W1 }, { type: 'finish', id: 'w1', how: 'done' }]).state
    expect(run(after, [{ type: 'click' }]).state.card).toBe('open')
    expect(run(after, [{ type: 'click' }, { type: 'click' }]).state.card).toBe(null)
  })
})

describe('a click on the orb closes the card it opened', () => {
  it('toggles an idle floating card: open, closed, open', () => {
    const closed = { ...idleFloating(), card: null }
    expect(run(closed, [{ type: 'click' }]).state.card).toBe('open')
    expect(run(closed, [{ type: 'click' }, { type: 'click' }]).state.card).toBe(null)
    expect(run(closed, [{ type: 'click' }, { type: 'click' }, { type: 'click' }]).state.card).toBe('open')
  })

  it("toggles an alert's card too, and the alert stays: nothing answers", () => {
    const { state, effects } = run(floatingAlert(), [{ type: 'click' }])
    expect(state.card).toBe(null)
    expect(state.shown).toBe('a1')
    expect(effects.filter((e) => e.kind === 'answer')).toEqual([])
  })

  it('a hover card opens fully on a click, not closed', () => {
    expect(run({ ...idleFloating(), card: 'hover' }, [{ type: 'click' }]).state.card).toBe('open')
  })
})

describe('Esc closes a floating card', () => {
  const esc = { type: 'key', key: 'Escape', windowFocused: true, onAlert: false } as const

  it('closes an idle floating card', () => {
    expect(reduce(idleFloating(), esc, 0).state.card).toBe(null)
  })

  it("closes an alert's card without deciding: the alert stays as its bubble", () => {
    const { state, effects } = reduce(floatingAlert(), { ...esc, onAlert: true }, 0)
    expect(state.card).toBe(null)
    expect(state.shown).toBe('a1')
    expect(effects.filter((e) => e.kind === 'answer')).toEqual([])
  })

  it('still folds a docked expanded buddy as before', () => {
    expect(reduce(SITUATION_STATES.expanded(), esc, 0).state.presence).toBe('compact')
  })
})

describe('a click outside closes an idle card, never an alert', () => {
  it("the window's focus leaving closes an idle floating card", () => {
    expect(reduce(idleFloating(), { type: 'blur' }, 0).state.card).toBe(null)
  })

  it("an alert's card stays open until its controls answer it", () => {
    const { state } = reduce(floatingAlert(), { type: 'blur' }, 0)
    expect(state.card).toBe('open')
    expect(state.shown).toBe('a1')
  })

  it('changes nothing with no card open', () => {
    const closed = { ...idleFloating(), card: null }
    expect(reduce(closed, { type: 'blur' }, 0).state).toEqual(closed)
  })
})

// ── The page: in the buddy's own window, a click outside is heard ───────────────────────────────

const sent: unknown[] = []
vi.mock('../../lib/buddy/host', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  createBuddyHost: () => ({ send: (input: unknown) => sent.push(input), ring: () => {}, stop: () => {}, answer: vi.fn() }),
}))
vi.mock('../../lib/useChatSocket', async (orig) => ({ ...(await orig<Record<string, unknown>>()), useChatSocket: () => {} }))

afterEach(() => {
  sent.length = 0
  delete (window as { pclawBuddy?: unknown }).pclawBuddy
})

function buddyWindow() {
  ;(window as { pclawBuddy?: unknown }).pclawBuddy = {
    pointerOver: () => {}, setRegions: () => {}, dragStart: () => {}, dragBy: () => {}, dragEnd: () => {},
    setPresentation: () => {}, openInApp: vi.fn(),
    onRing: () => () => {}, onConnection: () => () => {}, onPlacement: () => () => {}, onFullScreen: () => () => {},
    onOpenAlert: () => () => {},
  }
}

describe("the buddy's window losing the focus", () => {
  it('tells the buddy, once per blur, and stops when the page goes', async () => {
    buddyWindow()
    const { BuddyPage } = await import('./BuddyPage')
    const { unmount } = render(<BuddyPage sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />)
    window.dispatchEvent(new Event('blur'))
    expect(sent.filter((s) => (s as { type: string }).type === 'blur')).toHaveLength(1)
    unmount()
    window.dispatchEvent(new Event('blur'))
    expect(sent.filter((s) => (s as { type: string }).type === 'blur')).toHaveLength(1)
  })

  it('a tab with no shell hears nothing of it', async () => {
    const { BuddyPage } = await import('./BuddyPage')
    render(<BuddyPage sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />)
    window.dispatchEvent(new Event('blur'))
    expect(sent.some((s) => (s as { type: string }).type === 'blur')).toBe(false)
  })
})
