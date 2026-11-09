import { afterEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { DEFAULT_PREFERENCE } from './theme'

// ── The stored theme is on the page before it paints ──────────────────────────────────────────────
//
// `index.html` ships `data-mode="dark"`, and the theme provider only runs after the first commit, so
// a light theme used to open on dark frames, in a browser and in the desktop app. An inline script
// in `<head>`, after the theme-colour tag, reads the stored theme with the provider's key and
// default and writes the mode, its preference and the theme colour before the first paint. Inline,
// not a file: a file at the root would need the browser sign-in's pair, and the page's policy
// already allows an inline script.

const WEB = resolve(__dirname, '..', '..')
const HTML = readFileSync(resolve(WEB, 'index.html'), 'utf8')
const THEME = readFileSync(resolve(__dirname, 'theme.tsx'), 'utf8')
const TOKENS = readFileSync(resolve(WEB, 'src', 'design', 'tokens.css'), 'utf8')

const head = HTML.slice(HTML.indexOf('<head>'), HTML.indexOf('</head>'))
const themeColour = head.indexOf('<meta name="theme-color"')
const afterColour = head.slice(themeColour)
const opens = afterColour.indexOf('<script>')
const closes = afterColour.indexOf('</script>', opens)
// The first script after the theme colour, as index.html writes it, read by position: a regex
// over HTML would be a tag filter (CodeQL's js/bad-tag-filter).
const inline = opens >= 0 && closes >= 0 ? afterColour.slice(opens + '<script>'.length, closes) : null

/** Run the inline script in this document, as the page would before it paints. */
function runIt(stored: string | null, systemLight = false, kept: string | null = null) {
  document.head.innerHTML = '<meta name="theme-color" content="#0f0f0f" />'
  const root = document.documentElement
  root.className = ''
  root.dataset.mode = 'dark'
  delete root.dataset.modePreference
  localStorage.clear()
  if (stored !== null) localStorage.setItem('mode', stored)
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: /prefers-color-scheme: light/.test(q) && systemLight }))
  if (kept === null) delete window.pclawDesktop
  else window.pclawDesktop = { appearance: { kept, changed: () => {} } } as unknown as Window['pclawDesktop']
  new Function(inline!)()
  return {
    mode: root.dataset.mode,
    light: root.classList.contains('light'),
    preference: root.dataset.modePreference,
    colour: document.querySelector('meta[name="theme-color"]')?.getAttribute('content'),
  }
}

/** A palette step's hex in tokens.css. */
const step = (name: string) => new RegExp(`--${name}:\\s*(#[0-9a-f]{6});`, 'i').exec(TOKENS)?.[1]?.toLowerCase()

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); delete window.pclawDesktop })

describe('the stored theme is on the page before it paints', () => {
  it('index.html has an inline script in <head>, after the theme-colour tag, and the static tag stays', () => {
    expect(themeColour).toBeGreaterThan(0)
    expect(inline, 'an inline <script> after the theme-colour tag').not.toBeNull()
    expect(head).toContain('<meta name="theme-color" content="#0f0f0f" />')
  })

  it('a stored light theme opens light, with its preference and the light canvas as the theme colour', () => {
    expect(runIt('light')).toEqual({ mode: 'light', light: true, preference: 'light', colour: '#f0f4f8' })
  })

  it('a stored System theme follows the system scheme', () => {
    expect(runIt('auto', true)).toEqual({ mode: 'light', light: true, preference: 'auto', colour: '#f0f4f8' })
    expect(runIt('auto', false)).toEqual({ mode: 'dark', light: false, preference: 'auto', colour: '#0f0f0f' })
  })

  it('with nothing stored, the theme the desktop shell kept opens, and is stored for the provider', () => {
    expect(runIt(null, false, 'light')).toEqual({ mode: 'light', light: true, preference: 'light', colour: '#f0f4f8' })
    expect(localStorage.getItem('mode')).toBe('light')
    expect(runIt(null, true, 'auto')).toMatchObject({ mode: 'light', preference: 'auto' })
  })

  it('a stored theme beats the kept one, and a kept one that is no theme is ignored', () => {
    expect(runIt('dark', false, 'light')).toMatchObject({ mode: 'dark', preference: 'dark' })
    expect(localStorage.getItem('mode')).toBe('dark')
    expect(runIt(null, false, 'sepia')).toMatchObject({ mode: DEFAULT_PREFERENCE, preference: DEFAULT_PREFERENCE })
    expect(localStorage.getItem('mode')).toBeNull()
  })

  it('nothing stored, or junk, is the provider\'s default', () => {
    for (const stored of [null, 'sepia']) {
      const opened = runIt(stored)
      expect(opened.preference).toBe(DEFAULT_PREFERENCE)
      expect(opened.mode).toBe(DEFAULT_PREFERENCE)
    }
  })

  it('reads the provider\'s key and default, and writes the canvas tokens', () => {
    expect(THEME).toMatch(/const KEY = 'mode'/)
    expect(inline!).toContain("localStorage.getItem('mode')")
    expect(inline!).toContain(`'${DEFAULT_PREFERENCE}'`)
    expect(TOKENS).toMatch(/--color-canvas:\s*var\(--gray-168\);/)
    expect(TOKENS).toMatch(/\.light \{[\s\S]*?--color-canvas:\s*var\(--gray-965\);/)
    expect(inline!).toContain(`'${step('gray-168')}'`)
    expect(inline!).toContain(`'${step('gray-965')}'`)
  })
})
