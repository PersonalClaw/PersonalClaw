// @module-tag tree-scan
/**
 * Three keyframes never ride a spring.
 *
 * Motion springs between two values only. Handed `scale: [1, 1.18, 1]` on a spring it throws
 * ("Only two keyframes currently supported with spring and inertia animations"), so the moment
 * never plays at all. The composer's send→check bloom did exactly that from the day
 * `physics.playful` became a spring until it moved to a tween, and the only trace was an
 * unhandled rejection in whichever test happened to render a sent message.
 *
 * So this rail holds every motion site in the tree to one rule: a target with three or more
 * keyframes (`animate`, `exit`, a `while…` gesture, or a variant's `animate:`) takes a tween
 * (`duration` plus `ease`), never a `physics` preset, a `spring.spatial…` tier, `dragSpring()` or
 * `type: 'spring'`. Two keyframes on a spring are fine, and so is `spring.effects`, which is a
 * tween.
 *
 * It is a TEXT scan over source with comments blanked. It judges what each site names: a
 * transition held in a variable and named elsewhere is not followed.
 */

import { join } from 'node:path'

import { describe, expect, it } from 'vitest'
import { filesUnder, readSource } from '../test/sourceTree'

const SRC = join(process.cwd(), 'src')

/** Blank every comment, keeping line structure, so prose about these shapes is not counted. The
 *  `[^:]` guard keeps `https://` from reading as a line comment. */
function blankComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

const CLOSE: Record<string, string> = { '{': '}', '[': ']', '(': ')' }
const QUOTES = new Set(["'", '"', '`'])

/** The index just past the quote that closes the string opening at `at`. */
function pastString(src: string, at: number): number {
  const quote = src[at]
  let i = at + 1
  while (i < src.length && src[i] !== quote) i += src[i] === '\\' ? 2 : 1
  return i + 1
}

/** The bracketed expression opening at `src[at]`, brackets included. */
function balanced(src: string, at: number): string {
  const stack: string[] = []
  let i = at
  while (i < src.length) {
    const c = src[i]
    if (QUOTES.has(c)) { i = pastString(src, i); continue }
    if (CLOSE[c]) stack.push(CLOSE[c])
    else if (c === stack[stack.length - 1]) {
      stack.pop()
      if (stack.length === 0) return src.slice(at, i + 1)
    }
    i++
  }
  return src.slice(at)
}

/** A property's value starting at `at`: up to the first `,` or closing bracket at depth 0. */
function propertyValue(src: string, at: number): string {
  let depth = 0
  let i = at
  while (i < src.length) {
    const c = src[i]
    if (QUOTES.has(c)) { i = pastString(src, i); continue }
    if (CLOSE[c]) depth++
    else if (c === '}' || c === ']' || c === ')') { if (depth === 0) break; depth-- }
    else if (c === ',' && depth === 0) break
    i++
  }
  return src.slice(at, i)
}

/** The JSX opening tag around `at`: from the nearest `<Name` before it to the `>` that closes it. */
function enclosingTag(src: string, at: number): string {
  let start = at
  while (start > 0 && !(src[start] === '<' && /[A-Za-z]/.test(src[start + 1] ?? ''))) start--
  let depth = 0
  let i = start + 1
  while (i < src.length) {
    const c = src[i]
    if (QUOTES.has(c)) { i = pastString(src, i); continue }
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0 && src[i - 1] !== '=') return src.slice(start, i + 1)
    i++
  }
  return src.slice(start)
}

/** Each `transition: …` written inside `expr`, and `expr` with those values cut out. */
function innerTransitions(expr: string): { transitions: string[]; rest: string } {
  const transitions: string[] = []
  let rest = expr
  for (const m of [...expr.matchAll(/\btransition\s*:\s*/g)].reverse()) {
    const value = propertyValue(expr, m.index + m[0].length)
    transitions.push(value)
    rest = rest.slice(0, m.index) + rest.slice(m.index + m[0].length + value.length)
  }
  return { transitions, rest }
}

/** The number of elements in the array literal `expr` (brackets included). */
function elements(expr: string): number {
  const inner = expr.slice(1, -1)
  if (inner.trim() === '') return 0
  let count = 1
  let depth = 0
  for (let i = 0; i < inner.length; i++) {
    const c = inner[i]
    if (QUOTES.has(c)) { i = pastString(inner, i) - 1; continue }
    if (CLOSE[c]) depth++
    else if (c === '}' || c === ']' || c === ')') depth--
    else if (c === ',' && depth === 0 && inner.slice(i + 1).trim() !== '') count++
  }
  return count
}

