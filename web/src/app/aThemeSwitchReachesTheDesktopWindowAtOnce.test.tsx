import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { useEffect } from 'react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { ThemeProvider, useMode, type Preference } from './theme'
import { AppearanceProvider, useAppearance } from './appearance'
import { DARK_CANVAS, LIGHT_CANVAS, hexOf, reportAppearance, resetAppearanceReportForTests } from './windowAppearance'

// ── A theme switch reaches the desktop window at once ─────────────────────────────────────────────
//
// Every switch, from the corner control or Settings, writes the mode on the page's root in the commit
// that makes it, before anything reads it or paints it, and says the theme to the desktop shell
// (`pclawDesktop.appearance.changed`) with the colour the page now paints, so the window, the strip
// above the page and the native chrome follow it with no focus change (`desktop/windowAppearance.js`).
// The page's theme colour follows it too, and a paired gateway's window, which has no bridge, is read
// by that.

const CANVAS = { dark: 'rgb(15, 15, 15)', light: 'rgb(240, 244, 248)' }

/** `prefers-color-scheme: light`, which the test can flip, and its listeners. */
function systemScheme(light = false) {
  const listeners = new Set<() => void>()
  const mq = {
    get matches() { return light },
    media: '(prefers-color-scheme: light)',
    onchange: null,
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }
  vi.stubGlobal('matchMedia', (q: string) => (/prefers-color-scheme/.test(q) ? mq : { ...mq, matches: false }))
  return { set(next: boolean) { light = next; for (const cb of listeners) cb() } }
}

/** The body's computed colour stands in for the canvas: an override written on the root, else the
 *  token of the mode the root is in; `unreadable` gives none. */
function paintedCanvas(unreadable = false) {
  const real = window.getComputedStyle.bind(window)
  vi.stubGlobal('getComputedStyle', (el: Element, pseudo?: string | null) => {
    if (el !== document.body) return real(el, pseudo)
    const root = document.documentElement
    const override = root.style.getPropertyValue('--color-canvas').trim()
    const light = root.classList.contains('light')
    const ground = unreadable ? '' : override || (light ? CANVAS.light : CANVAS.dark)
    return { ...real(el, pseudo), backgroundColor: ground } as CSSStyleDeclaration
  })
}

let setPreference: (p: Preference) => void = () => {}
let setColor: (varName: string, mode: 'dark' | 'light', hex: string) => void = () => {}
let setScalar: (varName: string, value: number) => void = () => {}
const seen: Array<[string, string | undefined]> = []
const canvases: string[] = []

/** Takes the switch and the colours, and records what a passive effect of the same commit reads on
 *  the root. */
function Control() {
  const theme = useMode()
  const look = useAppearance()
  setPreference = theme.setPreference
  setColor = look.setColor
  setScalar = look.setScalar
  // A passive effect of the commit that changes the colours: it reads them only if they were
  // written in a layout effect.
  useEffect(() => { canvases.push(document.documentElement.style.getPropertyValue('--color-canvas').trim()) }, [look])
  useEffect(() => { seen.push([theme.mode, document.documentElement.dataset.mode]) }, [theme.mode])
  return null
}

const mount = () => render(<ThemeProvider><AppearanceProvider><Control /></AppearanceProvider></ThemeProvider>)
const settle = () => act(async () => { await Promise.resolve(); await Promise.resolve() })
const meta = () => document.querySelector('meta[name="theme-color"]')?.getAttribute('content')

let changed: ReturnType<typeof vi.fn>

beforeEach(() => {
  localStorage.clear()
  seen.length = 0
  canvases.length = 0
  resetAppearanceReportForTests()
  document.head.innerHTML = '<meta name="theme-color" content="#0f0f0f" />'
  const root = document.documentElement
  root.classList.remove('light')
  root.style.removeProperty('--color-canvas')
  delete root.dataset.modePreference
  root.dataset.mode = 'dark'
  changed = vi.fn()
  window.pclawDesktop = { appearance: { changed } } as unknown as Window['pclawDesktop']
  paintedCanvas()
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  delete window.pclawDesktop
})

