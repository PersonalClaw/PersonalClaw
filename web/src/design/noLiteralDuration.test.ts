// @module-tag tree-scan
/**
 * Every duration is named.
 *
 * An animation or a transition takes its length from a duration token, so the app runs on one set
 * of lengths, each declared once and each with a slider in Settings → Design → Motion, instead of on
 * numbers scattered through the tree. This rail holds the literal side of that:
 *
 *   1. Stylesheets: no `animation` or `transition` declaration, and none of their `-duration` and
 *      `-delay` longhands, carries a literal length. A length is read from a token with `var()`,
 *      and a multiple of one goes through `calc()`.
 *   2. Inline styles: no `transition` or `animation` string (or its `Duration` and `Delay`
 *      longhands) carries a literal length, or builds one from numbers (`${0.6 + i * 0.15}s`).
 *   3. Tailwind: no `duration-<n>`, `delay-<n>` or arbitrary length. The token form is
 *      `duration-(--duration-short)`.
 *   4. Framer: no `duration:` or `delay:` written as a number outside `design/motion.ts`, which
 *      declares the JS face of the tokens (`duration.*`, `spring.effects`, `instant`).
 *
 * An instant is not a length. `0`, the reduced-motion reset's `0.001ms` and `duration-0` pass; in
 * JS an instant is spelled `instant`.
 *
 * Then the token side, the way `docs/design/motion.md` §7 adds a motion token: each `--duration-*`
 * is declared once in `tokens.css`, registered in the Motion group with the same default, read
 * somewhere, and, where JS reads it, mirrored by `duration.<name>` through `runtime`, so the slider
 * moves the CSS and the JS together. Its reduced-motion answer is the sheet's global reset, which
 * flattens every CSS animation and transition whatever token it reads.
 *
 * It is a TEXT scan over source with comments blanked, because the files explain these lengths in
 * prose.
 */

import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'
import { filesUnder, readSource } from '../test/sourceTree'
import { duration, spring } from './motion'
import { runtime } from './runtime'
import { TOKENS, type ScalarToken } from './tokenRegistry'

const SRC = join(process.cwd(), 'src')
const SHEET = 'design/tokens.css'
/** Declares `duration`, `spring.effects` and `instant`: the JS tokens themselves. */
const MOTION_MODULE = 'design/motion.ts'

const relOf = (path: string) => path.slice(SRC.length + 1).split('\\').join('/')
const read = (rel: string) => readSource(join(SRC, rel))

const STYLESHEETS = filesUnder(SRC, (name) => name.endsWith('.css')).map(relOf).sort()
const SOURCES = filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name))
  .map(relOf)
  .filter((rel) => !rel.startsWith('test/'))
  .sort()

// ── the detectors ──────────────────────────────────────────────────────────────────────────────

/** Blank every comment, keeping line structure. The `[^:]` guard keeps `https://` from reading as
 *  a line comment; a stylesheet has block comments only. */
