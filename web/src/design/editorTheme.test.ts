// @module-tag tree-scan
/** The editor layer reaches Monaco and xterm, and follows the scheme and the mode (#3809).
 *
 * Monaco and xterm take concrete colours, so `editorTheme.ts` reads the computed layer-3 tokens and
 * builds each editor's theme from them. jsdom cannot compute a `var()` chain, so these tests read the
 * tokens through `tokenSheet.ts`, which runs the browser's cascade over the real sheets: the theme an
 * editor gets for a scheme and mode is checked against what that scheme and mode resolve to.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { join, relative } from 'node:path'
import { SCHEMES } from './schemes'
import { tokenLayer } from './tokenLayers'
import { loadTokenSheets, tokenValue, type Mode } from './tokenSheet'
import {
  MONACO_COLOR_IDS, MONACO_THEME, XTERM_THEME_KEYS, monacoTheme, monacoVar, syncMonacoTheme,
  useTerminalTheme, xtermTheme, xtermVar, type TokenReader,
} from './editorTheme'
import { filesUnder, readSource } from '../test/sourceTree'

const MODES: Mode[] = ['dark', 'light']
const SRC = join(process.cwd(), 'src')
const reader = (mode: Mode, scheme?: string): TokenReader => (name) => tokenValue(name, mode, scheme)
/** The reader the app has, keyed on what `<html>` says: its mode class and its `data-theme`. */
const fromHtml: TokenReader = (name) => {
  const root = document.documentElement
  return tokenValue(name, root.classList.contains('light') ? 'light' : 'dark', root.dataset.theme)
}

afterEach(() => {
  document.documentElement.className = ''
  delete document.documentElement.dataset.theme
})

// WCAG 2.1 relative luminance and contrast, over opaque sRGB hex.
const lum = (hex: string) => {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
}
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }

describe('editor layer: every key the adapter sets is a layer-3 token', () => {
  const sheets = loadTokenSheets()
  const declared = new Map(sheets.base.map((d) => [d.name, d.value]))

  it('names each token after the key it feeds', () => {
    expect(monacoVar('editor.selectionBackground')).toBe('--editor-selection-background')
    expect(monacoVar('editorLineNumber.activeForeground')).toBe('--editor-line-number-active-foreground')
    expect(xtermVar('brightBlack')).toBe('--terminal-bright-black')
    expect(xtermVar('selectionBackground')).toBe('--terminal-selection-background')
  })

  it('declares every Monaco id and every xterm field, in layer 3, in terms of layer 2', () => {
    const vars = [...MONACO_COLOR_IDS.map(monacoVar), ...XTERM_THEME_KEYS.map(xtermVar)]
    expect(vars.length).toBe(29)
    for (const v of vars) {
      expect(tokenLayer(v), v).toBe(3)
      const value = declared.get(v)
      expect(value, `${v} is declared in tokens.css`).toBeDefined()
      expect(value, v).toMatch(/^var\(--[\w-]+\)$/)
      expect(tokenLayer(/^var\((--[\w-]+)\)$/.exec(value!)![1]), `${v} reads layer 2`).toBe(2)
    }
  })

  it('is never reassigned: the light mode and a scheme move it by moving layer 2', () => {
    const layer3 = (ds: { name: string }[]) => ds.filter((d) => tokenLayer(d.name) === 3).map((d) => d.name)
    expect(layer3(sheets.light)).toEqual([])
    for (const s of Object.values(sheets.schemes)) expect(layer3([...s.dark, ...s.light]), s.id).toEqual([])
  })
})