/** True when `expr` holds an array literal of three or more keyframes, at any depth. */
function hasThreeKeyframes(expr: string): boolean {
  for (let i = 0; i < expr.length; i++) {
    if (QUOTES.has(expr[i])) { i = pastString(expr, i) - 1; continue }
    if (expr[i] === '[' && elements(balanced(expr, i)) >= 3) return true
  }
  return false
}

interface Property { key: string; value: string; from: number; to: number }

/** Each `key: value` written in `expr`, at any depth, with where its value sits. */
function properties(expr: string): Property[] {
  return [...expr.matchAll(/\b([A-Za-z_$][\w$]*)\s*:\s*/g)].map((m) => {
    const from = m.index + m[0].length
    const value = propertyValue(expr, from)
    return { key: m[1], value, from, to: from + value.length }
  })
}

/** `expr` with the values of the properties named in `keys` cut out. */
function withoutKeys(expr: string, keys: Set<string>): string {
  let out = ''
  let at = 0
  for (const p of properties(expr)) {
    if (!keys.has(p.key) || p.from < at) continue
    out += expr.slice(at, p.from)
    at = p.to
  }
  return out + expr.slice(at)
}

const SPRING = /\bphysics\.\w+|\bspring\.spatial\w*|\bdragSpring\(|\btype\s*:\s*['"]spring['"]/

/** True when a property of `target` with three or more keyframes rides a spring in one of
 *  `transitions`: its own per-property transition when one names it, else the transition's
 *  default (the transition with every other animated property's own value cut out). */
function springsKeyframes(target: string, transitions: string[]): boolean {
  const props = properties(target)
  const animated = new Set(props.map((p) => p.key))
  return props.filter((p) => hasThreeKeyframes(p.value)).some(({ key }) => transitions.some((t) => {
    const own = properties(t).find((p) => p.key === key)
    return SPRING.test(own ? own.value : withoutKeys(t, animated))
  }))
}

const TARGET = 'animate|exit|whileHover|whileTap|whileFocus|whileInView|whileDrag'
const JSX_TARGET = new RegExp(`\\b(?:${TARGET})=\\{`, 'g')
const OBJECT_TARGET = new RegExp(`\\b(?:${TARGET})\\s*:\\s*`, 'g')

interface Site { at: number; target: string; transitions: string[] }

/** Every motion target in `code` (comments blanked), with the transitions that drive it. */
function sites(code: string): Site[] {
  const out: Site[] = []
  for (const m of code.matchAll(JSX_TARGET)) {
    const { transitions, rest } = innerTransitions(balanced(code, m.index + m[0].length - 1))
    const tag = enclosingTag(code, m.index)
    const prop = /\btransition=\{/.exec(tag)
    if (prop) transitions.push(balanced(tag, prop.index + prop[0].length - 1))
    out.push({ at: m.index, target: rest, transitions })
  }
  for (const m of code.matchAll(OBJECT_TARGET)) {
    const { transitions, rest } = innerTransitions(propertyValue(code, m.index + m[0].length))
    out.push({ at: m.index, target: rest, transitions })
  }
  return out
}

interface Hit { line: number; text: string }

/** Every site in `src` that hands three keyframes to a spring. */
function findings(src: string): Hit[] {
  const code = blankComments(src)
  const raw = src.split('\n')
  const lines = sites(code)
    .filter((s) => springsKeyframes(s.target, s.transitions))
    .map((s) => code.slice(0, s.at).split('\n').length)
  return [...new Set(lines)].map((line) => ({ line, text: raw[line - 1].trim() }))
}

/** How many motion sites in `src` animate three or more keyframes, whatever they ride on. */
function keyframeSites(src: string): number {
  return sites(blankComments(src)).filter((s) => properties(s.target).some((p) => hasThreeKeyframes(p.value))).length
}

const SOURCES = filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name))
  .map((path) => path.slice(SRC.length + 1).split('\\').join('/'))
  .filter((rel) => !rel.startsWith('test/'))
  .sort()

const read = (rel: string) => readSource(join(SRC, rel))

describe('three keyframes never ride a spring', () => {
  it('no motion site hands three or more keyframes to a spring', () => {
    const offenders = SOURCES.flatMap((rel) => findings(read(rel)).map((h) => `${rel}:${h.line}  ${h.text}`))
    expect(offenders, [
      'three or more keyframes on a spring: motion throws, and the animation never plays:',
      ...offenders.map((o) => `  ${o}`),
      'Give these a tween ({ duration: duration.long, ease: … }), or animate two values on the spring.',
    ].join('\n')).toEqual([])
  })
})

describe('the rail can fail', () => {
  it('scanned a real tree, and found the keyframe sites it judges', () => {
    expect(SOURCES.length, 'the corpus is empty, so this rail proves nothing').toBeGreaterThan(300)
    // Each of these animates three keyframes on a tween. If the scan stops seeing them, it would
    // stop seeing a spring next to them too.
    for (const rel of ['ui/IconButton.tsx', 'ui/SpotlightTour.tsx', 'ui/dialog/DialogShell.tsx', 'ui/ClawMark.tsx', 'design/motion.ts']) {
      expect(SOURCES, `${rel} moved or was renamed`).toContain(rel)
      expect(keyframeSites(read(rel)), `${rel} has no keyframe site the scan can see`).toBeGreaterThan(0)
    }
  })

  it("the bloom's old shape is caught where it shipped", () => {
    const rel = 'ui/IconButton.tsx'
    const planted = read(rel).replace(/transition=\{bloom \? \([^\n]*\) : undefined\}/, 'transition={bloom ? physics.playful : undefined}')
    expect(planted, 'the plant did not land').not.toBe(read(rel))
    expect(findings(read(rel))).toEqual([])
    expect(findings(planted).map((h) => h.text)).toEqual([expect.stringContaining('scale: [1, 1.18, 1]')])
  })

  it('catches each way a spring reaches three keyframes', () => {
    const caught = [
      '<motion.span animate={{ y: [0, -4, 0] }} transition={spring.spatialFast} />',
      '<motion.span animate={{ y: [0, -4, 0], transition: physics.snappy }} />',
      '<motion.div className="a" animate={on ? { opacity: [0, 1, 0.5] } : {}}\n  transition={{ ...physics.smooth, delay: 0.1 }} />',
      "<motion.div whileTap={{ scale: [1, 0.9, 1] }} transition={{ type: 'spring', stiffness: 300 }} />",
      'export const pop: Variants = { animate: () => ({ scale: [1, 1.1, 1], transition: physics.playful }) }',
      '<motion.div drag animate={{ x: [0, 8, -8, 0] }} transition={dragSpring()} />',
      '<motion.div animate={{ opacity: [0.7, 1, 0.7], borderRadius: 8 }} transition={{ opacity: physics.smooth, borderRadius: { duration: 0.2 } }} />',
    ]
    for (const src of caught) expect(findings(src), src).toHaveLength(1)
  })

  it('passes two keyframes on a spring, three on a tween, and a shape named in a comment', () => {
    const passed = [
      '<motion.span initial={{ scale: 0.9 }} animate={{ scale: [0.9, 1] }} transition={physics.playful} />',
      "<motion.span animate={{ scale: [0.6, 1.18, 1] }} transition={{ duration: duration.long, ease: 'easeOut' }} />",
      '<motion.span animate={{ opacity: [0.4, 1, 0.4] }} transition={{ duration: duration.beat, times: [0, 0.5, 1], ease: ease.emphasized }} />',
      '<motion.span animate={{ opacity: [0.4, 1, 0.4] }} transition={spring.effects} />',
      "<motion.span animate={{ opacity: 1, transition: { times: [0, 0.5, 1] } }} transition={physics.smooth} />",
      '// it used to be animate={{ scale: [1, 1.18, 1] }} transition={physics.playful}\nconst a = 1',
      '<motion.span animate={{ x: rows[i] }} transition={physics.fluid} />',
      // The composer ring's shape: the pulsing opacity has a tween of its own, and only the radius springs.
      "<motion.div animate={{ opacity: on ? [0.7, 1, 0.7] : 0, borderRadius: r }}\n  transition={on ? { opacity: { duration: duration.beat, ease: 'easeInOut' }, borderRadius: spring.spatialDefault } : spring.spatialDefault} />",
      '<motion.div animate={{ opacity: [0.7, 1, 0.7], borderRadius: r }} transition={{ borderRadius: spring.spatialDefault, duration: duration.beat }} />',
    ]
    for (const src of passed) expect(findings(src), src).toEqual([])
  })
})
