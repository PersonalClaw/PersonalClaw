import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { TurnStatusRow, STATUS_WORDS, WORD_MS, SHOW_DELAY_MS, elapsedWords } from './TurnStatusRow'

// ── The running turn's live status, one bare line at the end of the conversation ─────────────
//
// One bare line while a turn runs, with no border, background or pill (#5867): the agent orb in
// the turn's own phase, what it is doing in the gateway's words (the tool by name), a word that
// turns over while it works, the time since the turn began, and the tokens its runtime reported
// (none until one is reported, never an estimate). Esc stops the turn while it shows; its Stop is
// the composer's, so the line has no button of its own. A state is said once, politely; the turning
// word is decoration and is never said. Under reduced motion no word turns over and the orb is
// still.

const T0 = Date.UTC(2026, 9, 23, 9, 0, 0)

let reduced = false
beforeEach(() => {
  vi.useFakeTimers({ now: T0 })
  reduced = false
  window.matchMedia = ((q: string) => ({
    matches: reduced && q.includes('prefers-reduced-motion'), media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const shown = async () => { await act(async () => { vi.advanceTimersByTime(SHOW_DELAY_MS) }) }
const row = () => document.querySelector<HTMLElement>('[data-turn-status]')
const live = () => screen.getByRole('status')
const word = () => document.querySelector('[data-turn-word]')

function draw(over: Partial<Parameters<typeof TurnStatusRow>[0]> = {}) {
  const onStop = vi.fn()
  const props = { running: true, phase: 'thinking' as const, line: '', startedAt: T0, usage: null, onStop, ...over }
  const view = render(<TurnStatusRow {...props} />)
  return { onStop, view, props }
}

describe('what the row shows for each state of the turn', () => {
  it('thinking: the orb thinking, the phase in words, a turning word, the time since it began', async () => {
    draw({ phase: 'thinking' })
    await shown()
    expect(row()?.dataset.phase).toBe('thinking')
    expect(row()?.querySelector('[data-agent-orb]')?.getAttribute('data-orb-phase')).toBe('thinking')
    expect(row()?.querySelector('[data-turn-state]')?.textContent).toBe('Thinking')
    expect(word()?.textContent).toBe(STATUS_WORDS[0])
    expect(row()?.querySelector('[data-turn-elapsed]')?.textContent).toBe('0s')
    await act(async () => { vi.advanceTimersByTime(65_000) })
    expect(row()?.querySelector('[data-turn-elapsed]')?.textContent).toBe('1m 05s')
  })

  it('running a tool: the tool by name, as the gateway says it', async () => {
    draw({ phase: 'tool', line: 'Terminal git log --oneline' })
    await shown()
    expect(row()?.dataset.phase).toBe('tool')
    expect(row()?.querySelector('[data-turn-state]')?.textContent).toBe('Terminal git log --oneline')
    expect(word()).not.toBeNull()
  })

  it('a tool the gateway names nothing for says it runs a tool', async () => {
    draw({ phase: 'tool', line: '' })
    await shown()
    expect(row()?.querySelector('[data-turn-state]')?.textContent).toBe('Running a tool')
  })

  it('streaming: writing the reply', async () => {
    draw({ phase: 'streaming' })
    await shown()
    expect(row()?.querySelector('[data-turn-state]')?.textContent).toBe('Writing')
    expect(word()).not.toBeNull()
  })

  it('waiting on you: the ask in words, and no word turns over while it waits', async () => {
    for (const [phase, said] of [['approval', 'Needs your OK'], ['input', 'Waiting for your answer']] as const) {
      const { view } = draw({ phase })
      await shown()
      expect(row()?.dataset.phase).toBe(phase)
      expect(row()?.querySelector('[data-turn-state]')?.textContent).toBe(said)
      expect(word(), `${phase}: it waits; nothing works`).toBeNull()
      view.unmount()
    }
  })

  it('a turn the gateway has not described yet is waiting to start', async () => {
    draw({ phase: null })
    await shown()
    expect(row()?.dataset.phase).toBe('queued')
    expect(row()?.querySelector('[data-turn-state]')?.textContent).toBe('Waiting to start')
  })

  it('the word turns over while it works, through PersonalClaw\'s own list', async () => {
    draw({ phase: 'thinking' })
    await shown()
    const seen = new Set<string>()
    for (let i = 0; i < STATUS_WORDS.length; i++) {
      seen.add(word()?.textContent ?? '')
      await act(async () => { vi.advanceTimersByTime(WORD_MS) })
    }
    expect([...seen].sort()).toEqual([...STATUS_WORDS].sort())
  })
})

describe('the tokens: only what the runtime reported', () => {
  it('shows none while the runtime has reported none, never an estimate', async () => {
    draw({ usage: null })
    await shown()
    expect(row()?.querySelector('[data-turn-tokens]')).toBeNull()
    expect(row()?.textContent).not.toMatch(/token|\bin\b.*\bout\b/i)
  })

  it('shows the reported tokens, in and out', async () => {
    draw({ usage: { input: 12_840, output: 931 } })
    await shown()
    expect(row()?.querySelector('[data-turn-tokens]')?.textContent).toBe('12,840 in · 931 out')
  })

  it('a report of nothing is no report', async () => {
    draw({ usage: { input: 0, output: 0 } })
    await shown()
    expect(row()?.querySelector('[data-turn-tokens]')).toBeNull()
  })
})

describe('what is said, and what is not', () => {
  it('a state is said once, politely; the turning word and the clock are not said', async () => {
    const { view, props } = draw({ phase: 'thinking' })
    await shown()
    expect(live().getAttribute('aria-live')).toBe('polite')
    expect(live().textContent).toBe('Thinking')
    expect(word()?.getAttribute('aria-hidden')).toBe('true')
    await act(async () => { vi.advanceTimersByTime(WORD_MS * 3) })
    expect(live().textContent, 'the word turning over is not news').toBe('Thinking')
    view.rerender(<TurnStatusRow {...props} phase="tool" line="Search the web" />)
    expect(live().textContent).toBe('Search the web')
  })
})

describe('under reduced motion', () => {
  it('no word turns over and the orb is still; the state and the clock stay', async () => {
    reduced = true
    draw({ phase: 'thinking' })
    await shown()
    expect(word()).toBeNull()
    expect(row()?.querySelector('[data-agent-orb]')?.getAttribute('data-agent-orb'), 'one static frame').not.toBe('animated')
    expect(row()?.querySelector('[data-turn-state]')?.textContent).toBe('Thinking')
    expect(row()?.querySelector('[data-turn-elapsed]')).not.toBeNull()
  })
})

describe('a bare line', () => {
  // The pill the line was: a border, a fill, a rounded pill and its own padding.
  const PILL = /(^|\s)(border|border-\S+|bg-\S+|rounded-\S+|px-\S+|py-\S+|shadow\S*)(\s|$)/

  it('has no border, background or pill of its own', async () => {
    draw()
    await shown()
    const classes = row()!.className
    expect(classes, `the line's classes: ${classes}`).not.toMatch(PILL)
  })

  it('has no Stop button of its own: the composer holds the one', async () => {
    draw()
    await shown()
    expect(row()!.querySelector('button'), 'no control in the line').toBeNull()
    expect(screen.queryByRole('button', { name: 'Stop this turn' })).toBeNull()
  })
})

describe('when it shows, and Esc', () => {
  it('shows a moment after the turn starts, so a fast reply never flashes it, and goes when it ends', async () => {
    const { view, props } = draw()
    expect(row()).toBeNull()
    await shown()
    expect(row()).not.toBeNull()
    view.rerender(<TurnStatusRow {...props} running={false} />)
    expect(row()).toBeNull()
  })

  it('a turn that ends before the delay is never drawn', async () => {
    const { view, props } = draw()
    view.rerender(<TurnStatusRow {...props} running={false} />)
    await shown()
    expect(row()).toBeNull()
  })

  it('Esc stops the turn while the line shows', async () => {
    const { onStop } = draw()
    await shown()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('Esc a dialog, a menu, a field or a held key already took stops nothing', async () => {
    const { onStop } = draw()
    await shown()
    // A dialog open over the chat.
    const dialog = document.createElement('div')
    dialog.setAttribute('aria-modal', 'true')
    document.body.appendChild(dialog)
    fireEvent.keyDown(document.body, { key: 'Escape' })
    dialog.remove()
    // A field of its own (a rename) takes Esc to cancel.
    const field = document.createElement('input')
    document.body.appendChild(field)
    fireEvent.keyDown(field, { key: 'Escape' })
    field.remove()
    // A control that handled it already.
    const menu = document.createElement('div')
    document.body.appendChild(menu)
    menu.addEventListener('keydown', (e) => e.preventDefault())
    fireEvent.keyDown(menu, { key: 'Escape' })
    menu.remove()
    // A held key, and Esc with a modifier.
    fireEvent.keyDown(document.body, { key: 'Escape', repeat: true })
    fireEvent.keyDown(document.body, { key: 'Escape', metaKey: true })
    expect(onStop).not.toHaveBeenCalled()
  })

  it('Esc in the composer, the field the row belongs with, stops the turn', async () => {
    const { onStop } = draw()
    await shown()
    const scope = document.createElement('div')
    scope.setAttribute('data-turn-stop-scope', '')
    const editor = document.createElement('div')
    editor.setAttribute('contenteditable', 'true')
    scope.appendChild(editor)
    document.body.appendChild(scope)
    fireEvent.keyDown(editor, { key: 'Escape' })
    scope.remove()
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('without the shortcut, Esc is left alone', async () => {
    const { onStop } = draw({ stopShortcut: false })
    await shown()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(onStop).not.toHaveBeenCalled()
  })

})

describe('the elapsed time in words', () => {
  it('reads seconds, then minutes and seconds, then hours and minutes', () => {
    expect(elapsedWords(0)).toBe('0s')
    expect(elapsedWords(59_400)).toBe('59s')
    expect(elapsedWords(65_000)).toBe('1m 05s')
    expect(elapsedWords(3_725_000)).toBe('1h 02m')
    expect(elapsedWords(-5)).toBe('0s')
  })
})