describe('editor layer: the editor and the terminal follow the scheme', () => {
  for (const mode of MODES) {
    it(`${mode}: switching from coral to jade re-themes Monaco and the terminal to the computed layer-3 values`, () => {
      for (const scheme of ['coral', 'jade']) {
        const monaco = monacoTheme(reader(mode, scheme), mode === 'light')
        expect(monaco.base).toBe(mode === 'light' ? 'vs' : 'vs-dark')
        for (const id of MONACO_COLOR_IDS) expect(monaco.colors[id], `${scheme} ${id}`).toBe(tokenValue(monacoVar(id), mode, scheme))
        const term = xtermTheme(reader(mode, scheme)) as Record<string, string>
        for (const key of XTERM_THEME_KEYS) expect(term[key], `${scheme} ${key}`).toBe(tokenValue(xtermVar(key), mode, scheme))
      }
      const coral = monacoTheme(reader(mode, 'coral'), mode === 'light')
      const jade = monacoTheme(reader(mode, 'jade'), mode === 'light')
      // The accent-driven keys move with the scheme…
      expect(jade.colors['editor.selectionBackground']).not.toBe(coral.colors['editor.selectionBackground'])
      expect(jade.colors['editorCursor.foreground']).not.toBe(coral.colors['editorCursor.foreground'])
      expect(xtermTheme(reader(mode, 'jade')).cursor).not.toBe(xtermTheme(reader(mode, 'coral')).cursor)
      // …and the surfaces are the scheme's surfaces, which a curated scheme leaves to the mode.
      expect(jade.colors['editor.background']).toBe(tokenValue('--color-surface-container', mode, 'jade'))
      expect(xtermTheme(reader(mode, 'jade')).background).toBe(tokenValue('--color-surface', mode, 'jade'))
    })
  }

  it('the backgrounds follow the mode', () => {
    expect(monacoTheme(reader('dark')).colors['editor.background']).not.toBe(monacoTheme(reader('light'), true).colors['editor.background'])
    expect(xtermTheme(reader('dark')).background).not.toBe(xtermTheme(reader('light')).background)
  })

  it('a short hex is handed over long, the form Monaco parses', () => {
    expect(monacoTheme(() => '#fff').colors['editor.background']).toBe('#ffffff')
    expect(xtermTheme(() => ' #abc ').background).toBe('#aabbcc')
  })

  it('Monaco is re-themed when <html> changes scheme or mode, and only then', async () => {
    const monaco = { editor: { defineTheme: vi.fn(), setTheme: vi.fn() } }
    const stop = syncMonacoTheme(monaco, fromHtml)
    expect(monaco.editor.defineTheme).toHaveBeenCalledTimes(1)
    expect(monaco.editor.setTheme).toHaveBeenLastCalledWith(MONACO_THEME)

    document.documentElement.dataset.theme = 'jade'
    await new Promise((r) => setTimeout(r))
    expect(monaco.editor.defineTheme).toHaveBeenCalledTimes(2)
    expect(monaco.editor.defineTheme.mock.calls[1][1].colors['editorCursor.foreground']).toBe(tokenValue('--color-primary', 'dark', 'jade'))

    document.documentElement.classList.add('light')
    await new Promise((r) => setTimeout(r))
    expect(monaco.editor.defineTheme).toHaveBeenCalledTimes(3)
    expect(monaco.editor.defineTheme.mock.calls[2][1].base).toBe('vs')

    // An unrelated write on <html> (the shell measures itself into inline vars) moves nothing.
    document.documentElement.style.setProperty('--rightpanel-open', '1')
    await new Promise((r) => setTimeout(r))
    expect(monaco.editor.defineTheme).toHaveBeenCalledTimes(3)
    stop()
    document.documentElement.style.removeProperty('--rightpanel-open')
  })

  it('the terminal theme hook re-themes the terminal when the scheme or the mode changes', async () => {
    const { result } = renderHook(() => useTerminalTheme(fromHtml))
    expect(result.current.background).toBe(tokenValue('--terminal-background', 'dark'))
    await act(async () => { document.documentElement.classList.add('light'); await new Promise((r) => setTimeout(r)) })
    expect(result.current.background).toBe(tokenValue('--terminal-background', 'light'))
    await act(async () => { document.documentElement.dataset.theme = 'ocean'; await new Promise((r) => setTimeout(r)) })
    expect(result.current.cursor).toBe(tokenValue('--color-primary', 'light', 'ocean'))
  })
})

