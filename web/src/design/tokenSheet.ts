/**
 * The token sheets as data: what `tokens.css` and each scheme sheet declare, and what a token
 * resolves to for a given mode and scheme.
 *
 * The semantic tokens are written as `var()` references to the palette, so a rail can no longer
 * read a value by matching `--color-canvas: #…` in the source. Every rail that measures a token
 * reads it through `tokenValue` here instead, which applies the same cascade the browser does:
 *
 *   1. the base sheet: the palette (`:root`) and the semantic, editor and component layers (`@theme`)
 *   2. `.light`, in light mode: the semantic layer reassigned onto light palette steps
 *   3. the scheme's sheet, `:root[data-theme="<id>"]`, then `:root.light[data-theme="<id>"]` in light
 *
 * then follows each `var()` to a literal. Test-side only: the app never parses its own CSS.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { PALETTE_TOKEN } from './palette'

export type Mode = 'dark' | 'light'
export interface Decl { name: string; value: string }
export interface SchemeSheet { id: string; dark: Decl[]; light: Decl[]; selectors: string[] }
export interface TokenSheets {
  /** `:root` (the palette) and `@theme`, in source order. */
  base: Decl[]
  /** The layer-1 declarations alone. */
  palette: Decl[]
  /** `.light`. */
  light: Decl[]
  schemes: Record<string, SchemeSheet>
}

const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** Top-level `selector { body }` pairs. Nested at-rules come back whole; the cascade ignores them. */
export function topLevelBlocks(css: string): { selector: string; body: string }[] {
  const text = stripComments(css)
  const out: { selector: string; body: string }[] = []
  let depth = 0
  let start = 0
  let selStart = 0
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '{') {
      if (depth === 0) { start = i + 1 }
      depth += 1
    } else if (c === '}') {
      depth -= 1
      if (depth === 0) {
        const head = text.slice(selStart, start - 1)
        // A statement at-rule (`@import "x";`) ends with `;` before the selector: keep the tail.
        const selector = head.slice(head.lastIndexOf(';') + 1).trim()
        out.push({ selector, body: text.slice(start, i) })
        selStart = i + 1
      }
    }
  }
  return out
}

/** The custom-property declarations of a block body, in order (nested rules skipped). */
export function declarations(body: string): Decl[] {
  const out: Decl[] = []
  let depth = 0
  let cur = ''
  for (const c of body) {
    if (c === '{') depth += 1
    if (c === '}') { depth -= 1; cur = ''; continue }
    if (depth > 0) continue
    if (c === ';') {
      const m = /^\s*(--[\w-]+)\s*:\s*([\s\S]*)$/.exec(cur)
      if (m) out.push({ name: m[1], value: m[2].trim().replace(/\s+/g, ' ') })
      cur = ''
    } else cur += c
  }
  return out
}

const SCHEME_DARK = /^:root\[data-theme="([\w-]+)"\]$/
const SCHEME_LIGHT = /^:root\.light\[data-theme="([\w-]+)"\]$/

export function parseTokenSheets(tokensCss: string, schemeCss: Record<string, string>): TokenSheets {
  const base: Decl[] = []
  const palette: Decl[] = []
  const light: Decl[] = []
  for (const { selector, body } of topLevelBlocks(tokensCss)) {
    if (selector === '@theme' || selector === ':root') base.push(...declarations(body))
    else if (selector === '.light') light.push(...declarations(body))
  }
  palette.push(...base.filter((d) => PALETTE_TOKEN.test(d.name)))
  const schemes: Record<string, SchemeSheet> = {}
  for (const [file, css] of Object.entries(schemeCss)) {
    const id = file.replace(/\.css$/, '')
    const sheet: SchemeSheet = { id, dark: [], light: [], selectors: [] }
    for (const { selector, body } of topLevelBlocks(css)) {
      sheet.selectors.push(selector)
      const d = SCHEME_DARK.exec(selector)
      const l = SCHEME_LIGHT.exec(selector)
      if (d && d[1] === id) sheet.dark.push(...declarations(body))
      else if (l && l[1] === id) sheet.light.push(...declarations(body))
    }
    schemes[id] = sheet
  }
  return { base, palette, light, schemes }
}

/** Declared values after the cascade for one mode and scheme (no `var()` followed yet). */
export function cascade(sheets: TokenSheets, mode: Mode, scheme?: string): Map<string, string> {
  const map = new Map<string, string>()
  const apply = (ds: Decl[]) => { for (const d of ds) map.set(d.name, d.value) }
  apply(sheets.base)
  if (mode === 'light') apply(sheets.light)
  const s = scheme ? sheets.schemes[scheme] : undefined
  if (s) {
    apply(s.dark)
    if (mode === 'light') apply(s.light)
  }
  return map
}

/** `value` with every `var(--x)` / `var(--x, fallback)` followed through `map`. */
export function resolveValue(map: Map<string, string>, value: string, seen: string[] = []): string {
  return value.replace(/var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))?[^()]*))?\)/g, (_, name: string, fb?: string) => {
    if (seen.includes(name)) throw new Error(`var() cycle: ${[...seen, name].join(' → ')}`)
    const v = map.get(name)
    if (v == null) {
      if (fb != null) return resolveValue(map, fb.trim(), seen)
      throw new Error(`${name} is not declared`)
    }
    return resolveValue(map, v, [...seen, name])
  })
}

const WEB = process.cwd()
export const TOKENS_CSS = join(WEB, 'src/design/tokens.css')
export const SCHEME_DIR = join(WEB, 'src/design/schemes')

/** Every scheme sheet on disk, keyed by file name (the index sheet is not a scheme). */
export function schemeSheetFiles(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const f of readdirSync(SCHEME_DIR).sort()) {
    if (f.endsWith('.css') && f !== 'index.css') out[f] = readFileSync(join(SCHEME_DIR, f), 'utf8')
  }
  return out
}

let cached: TokenSheets | undefined
export function loadTokenSheets(): TokenSheets {
  cached ??= parseTokenSheets(readFileSync(TOKENS_CSS, 'utf8'), schemeSheetFiles())
  return cached
}

/** The literal a token resolves to — what the browser paints for it — in `mode` under `scheme`
 *  (the base sheet alone when `scheme` is omitted, which is also the default scheme's look). */
export function tokenValue(name: string, mode: Mode, scheme?: string): string {
  const map = cascade(loadTokenSheets(), mode, scheme)
  const v = map.get(name)
  if (v == null) throw new Error(`${name} is not declared for ${mode}${scheme ? ` / ${scheme}` : ''}`)
  return resolveValue(map, v, [name])
}

/** The value as written, after the cascade but before any `var()` is followed. */
export function declaredValue(name: string, mode: Mode, scheme?: string): string | undefined {
  return cascade(loadTokenSheets(), mode, scheme).get(name)
}
