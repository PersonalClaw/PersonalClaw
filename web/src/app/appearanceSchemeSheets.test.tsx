import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'

// A curated scheme is its override sheet (`design/schemes/<id>.css`), switched by `data-theme`; only
// the owner's own colours ride inline on <html> (#3809). These pin the store's half of that: what it
// writes inline, what `data-theme` says, and that diverging from a curated scheme keeps its colours.

vi.mock('../lib/api', () => ({ api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) } }))

const { AppearanceProvider, useAppearance } = await import('./appearance')
const { TOKENS } = await import('../design/tokenRegistry')
const { getScheme } = await import('../design/schemes')

const COLOR_VARS = TOKENS.filter((t) => t.kind === 'color').map((t) => t.varName)
const inlineColors = () => COLOR_VARS.filter((v) => document.documentElement.style.getPropertyValue(v) !== '')

let store: ReturnType<typeof useAppearance>
function Probe() { store = useAppearance(); return null }
const mount = () => render(<AppearanceProvider><Probe /></AppearanceProvider>)

const ORIGINAL_MATCH_MEDIA = window.matchMedia
beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  window.localStorage.clear()
  document.documentElement.removeAttribute('style')
})
afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

describe('appearance: a curated scheme is a sheet, not inline colours', () => {
  it('a fresh install writes no colour inline and names the default scheme', () => {
    mount()
    expect(document.documentElement.dataset.theme).toBe('coral')
    expect(inlineColors()).toEqual([])
  })

  it('picking a curated scheme switches data-theme and still writes no colour inline', () => {
    mount()
    act(() => store.applyScheme('jade'))
    expect(document.documentElement.dataset.theme).toBe('jade')
    expect(inlineColors()).toEqual([])
    // The panel still shows the scheme's own values, read from the table its sheet is made from.
    const primary = TOKENS.find((t) => t.varName === '--color-primary')!
    expect(store.colorValue(primary, 'dark')).toBe(getScheme('jade')!.colors['--color-primary'].dark)
    expect(store.currentColors().light['--color-info']).toBe(getScheme('jade')!.colors['--color-info'].light)
  })

  it('switching back from an edited scheme drops every inline colour', () => {
    mount()
    act(() => store.setColor('--color-primary', 'dark', '#123456'))
    expect(inlineColors()).not.toEqual([])
    act(() => store.applyScheme('ocean'))
    expect(inlineColors()).toEqual([])
  })

  it('editing one colour of a curated scheme keeps the other colours of that scheme', () => {
    mount()
    act(() => store.applyScheme('jade'))
    act(() => store.setColor('--color-primary', 'dark', '#123456'))
    const root = document.documentElement
    expect(store.activeScheme).toBe('custom:unsaved')
    // `custom:unsaved` matches no sheet, so the scheme's colours are now inline, with the edit.
    expect(root.dataset.theme).toBe('custom:unsaved')
    expect(root.style.getPropertyValue('--color-primary')).toBe('#123456')
    expect(root.style.getPropertyValue('--color-info')).toBe(getScheme('jade')!.colors['--color-info'].dark)
    expect(root.style.getPropertyValue('--grad-1')).toBe(getScheme('jade')!.colors['--grad-1'].dark)
  })

  it('a colour stored by an older build for a curated scheme still paints as it did', () => {
    // Before the sheets, picking a scheme stored its colours; those keep riding inline.
    window.localStorage.setItem('appearance', JSON.stringify({ scheme: 'jade', colors: getScheme('jade')!.colors, scalars: {}, selects: {} }))
    mount()
    expect(document.documentElement.style.getPropertyValue('--color-primary')).toBe(getScheme('jade')!.colors['--color-primary'].dark)
  })
})