describe('editor layer: no page keeps per-theme editor code', () => {
  const files = filesUnder(SRC, (entry, p) => {
    const rel = relative(SRC, p).replace(/\\/g, '/')
    return !rel.startsWith('design/') && /\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)
  })

  // 🪤 One recorded leftover, and it is dead: `CodeCockpitPage.tsx` still passes `theme={mode …}` to
  // the two cockpit reveals, which ignore it now (they draw from the editor layer). That page is the
  // chat/cockpit regions work's (#3805), so the three props are left for it to drop rather than
  // edited under it; the reveals' own half is asserted below so the prop cannot come back to life.
  const DEAD_REVEAL_PROP = /<(?:DiffReveal|TypingReveal)\b[^\n]*theme=\{mode === 'light' \? 'light' : 'dark'\}/

  it('no source picks an editor theme by mode', () => {
    const offenders = files.flatMap((f) => readSource(f).split('\n')
      .filter((line) => /['"]vs-dark['"]|theme=\{\s*mode\s*===/.test(line) && !DEAD_REVEAL_PROP.test(line))
      .map((line) => `${relative(SRC, f)}: ${line.trim()}`))
    expect(offenders).toEqual([])
  })

  it('the cockpit reveals ignore a theme prop: their colours are the editor layer', () => {
    for (const f of ['pages/code/DiffReveal.tsx', 'pages/code/TypingReveal.tsx']) {
      const src = readSource(join(SRC, f))
      expect(src, f).toMatch(/theme\?: 'light' \| 'dark'/)
      expect(src, f).not.toMatch(/\btheme\s*===|\{[^}]*\btheme\b[^}]*\}\s*:\s*\{/)
      expect(src, f).toMatch(/var\(--editor-background\)/)
      expect(src, f).not.toMatch(/#[0-9a-fA-F]{6}\b|rgba\(/)
    }
  })

  it('every Monaco editor uses the one app theme', () => {
    const editors = files.filter((f) => /'@monaco-editor\/react'/.test(readSource(f)) && /<Monaco\w*\b/.test(readSource(f)))
    expect(editors.length, 'Monaco call sites found').toBeGreaterThanOrEqual(5)
    for (const f of editors) {
      const src = readSource(f)
      const tags = [...src.matchAll(/<Monaco\w*\b[\s\S]*?\/>/g)].map((m) => m[0])
      for (const tag of tags) expect(tag, relative(SRC, f)).toMatch(/theme=\{MONACO_THEME\}/)
    }
  })

  it('the terminal takes its colours from the editor layer, not from literals', () => {
    const src = readSource(join(SRC, 'pages/terminal/TerminalView.tsx'))
    expect(src).toMatch(/useTerminalTheme\(\)/)
    expect(src).not.toMatch(/#[0-9a-fA-F]{6}\b/)
  })
})

describe('editor layer: text and marks read on the editor and terminal surfaces, in every scheme and mode', () => {
  for (const s of SCHEMES) {
    for (const mode of MODES) {
      it(`${s.id} / ${mode}`, () => {
        const t = (name: string) => tokenValue(name, mode, s.id)
        const editor = t('--editor-background')
        expect(ratio(t('--editor-foreground'), editor), 'editor text').toBeGreaterThanOrEqual(4.5)
        expect(ratio(t('--editor-line-number-foreground'), editor), 'line numbers').toBeGreaterThanOrEqual(4.5)
        expect(ratio(t('--editor-line-number-active-foreground'), editor), 'active line number').toBeGreaterThanOrEqual(4.5)
        expect(ratio(t('--editor-foreground'), t('--editor-line-highlight-background')), 'text on the current line').toBeGreaterThanOrEqual(4.5)
        expect(ratio(t('--editor-foreground'), t('--editor-selection-background')), 'selected text').toBeGreaterThanOrEqual(4.5)
        expect(ratio(t('--editor-cursor-foreground'), editor), 'editor cursor (non-text, 3:1)').toBeGreaterThanOrEqual(3)
        const term = t('--terminal-background')
        expect(ratio(t('--terminal-foreground'), term), 'terminal text').toBeGreaterThanOrEqual(4.5)
        expect(ratio(t('--terminal-foreground'), t('--terminal-selection-background')), 'selected terminal text').toBeGreaterThanOrEqual(4.5)
        expect(ratio(t('--terminal-cursor'), term), 'terminal cursor (non-text, 3:1)').toBeGreaterThanOrEqual(3)
      })
    }
  }
})
