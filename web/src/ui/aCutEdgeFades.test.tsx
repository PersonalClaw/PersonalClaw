// A pane whose content runs past its edge fades there, the app's one such cue: scrollbars stay
// hidden app-wide (the tenet of 2026-07-07, tokens.css), so a list with more below it, or a panel
// scrolled down, read as if they ended at their edge. jsdom lays nothing out, so each pane's
// metrics are stubbed: its height, its content's height and how far it has scrolled.
import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ScrollFade, cutEdges } from './ScrollFade'

let watchers: Set<() => void>
let fontsLoaded: () => void

/** Stub *el*'s scroll metrics: what it shows, all it holds, and how far down it is. */
function metrics(el: HTMLElement, m: { client: number; scroll: number; top?: number }) {
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => m.client })
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => m.scroll })
  Object.defineProperty(el, 'scrollTop', { configurable: true, get: () => m.top ?? 0 })
}

function pane(container: HTMLElement): HTMLElement {
  return container.querySelector('[data-testid="pane"]') as HTMLElement
}

function fades(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[data-fade]')).map((f) => f.getAttribute('data-fade') ?? '')
}

/** A resize that is not the window's: the pane or something in it changed size. */
function resized() {
  act(() => { for (const fire of watchers) fire() })
}

beforeEach(() => {
  watchers = new Set()
  vi.stubGlobal('ResizeObserver', class {
    fire: () => void
    constructor(callback: ResizeObserverCallback) { this.fire = () => callback([], this as unknown as ResizeObserver) }
    observe() { watchers.add(this.fire) }
    unobserve() { watchers.delete(this.fire) }
    disconnect() { watchers.delete(this.fire) }
  })
  const ready = new Promise<void>((resolve) => { fontsLoaded = resolve })
  Object.defineProperty(document, 'fonts', { configurable: true, value: { ready } })
})

afterEach(() => { vi.unstubAllGlobals() })

describe('a cut edge fades', () => {
  it('says which edges content runs past, and none when it fits', () => {
    expect(cutEdges({ scrollTop: 0, clientHeight: 400, scrollHeight: 400 })).toEqual({ top: false, bottom: false })
    expect(cutEdges({ scrollTop: 0, clientHeight: 400, scrollHeight: 401 })).toEqual({ top: false, bottom: false })
    expect(cutEdges({ scrollTop: 0, clientHeight: 400, scrollHeight: 900 })).toEqual({ top: false, bottom: true })
    expect(cutEdges({ scrollTop: 200, clientHeight: 400, scrollHeight: 900 })).toEqual({ top: true, bottom: true })
    expect(cutEdges({ scrollTop: 500, clientHeight: 400, scrollHeight: 900 })).toEqual({ top: true, bottom: false })
  })

  it('fades the bottom while content runs past it, and the top once it has moved past', () => {
    const m = { client: 400, scroll: 400, top: 0 }
    const { container } = render(<ScrollFade ground="canvas" data-testid="pane"><p>rows</p></ScrollFade>)
    metrics(pane(container), m)
    resized()
    expect(fades(container)).toEqual([])

    m.scroll = 900
    resized()
    expect(fades(container)).toEqual(['bottom'])

    m.top = 200
    act(() => { pane(container).dispatchEvent(new Event('scroll')) })
    expect(fades(container)).toEqual(['top', 'bottom'])

    m.top = 500
    act(() => { pane(container).dispatchEvent(new Event('scroll')) })
    expect(fades(container)).toEqual(['top'])
  })

  it('measures again when the pane or its content changes size, with no window resize', () => {
    const m = { client: 400, scroll: 400 }
    const { container } = render(<ScrollFade ground="surface" data-testid="pane"><p>the skeleton</p></ScrollFade>)
    metrics(pane(container), m)
    resized()
    expect(fades(container)).toEqual([])
    // The body arrives after its skeleton, or Show all opens the rest: the content grows.
    m.scroll = 1200
    resized()
    expect(fades(container)).toEqual(['bottom'])
    // The sidebar collapses or a panel closes: the pane grows past its content.
    m.client = 1300
    resized()
    expect(fades(container)).toEqual([])
  })

  it('measures again once the faces have loaded', async () => {
    const m = { client: 400, scroll: 400 }
    const { container } = render(<ScrollFade ground="canvas" data-testid="pane"><p>text in its fallback face</p></ScrollFade>)
    metrics(pane(container), m)
    m.scroll = 700
    await act(async () => { fontsLoaded(); await Promise.resolve() })
    expect(fades(container)).toEqual(['bottom'])
  })

  it('takes no pointer, says nothing, and keeps itself from the user\'s own ground under forced colours', () => {
    const m = { client: 400, scroll: 900, top: 100 }
    const { container } = render(<ScrollFade ground="surface" data-testid="pane"><p>rows</p></ScrollFade>)
    metrics(pane(container), m)
    resized()
    const drawn = Array.from(container.querySelectorAll('[data-fade]'))
    expect(drawn).toHaveLength(2)
    for (const fade of drawn) {
      expect(fade.getAttribute('aria-hidden')).toBe('true')
      expect(fade.textContent).toBe('')
      const classes = fade.className.split(/\s+/)
      expect(classes).toContain('pointer-events-none')
      expect(classes).toContain('from-surface')
      expect(classes).toContain('forced-colors:[forced-color-adjust:none]')
      expect(classes).toContain('forced-colors:from-[Canvas]')
      expect(classes.some((c) => /^(animate-|transition)/.test(c))).toBe(false)
    }
  })

  it('keeps what a key or a reveal brings into view clear of the fade', () => {
    const { container } = render(<ScrollFade ground="canvas" data-testid="pane"><p>rows</p></ScrollFade>)
    expect(pane(container).style.scrollPaddingTop).toBe('24px')
    expect(pane(container).style.scrollPaddingBottom).toBe('24px')
  })
})