describe('a theme switch reaches the desktop window at once', () => {
  it('each switch writes the mode, its preference and the theme colour in the commit, and says it once', async () => {
    systemScheme(false)
    mount()
    await settle()
    changed.mockClear()
    for (const [preference, mode, canvas] of [['light', 'light', '#f0f4f8'], ['dark', 'dark', '#0f0f0f'], ['light', 'light', '#f0f4f8']] as const) {
      await act(async () => { setPreference(preference) })
      await settle()
      const root = document.documentElement
      expect(root.dataset.mode).toBe(mode)
      expect(root.classList.contains('light')).toBe(mode === 'light')
      expect(root.dataset.modePreference).toBe(preference)
      expect(meta()).toBe(canvas)
      expect(changed).toHaveBeenLastCalledWith({ preference, mode, canvas })
    }
    expect(changed).toHaveBeenCalledTimes(3)
  })

  it('writes the root in a layout effect: a passive effect of the same commit reads the new mode', async () => {
    systemScheme(false)
    mount()
    await act(async () => { setPreference('light') })
    await act(async () => { setPreference('dark') })
    expect(seen.length).toBeGreaterThanOrEqual(3)
    for (const [mode, onRoot] of seen) expect(onRoot).toBe(mode)
  })

  it('on System, a change of the system scheme re-resolves the mode and says it', async () => {
    const scheme = systemScheme(false)
    mount()
    await act(async () => { setPreference('auto') })
    await settle()
    expect(changed).toHaveBeenLastCalledWith({ preference: 'auto', mode: 'dark', canvas: '#0f0f0f' })
    await act(async () => { scheme.set(true) })
    await settle()
    expect(document.documentElement.dataset.mode).toBe('light')
    expect(changed).toHaveBeenLastCalledWith({ preference: 'auto', mode: 'light', canvas: '#f0f4f8' })
  })

  it('a change of the canvas colour says the new canvas once, on its own and with a switch', async () => {
    systemScheme(false)
    mount()
    await settle()
    changed.mockClear()
    await act(async () => { setColor('--color-canvas', 'dark', '#101418') })
    await settle()
    expect(changed).toHaveBeenCalledTimes(1)
    expect(changed).toHaveBeenLastCalledWith({ preference: 'dark', mode: 'dark', canvas: '#101418' })
    expect(meta()).toBe('#101418')
    await act(async () => {
      setColor('--color-canvas', 'light', '#fbfaf7')
      setPreference('light')
    })
    await settle()
    expect(changed).toHaveBeenCalledTimes(2)
    expect(changed).toHaveBeenLastCalledWith({ preference: 'light', mode: 'light', canvas: '#fbfaf7' })
    expect(meta()).toBe('#fbfaf7')
    expect(canvases.at(-1), 'the colours are on the root before the commit\'s passive effects').toBe('#fbfaf7')
  })

  it('a change that leaves the theme and its canvas as they were says nothing more', async () => {
    systemScheme(false)
    mount()
    await act(async () => { setPreference('light') })
    await settle()
    const calls = changed.mock.calls.length
    await act(async () => { setScalar('--font-scale', 110) })
    await settle()
    expect(document.documentElement.style.fontSize).toBe('110%')
    expect(changed).toHaveBeenCalledTimes(calls)
  })

  it('a switch with a ground that can\'t be read says the mode\'s canvas', async () => {
    paintedCanvas(true)
    systemScheme(false)
    mount()
    await act(async () => { setPreference('light') })
    await settle()
    expect(changed).toHaveBeenLastCalledWith({ preference: 'light', mode: 'light', canvas: '#f0f4f8' })
  })

  it('in a browser, with no bridge, only the theme colour changes', async () => {
    delete window.pclawDesktop
    systemScheme(false)
    mount()
    await act(async () => { setPreference('light') })
    await settle()
    expect(meta()).toBe('#f0f4f8')
    expect(document.documentElement.dataset.modePreference).toBe('light')
  })
})

describe('the report', () => {
  it('two asks in one task send one report, with the state at its end', async () => {
    systemScheme(false)
    const root = document.documentElement
    root.dataset.mode = 'dark'
    root.dataset.modePreference = 'dark'
    reportAppearance()
    root.classList.add('light')
    root.dataset.mode = 'light'
    root.dataset.modePreference = 'light'
    reportAppearance()
    await Promise.resolve()
    expect(changed).toHaveBeenCalledTimes(1)
    expect(changed).toHaveBeenLastCalledWith({ preference: 'light', mode: 'light', canvas: '#f0f4f8' })
  })

  it('a clear ground is no colour', () => {
    expect(hexOf('rgba(0, 0, 0, 0)')).toBe('')
    expect(hexOf('rgb(240, 244, 248)')).toBe('#f0f4f8')
  })

  it('its canvases are the canvas tokens', () => {
    const tokens = readFileSync(resolve(__dirname, '..', 'design', 'tokens.css'), 'utf8')
    const step = (name: string) => new RegExp(`--${name}:\\s*(#[0-9a-f]{6});`, 'i').exec(tokens)?.[1]?.toLowerCase()
    expect(tokens).toMatch(/--color-canvas:\s*var\(--gray-168\);/)
    expect(tokens).toMatch(/\.light \{[\s\S]*?--color-canvas:\s*var\(--gray-965\);/)
    expect(DARK_CANVAS).toBe(step('gray-168'))
    expect(LIGHT_CANVAS).toBe(step('gray-965'))
  })
})