function blankComments(src: string, css = false): string {
  const blocks = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  if (css) return blocks
  return blocks.replace(/(^|[^:])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

/** A length: `120ms`, `1.4s`, `.9s`. Not inside a name (`--z-2s`, `h2s`). */
const LENGTH = /(?<![\w.$-])(\d*\.?\d+)(ms|s)\b/g

/** The lengths in `text` that are not an instant (zero, or under a millisecond). */
function lengths(text: string): string[] {
  return [...text.matchAll(LENGTH)]
    .filter(([, n, unit]) => Number(n) * (unit === 's' ? 1000 : 1) >= 1)
    .map(([m]) => m)
}

interface Hit { file: string; line: number; kind: string; text: string }

const CSS_TIMING = /(?:^|[{;\s])((?:-webkit-)?(?:animation|transition)(?:-duration|-delay)?)\s*:\s*([^;{}]*)/g

/** Literal lengths in a stylesheet's animation and transition declarations. */
function cssHits(file: string, src: string): Hit[] {
  const code = blankComments(src, true)
  const raw = src.split('\n')
  const hits: Hit[] = []
  for (const m of code.matchAll(CSS_TIMING)) {
    const found = lengths(m[2])
    if (found.length === 0) continue
    const line = code.slice(0, m.index! + m[0].indexOf(m[1])).split('\n').length
    hits.push({ file, line, kind: `${m[1]}: ${found.join(', ')}`, text: raw[line - 1].trim() })
  }
  return hits
}

const INLINE_KEY = /\b(?:transition|animation)(?:Duration|Delay)?\s*:/
const STRING = /'[^'\n]*'|"[^"\n]*"|`[^`\n]*`/g
/** A template that computes a length from numbers: `${0.6 + i * 0.15}s`. */
const COMPUTED_LENGTH = /\$\{[^}]*\d[^}]*\}m?s\b/
/** `duration-150`, `delay-75`, `duration-[250ms]`. `duration-0` is an instant. */
const TW_LENGTH = /(?<![\w-])(?:duration|delay)-(?!0(?![\w.-]))(?:\d|\[)/
const TW_ARBITRARY = /(?<![\w-])animate-\[[^\]\s]*\d(?:ms|s)|\[(?:animation|transition)[\w-]*:[^\]\s]*\d(?:ms|s)/
/** A framer `duration:` or `delay:` written as a number: `duration: 0.15`, `delay: .04`. */
const FRAMER_LITERAL = /(?<![\w.$])(?:duration|delay)\s*:\s*-?\.?\d/

/** Literal lengths in a component: inline styles, Tailwind classes and framer transitions. */
function sourceHits(file: string, src: string): Hit[] {
  const raw = src.split('\n')
  const hits: Hit[] = []
  blankComments(src).split('\n').forEach((code, i) => {
    const at = (kind: string) => hits.push({ file, line: i + 1, kind, text: raw[i].trim() })
    if (INLINE_KEY.test(code)) {
      const strings = code.match(STRING) ?? []
      if (strings.some((s) => lengths(s).length > 0 || (s.startsWith('`') && COMPUTED_LENGTH.test(s)))) at('inline style length')
    }
    if (TW_LENGTH.test(code) || TW_ARBITRARY.test(code)) at('Tailwind length')
    if (file !== MOTION_MODULE && FRAMER_LITERAL.test(code)) at('framer length')
  })
  return hits
}

const show = (h: Hit) => `${h.file}:${h.line} ${h.kind}  ${h.text}`

// ── the token side ─────────────────────────────────────────────────────────────────────────────

/** Every `--duration-*` declaration in the main sheet, in order. */
function declarations(): { name: string; value: string }[] {
  const code = blankComments(read(SHEET), true)
  return [...code.matchAll(/(?<![\w-])--duration-([\w-]+)\s*:\s*([^;]+);/g)].map(([, name, value]) => ({ name, value: value.trim() }))
}

/** A declared length in milliseconds, or NaN when it is not one plain length. */
function toMs(value: string): number {
  const m = /^(\d*\.?\d+)(ms|s)$/.exec(value.trim())
  return m ? Number(m[1]) * (m[2] === 's' ? 1000 : 1) : NaN
}

const camel = (name: string) => name.replace(/-(\w)/g, (_, c: string) => c.toUpperCase())
const registered = (name: string) =>
  TOKENS.find((t): t is ScalarToken => t.kind === 'scalar' && t.varName === `--duration-${name}`)

/** The one-shot steps the app's transitions take. Pinned by name so a rename shows up here. */
const STEPS = ['short', 'effects', 'medium', 'long']

const RUNTIME_DEFAULTS = { ...runtime }
afterEach(() => { Object.assign(runtime, RUNTIME_DEFAULTS) })

describe('no literal duration in an animation or a transition', () => {
  it('in a stylesheet', () => {
    const hits = STYLESHEETS.flatMap((rel) => cssHits(rel, read(rel))).map(show)
    expect(hits, [
      'a literal length in an animation or transition declaration:',
      ...hits.map((h) => `  ${h}`),
      'Read a --duration-* token with var(), or calc() a multiple of one.',
    ].join('\n')).toEqual([])
  })

  it('in a component: an inline style, a Tailwind class or a framer transition', () => {
    const hits = SOURCES.flatMap((rel) => sourceHits(rel, read(rel))).map(show)
    expect(hits, [
      'a literal length in a component:',
      ...hits.map((h) => `  ${h}`),
      'CSS reads var(--duration-*), Tailwind takes duration-(--duration-*), and framer takes duration.*,',
      'spring.effects or instant from design/motion.',
    ].join('\n')).toEqual([])
  })
})

describe('each duration token is added the way motion.md §7 adds one', () => {
  it('the steps are declared once each, as plain lengths', () => {
    const decls = declarations()
    for (const step of STEPS) expect(decls.map((d) => d.name), `--duration-${step} is not declared`).toContain(step)
    const names = decls.map((d) => d.name)
    expect(names.filter((n, i) => names.indexOf(n) !== i), 'a duration token is declared twice').toEqual([])
    for (const d of decls) expect(toMs(d.value), `--duration-${d.name}: ${d.value} is not one length`).toBeGreaterThan(0)
  })

  it('each is registered in the Motion group, with the default the sheet declares', () => {
    for (const d of declarations()) {
      const t = registered(d.name)
      expect(t, `--duration-${d.name} has no registry entry, so the Design panel has no slider for it`).toBeTruthy()
      expect(t!.group).toBe('Motion')
      expect(['ms', 's'], `--duration-${d.name} has no time unit`).toContain(t!.unit)
      expect(toMs(`${t!.value}${t!.unit}`), `--duration-${d.name}: registry ${t!.value}${t!.unit} vs sheet ${d.value}`).toBe(toMs(d.value))
      expect(t!.min, `--duration-${d.name} can be dialled below zero`).toBeGreaterThanOrEqual(0)
    }
  })

  it('each is read: by a stylesheet, a component, or JS through its runtime key', () => {
    const corpus = [...STYLESHEETS, ...SOURCES].map((rel) => blankComments(read(rel), rel.endsWith('.css'))).join('\n')
    const motionModule = blankComments(read(MOTION_MODULE))
    for (const d of declarations()) {
      const key = registered(d.name)?.runtimeKey
      const readByCss = corpus.includes(`var(--duration-${d.name})`) || corpus.includes(`(--duration-${d.name})`)
      const readByJs = !!key && motionModule.includes(`runtime.${key}`)
      expect(readByCss || readByJs, `--duration-${d.name} is declared and read nowhere`).toBe(true)
    }
  })

  it('where JS reads one, duration.<name> is the same length, read at call time', () => {
    const mirrored = declarations().filter((d) => registered(d.name)?.runtimeKey)
    for (const step of STEPS) expect(mirrored.map((d) => d.name), `duration.${step} does not follow --duration-${step}`).toContain(step)
    for (const d of mirrored) {
      const t = registered(d.name)!
      const name = camel(d.name) as keyof typeof duration
      expect(Object.getOwnPropertyDescriptor(duration, name)?.get, `duration.${name} must be a getter over runtime`).toBeTypeOf('function')
      expect(duration[name] * 1000, `duration.${name} vs ${d.value}`).toBeCloseTo(toMs(d.value), 6)
      // A getter that cached its first answer would ignore the slider for the rest of the session.
      ;(runtime as unknown as Record<string, number>)[t.runtimeKey!] = t.value * 2
      expect(duration[name] * 1000).toBeCloseTo(toMs(d.value) * 2, 6)
    }
  })

  it('a fade is the effects step: spring.effects takes its length from --duration-effects', () => {
    expect((spring.effects as { duration?: number }).duration).toBe(duration.effects)
    runtime.durationEffects = 350
    expect((spring.effects as { duration?: number }).duration).toBe(0.35)
  })

  it('the reduced-motion answer: the sheet flattens every animation and transition', () => {
    const code = blankComments(read(SHEET), true)
    expect(code).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{\s*\*, \*::before, \*::after \{ animation-duration: 0\.001ms !important; transition-duration: 0\.001ms !important; \}/,
    )
  })
})

describe('the rail can fail', () => {
  it('scanned a real tree', () => {
    expect(STYLESHEETS, 'the main sheet moved').toContain(SHEET)
    expect(SOURCES.length, 'the corpus is empty, so this rail proves nothing').toBeGreaterThan(300)
  })

  it('a literal put back in the sheet is caught', () => {
    const sheet = read(SHEET)
    const planted = sheet.replace(/(\.skeleton \{[^}]*animation: skeleton-shimmer )var\(--duration-loading\)/, (_, head: string) => `${head}1.4s`)
    expect(planted, 'the plant did not land').not.toBe(sheet)
    expect(cssHits(SHEET, planted).map((h) => h.kind)).toEqual(['animation: 1.4s'])
  })

  it('a literal put back in an inline style, a class or a transition is caught', () => {
    const plants: [string, string, string, string][] = [
      ['pages/knowledge/KnowledgeGraph.tsx', "'transform var(--duration-effects) ease-out'", "'transform 200ms ease-out'", 'inline style length'],
      ['pages/chat/ChatActivityPanel.tsx', 'duration-(--duration-short)', 'duration-150', 'Tailwind length'],
      ['ui/WavyProgress.tsx', 'duration: duration.loading', 'duration: 1.4', 'framer length'],
      ['ui/widget/BlueprintSkeleton.tsx', 'calc(var(--duration-effects) * ${step})', '${step * 0.2}s', 'inline style length'],
    ]
    for (const [rel, token, literal, kind] of plants) {
      const src = read(rel)
      const planted = src.replace(token, literal)
      expect(planted, `the plant did not land in ${rel}`).not.toBe(src)
      expect(sourceHits(rel, src), `${rel} already carries a literal`).toEqual([])
      expect(sourceHits(rel, planted).map((h) => h.kind), rel).toEqual([kind])
    }
  })

  it('an instant, a token, a name and a comment are not lengths', () => {
    expect(cssHits('x.css', '* { animation-duration: 0.001ms !important; transition: opacity 0s; }')).toEqual([])
    expect(cssHits('x.css', '.a { animation: spin var(--duration-drift) linear calc(var(--duration-effects) * 2) infinite; }')).toEqual([])
    expect(cssHits('x.css', '/* was 160ms */ .a { transition: opacity var(--duration-effects) ease; }')).toEqual([])
    expect(cssHits('x.css', '.a { --duration-short: 100ms; view-transition-name: a2s; }')).toEqual([])
    expect(cssHits('x.css', '.a {\n  transition: opacity 160ms ease;\n}')).toEqual([
      { file: 'x.css', line: 2, kind: 'transition: 160ms', text: 'transition: opacity 160ms ease;' },
    ])
    expect(sourceHits('x.tsx', "className='transition-colors duration-0 motion-reduce:duration-0'")).toEqual([])
    expect(sourceHits('x.tsx', "className='transition-colors duration-(--duration-short)'")).toEqual([])
    expect(sourceHits('x.tsx', 'transition={{ duration: duration.medium, delay: duration.short }}')).toEqual([])
    expect(sourceHits('x.tsx', '// transition={{ duration: 0.15 }} used to be here')).toEqual([])
    expect(sourceHits('x.tsx', "style={{ animation: `demo ${v} ${ease || 'ease'} infinite` }}")).toEqual([])
    expect(sourceHits('x.tsx', 'transition={reduce ? { duration: 0 } : spring.spatialSlow}').map((h) => h.kind)).toEqual(['framer length'])
    expect(sourceHits('x.tsx', "className='animate-[spin_1s_linear_infinite]'").map((h) => h.kind)).toEqual(['Tailwind length'])
    expect(sourceHits('x.tsx', "className='delay-75 [transition:opacity_120ms]'").map((h) => h.kind)).toEqual(['Tailwind length'])
  })
})
