/** The colour layers hold their shape (`web/DESIGN.md` §2, "The token layers").
 *
 * The palette holds raw values and nothing else; the semantic layer is written only in terms of the
 * palette; dark is the default and light is a reassignment of the semantic layer and nothing else;
 * a component token is composed from the semantic layer and never reassigned. The scheme sheets'
 * half of the same rules is in `schemeSheets.test.ts`; the component half (no component reads a
 * palette token) is in `tokenLint.test.ts`.
 */
import { describe, it, expect } from 'vitest'
import { PALETTE_TOKEN } from './palette'
import { COMPONENT_TOKENS, tokenLayer } from './tokenLayers'
import { cascade, declarations, loadTokenSheets, parseTokenSheets, resolveValue, tokenValue } from './tokenSheet'

/** Elevation sits outside the colour layers: its shadows keep their own light reassignment
 *  (their alpha differs per mode, not only their colour). Named so a new colour cannot hide here. */
const ELEVATION = ['--shadow-composer', '--shadow-menu', '--shadow-sheet', '--shadow-rest', '--neu-extrude', '--glass-highlight']

const sheets = loadTokenSheets()
const refs = (value: string) => [...value.matchAll(/var\(\s*(--[\w-]+)/g)].map((m) => m[1])
const COLOUR_LITERAL = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(/

describe('token layers: the base sheet', () => {
  it('found the layers it is checking (not vacuously green)', () => {
    expect(sheets.palette.length).toBeGreaterThan(40)
    expect(sheets.base.filter((d) => tokenLayer(d.name) === 2).length).toBeGreaterThan(30)
    expect(sheets.light.filter((d) => tokenLayer(d.name) === 2).length).toBeGreaterThan(25)
    for (const t of COMPONENT_TOKENS) expect(sheets.base.some((d) => d.name === t), t).toBe(true)
  })

  it('layer 1 is raw opaque hex and nothing else', () => {
    for (const d of sheets.palette) expect(d.value, d.name).toMatch(/^#[0-9a-f]{6}$/)
  })

  it('layer 2 is written only in terms of layer 1 (or as an alias of another layer-2 token)', () => {
    for (const d of [...sheets.base, ...sheets.light].filter((x) => tokenLayer(x.name) === 2)) {
      expect(d.value, d.name).toMatch(/^var\(--[\w-]+\)$/)
      expect([1, 2], `${d.name} reads ${refs(d.value)[0]}`).toContain(tokenLayer(refs(d.value)[0]))
    }
  })

  it('light mode reassigns layer 2 and nothing else, besides the elevation shadows', () => {
    for (const d of sheets.light) {
      expect(tokenLayer(d.name) === 2 || ELEVATION.includes(d.name), `.light sets ${d.name}`).toBe(true)
    }
  })

  it('a component token reads layer 2 or 4, never the palette, and is never reassigned', () => {
    for (const name of COMPONENT_TOKENS) {
      const d = sheets.base.find((x) => x.name === name)!
      // Scalars (`--glass-alpha`) are fine; a colour it reads is layer 2 or 4, never the palette.
      expect(refs(d.value).some((r) => [2, 4].includes(tokenLayer(r) ?? 0)), `${name} reads a colour token`).toBe(true)
      for (const r of refs(d.value)) expect(tokenLayer(r), `${name} reads ${r}`).not.toBe(1)
      expect(sheets.light.some((x) => x.name === name), `.light reassigns ${name}`).toBe(false)
      for (const s of Object.values(sheets.schemes)) {
        expect([...s.dark, ...s.light].some((x) => x.name === name), `${s.id} reassigns ${name}`).toBe(false)
      }
    }
  })

  it('no colour value lives outside the layers (or the named elevation group)', () => {
    for (const d of sheets.base) {
      if (tokenLayer(d.name) || ELEVATION.includes(d.name)) continue
      expect(COLOUR_LITERAL.test(d.value), `${d.name}: ${d.value}`).toBe(false)
      for (const r of refs(d.value)) expect(PALETTE_TOKEN.test(r), `${d.name} reads the palette`).toBe(false)
    }
  })

  it('a layer-1 token is declared only in the palette block', () => {
    const outside = sheets.base.filter((d) => PALETTE_TOKEN.test(d.name)).length
    expect(outside).toBe(sheets.palette.length)
  })

  it('every token in the base sheet resolves in both modes', () => {
    for (const d of sheets.base) {
      for (const mode of ['dark', 'light'] as const) {
        expect(() => resolveValue(cascade(sheets, mode), d.value, [d.name]), `${mode} ${d.name}`).not.toThrow()
      }
    }
  })
})

describe('token layers: the cascade reader', () => {
  it('reads the .light RULE BLOCK, not a comment that mentions it, and follows var() to the palette', () => {
    const css = [
      '/* a note about .light mode { --color-canvas: #000000; } */',
      ':root { --gray-100: #111111; --gray-900: #eeeeee; }',
      '@theme { --color-canvas: var(--gray-100); --color-alias: var(--color-canvas); }',
      '.light { --color-canvas: var(--gray-900); }',
    ].join('\n')
    const s = parseTokenSheets(css, {
      'x.css': ':root[data-theme="x"] { --gray-500: #777777; --color-canvas: var(--gray-500); }\n:root.light[data-theme="x"] { --color-canvas: var(--gray-100); }',
    })
    const value = (mode: 'dark' | 'light', scheme?: string) => resolveValue(cascade(s, mode, scheme), 'var(--color-alias)')
    expect(value('dark')).toBe('#111111')
    expect(value('light')).toBe('#eeeeee')
    expect(value('dark', 'x')).toBe('#777777')
    expect(value('light', 'x')).toBe('#111111')
  })

  it('an undeclared read is an error, not an empty string', () => {
    expect(() => resolveValue(new Map(), 'var(--missing)')).toThrow(/not declared/)
    expect(resolveValue(new Map(), 'var(--missing, #123456)')).toBe('#123456')
    expect(declarations('--a: 1; .x { --b: 2; } --c: 3;').map((d) => d.name)).toEqual(['--a', '--c'])
  })

  it('agrees with the real sheet on a value every rail leans on', () => {
    expect(tokenValue('--color-canvas', 'dark')).toBe('#0f0f0f')
    expect(tokenValue('--color-canvas', 'light')).toBe('#f0f4f8')
    expect(tokenValue('--color-primary', 'light', 'jade')).toBe('#006c64')
  })
})
