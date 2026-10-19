/** Each curated scheme is one override sheet, generated from its table in `schemes.ts`.
 *
 * `npm run generate:schemes` writes `schemes/<id>.css` and `schemes/index.css` (gated on
 * `PC_WRITE_SCHEMES=1`, the same shape as `audit:consistency`; a plain test run writes nothing).
 * Every other test here reads what is committed: a sheet that drifts from its table, a scheme
 * without a sheet, a sheet that reaches past layers 1 and 2, or a value that resolves to something
 * the table does not say, each reds.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { SCHEMES, DEFAULT_SCHEME } from './schemes'
import { PERSONALITIES } from './personalities'
import { TOKENS } from './tokenRegistry'
import { normalizeHex, oklch, paletteFamily } from './palette'
import { schemeIndexCss, schemePaletteNames, schemeSheetCss } from './schemeSheet'
import { SCHEME_DIR, loadTokenSheets, schemeSheetFiles, tokenValue, type Mode } from './tokenSheet'
import { tokenLayer } from './tokenLayers'

const WRITE_ENV = 'PC_WRITE_SCHEMES'
const MODES: Mode[] = ['dark', 'light']

function generated(): Record<string, string> {
  const sheets = loadTokenSheets()
  const base = new Map(sheets.palette.map((d) => [d.name, normalizeHex(d.value)]))
  const names = schemePaletteNames(SCHEMES, base)
  const out: Record<string, string> = { 'index.css': schemeIndexCss(SCHEMES) }
  for (const s of SCHEMES) out[`${s.id}.css`] = schemeSheetCss(s, names, base)
  return out
}

describe('scheme sheets: generator', () => {
  it.runIf(process.env[WRITE_ENV] === '1')('writes every sheet from its table', () => {
    const files = generated()
    for (const [f, css] of Object.entries(files)) writeFileSync(join(SCHEME_DIR, f), css, 'utf8')
    expect(Object.keys(files)).toHaveLength(SCHEMES.length + 1)
  })

  it('every committed sheet is what its table generates', () => {
    const want = generated()
    for (const [f, css] of Object.entries(want)) {
      expect(readFileSync(join(SCHEME_DIR, f), 'utf8'), `${f} drifted from schemes.ts: run \`npm run generate:schemes\``).toBe(css)
    }
  })
})

describe('scheme sheets: one per scheme, layers 1 and 2 only', () => {
  const sheets = loadTokenSheets()

  it('every scheme id has exactly one sheet, and there is no sheet without a scheme', () => {
    expect(Object.keys(schemeSheetFiles()).sort()).toEqual(SCHEMES.map((s) => `${s.id}.css`).sort())
    expect(SCHEMES.length).toBe(12)
  })

  it('each sheet has the two blocks for its own id and nothing else', () => {
    for (const s of SCHEMES) {
      expect(sheets.schemes[s.id].selectors, s.id).toEqual([
        `:root[data-theme="${s.id}"]`, `:root.light[data-theme="${s.id}"]`,
      ])
    }
  })

  it('each sheet sets only layer 1 and layer 2 tokens', () => {
    for (const s of SCHEMES) {
      for (const d of [...sheets.schemes[s.id].dark, ...sheets.schemes[s.id].light]) {
        expect([1, 2], `${s.id} sets ${d.name}`).toContain(tokenLayer(d.name))
      }
    }
  })

  it('the light block reassigns every semantic token the dark block sets, so no dark value leaks', () => {
    for (const s of SCHEMES) {
      const semantic = (ds: { name: string }[]) => ds.map((d) => d.name).filter((n) => tokenLayer(n) === 2)
      expect(semantic(sheets.schemes[s.id].light), s.id).toEqual(semantic(sheets.schemes[s.id].dark))
      expect(sheets.schemes[s.id].light.every((d) => tokenLayer(d.name) === 2), `${s.id}: palette steps live in the first block`).toBe(true)
    }
  })

  it('a sheet declares only the palette steps the base sheet lacks', () => {
    const base = new Set(sheets.palette.map((d) => d.name))
    for (const s of SCHEMES) {
      for (const d of sheets.schemes[s.id].dark.filter((x) => tokenLayer(x.name) === 1)) {
        expect(base.has(d.name), `${s.id} redeclares ${d.name}`).toBe(false)
      }
    }
  })

  it('every semantic token a sheet sets is a var() of a palette step', () => {
    for (const s of SCHEMES) {
      for (const d of [...sheets.schemes[s.id].dark, ...sheets.schemes[s.id].light].filter((x) => tokenLayer(x.name) === 2)) {
        expect(d.value, `${s.id} ${d.name}`).toMatch(/^var\(--[a-z]+-\d{1,4}\)$/)
      }
    }
  })

  it('one palette name is one value in every sheet', () => {
    const seen = new Map<string, string>()
    for (const d of sheets.palette) seen.set(d.name, normalizeHex(d.value))
    for (const s of SCHEMES) {
      for (const d of sheets.schemes[s.id].dark.filter((x) => tokenLayer(x.name) === 1)) {
        const v = normalizeHex(d.value)
        expect(seen.get(d.name) ?? v, `${d.name} in ${s.id}`).toBe(v)
        seen.set(d.name, v)
      }
    }
  })

  it('a palette name says what its value is: its hue band, and its lightness within 3 steps', () => {
    const all = [...sheets.palette, ...SCHEMES.flatMap((s) => sheets.schemes[s.id].dark.filter((x) => tokenLayer(x.name) === 1))]
    expect(all.length).toBeGreaterThan(40)
    for (const d of all) {
      const [, family, step] = /^--([a-z]+)-(\d+)$/.exec(d.name)!
      expect(paletteFamily(d.value), d.name).toBe(family)
      expect(Math.abs(Number(step) - oklch(d.value).l * 1000), d.name).toBeLessThanOrEqual(3)
    }
  })

  it('every scheme resolves, in both modes, to exactly the values its table holds', () => {
    for (const s of SCHEMES) {
      for (const mode of MODES) {
        for (const [name, v] of Object.entries(s.colors)) {
          expect(tokenValue(name, mode, s.id), `${s.id} ${mode} ${name}`).toBe(normalizeHex(v[mode]))
        }
      }
    }
  })

  it('the default scheme’s sheet changes nothing: coral is the base sheet', () => {
    for (const mode of MODES) {
      for (const name of Object.keys(SCHEMES[0].colors)) {
        expect(tokenValue(name, mode, DEFAULT_SCHEME), `${mode} ${name}`).toBe(tokenValue(name, mode))
      }
    }
  })

  it('the registry’s defaults are what the base sheet paints, so Settings shows the truth', () => {
    for (const t of TOKENS) {
      if (t.kind !== 'color') continue
      for (const mode of MODES) expect(tokenValue(t.varName, mode), `${mode} ${t.varName}`).toBe(normalizeHex(t[mode]))
    }
  })

  it('every identity is one sheet: its base scheme’s', () => {
    for (const p of PERSONALITIES) {
      expect(Object.keys(schemeSheetFiles()), p.id).toContain(`${p.baseScheme}.css`)
    }
  })

  it('the index imports every sheet once, in SCHEMES order, and loads after tokens.css', () => {
    const index = readFileSync(join(SCHEME_DIR, 'index.css'), 'utf8')
    expect([...index.matchAll(/@import "\.\/([\w-]+)\.css";/g)].map((m) => m[1])).toEqual(SCHEMES.map((s) => s.id))
    const main = readFileSync(join(process.cwd(), 'src/main.tsx'), 'utf8')
    const tokens = main.indexOf("import './design/tokens.css'")
    const schemes = main.indexOf("import './design/schemes/index.css'")
    expect(tokens).toBeGreaterThan(-1)
    expect(schemes, 'the scheme sheets load after the main sheet').toBeGreaterThan(tokens)
  })
})
