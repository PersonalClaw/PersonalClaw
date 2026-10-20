// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join, relative, sep } from 'node:path'
import { census, DOM_GLOBALS, DOM_GLOBALS_CHECKED_ONLY, DomCensus } from './domCensus'

// ── The census that decides which test files run without a DOM ──────────────────────────────────
//
// `vitest.config.ts` runs the files this census names in the `node` project, which has no DOM. A
// file it puts there wrongly fails loudly when it uses the DOM, but passes quietly when the code
// under it only CHECKS for one and takes its no-DOM branch, so the plants below are mostly checks.
//
// The census reads this file too, and this file must run in the `node` project, where its last
// test proves that project has no DOM. So nothing here is written in a shape the census reads: each
// DOM global is spelled from the census's own list, each planted import from `IMPORT` below, and
// the environment pragma from `PRAGMA`.

const WEB = process.cwd()
const SELF = 'src/test/domCensus.test.ts'

const [WINDOW, DOCUMENT] = DOM_GLOBALS
const PROMPT = DOM_GLOBALS_CHECKED_ONLY.find((name) => name.startsWith('pro')) ?? ''
const IMPORT = ['im', 'port'].join('')
const EXPORT = ['ex', 'port'].join('')
/** Vitest's environment pragma, spelled in pieces: written whole anywhere in this file, Vitest
 *  would read it as this file's own and run it in that environment. */
const PRAGMA = ['@vitest', 'environment'].join('-')

/** A planted import line: `IMPORT what from 'spec'`, or a bare `IMPORT 'spec'`. */
const imp = (spec: string, what = '', keyword = IMPORT) =>
  what ? `${keyword} ${what} from '${spec}'` : `${keyword} '${spec}'`

/** A census over an in-memory tree under `/t`, judging `/t/a.test.ts`. */
function judge(files: Record<string, string>, test = '/t/a.test.ts'): string | null {
  const tree = new Map(Object.entries(files))
  return new DomCensus((path) => tree.has(path), (path) => tree.get(path) ?? '').judge(test)
}

describe('a test file is judged by what it and its imports reach', () => {
  it('a file that uses no DOM and imports nothing that does runs in node', () => {
    const files = {
      '/t/a.test.ts': `${imp('vitest', '{ it }')}\n${imp('./f', '{ f }')}\nit('x', () => f())`,
      '/t/f.ts': `${EXPORT} const f = () => 1`,
    }
    expect(judge(files)).toBeNull()
  })

  it('a file that uses a DOM global stays in dom', () => {
    expect(judge({ '/t/a.test.ts': `const el = ${DOCUMENT}.createElement('div')` })).toMatch(/names `/)
  })

  it('a file whose import only feature-detects the DOM stays in dom, however deep', () => {
    const files = {
      '/t/a.test.ts': imp('./g', '{ g }'),
      '/t/g.ts': imp('./f', '{ f as g }', EXPORT),
      '/t/f.ts': `${EXPORT} const f = () => typeof ${WINDOW} !== 'undefined' ? 'browser' : 'server'`,
    }
    expect(judge(files)).toMatch(/\/t\/f\.ts: names `/)
  })

  it('a word that is also a browser global counts only as a check', () => {
    expect(PROMPT).not.toBe('')
    expect(judge({ '/t/a.test.ts': `const ${PROMPT} = 'Summarise this'` }), 'an ordinary variable').toBeNull()
    expect(judge({ '/t/a.test.ts': `const has = typeof ${PROMPT} === 'function'` })).toMatch(/names `typeof/)
    expect(judge({ '/t/a.test.ts': `const has = globalThis.${PROMPT} !== undefined` })).toMatch(/names `globalThis/)
    expect(judge({ '/t/a.test.ts': `const has = '${PROMPT}' in globalThis` })).toMatch(/names `/)
  })

  it('a sentence about the DOM is not a use of it, and an erased type import reaches nothing', () => {
    expect(judge({ '/t/a.test.ts': `// runs without a ${WINDOW}\n/* or a ${DOCUMENT} */\n${EXPORT} {}` })).toBeNull()
    const typeOnly = {
      '/t/a.test.ts': imp('./v', 'type { V }'),
      '/t/v.ts': `${EXPORT} type V = typeof ${WINDOW}`,
    }
    expect(judge(typeOnly)).toBeNull()
  })

  it('a package, a component, an asset or a jsdom pragma keeps a file in dom', () => {
    expect(judge({ '/t/a.test.ts': imp('lucide-react', '{ X }') })).toMatch(/the package lucide-react/)
    expect(judge({ '/t/a.test.ts': `${imp('node:fs', '{ readFileSync }')}\n${imp('typescript', 'ts')}` })).toBeNull()
    expect(judge({ '/t/a.test.ts': imp('./B', '{ B }'), '/t/B.tsx': `${EXPORT} const B = 1` })).toMatch(/component module/)
    expect(judge({ '/t/a.test.ts': imp('./s.css?raw', 'css') })).toMatch(/an asset/)
    // Vitest reads the pragma anywhere in a file's text, a string included, so the census does too.
    expect(judge({ '/t/a.test.ts': `// ${PRAGMA} jsdom\n${EXPORT} {}` })).toMatch(/asks for jsdom/)
    expect(judge({ '/t/a.test.ts': `const s = 'mentions ${PRAGMA} happy-dom'` }), 'a mention is a pragma').toMatch(/asks for happy-dom/)
    expect(judge({ '/t/a.test.ts': `// ${PRAGMA} node\n${EXPORT} {}` }), 'asking for node keeps node').toBeNull()
    expect(judge({ '/t/a.test.ts': imp('./gone', '{ gone }') }), 'an import that resolves to nothing').toMatch(/no module here/)
    expect(judge({ '/t/a.test.tsx': '' }, '/t/a.test.tsx')).toMatch(/renders/)
  })

  it('an import cycle cannot hide a DOM module behind it, from either end', () => {
    const files = {
      '/t/a.test.ts': `${imp('./p')}\n${imp('./q')}`,
      '/t/p.ts': imp('./q'),
      '/t/q.ts': `${imp('./p')}\n${imp('./r')}`,
      '/t/r.ts': `${WINDOW}.x = 1`,
      '/t/b.test.ts': imp('./q'),
    }
    const tree = new Map(Object.entries(files))
    const one = new DomCensus((path) => tree.has(path), (path) => tree.get(path) ?? '')
    expect(one.judge('/t/a.test.ts')).toMatch(/r\.ts/)
    expect(one.judge('/t/b.test.ts')).toMatch(/r\.ts/)
    const other = new DomCensus((path) => tree.has(path), (path) => tree.get(path) ?? '')
    expect(other.judge('/t/b.test.ts')).toMatch(/r\.ts/)
    expect(other.judge('/t/a.test.ts')).toMatch(/r\.ts/)
  })
})

describe('the census over this tree', () => {
  const { node, dom } = census(WEB)

  it('moves the DOM-free .test.ts files, and only .test.ts files', () => {
    // The floor: a census that moved almost nothing would pass every check below.
    expect(node.length, 'almost no file runs in node: the census broke').toBeGreaterThan(200)
    expect(node.every((path) => path.endsWith('.test.ts'))).toBe(true)
    expect(node, dom.get(SELF) ?? '').toContain(SELF)
    expect([...dom.keys()].filter((path) => path.endsWith('.test.tsx')).length).toBeGreaterThan(900)
    // A file that asks for jsdom keeps it, and every file is in exactly one project.
    expect(dom.get('src/app/appSdkAgentTier.test.ts') ?? '').toMatch(/asks for jsdom/)
    expect(dom.get('src/design/reducedMotionAppWide.test.ts') ?? '').toMatch(/names `/)
    expect(node.filter((path) => dom.has(path))).toEqual([])
    expect(node.map((path) => relative(WEB, join(WEB, path)).split(sep).join('/'))).toEqual(node)
  })

  it('and the node project it builds has no DOM', () => {
    // This file is in the node project (above), so these are that project's globals.
    const scope = globalThis as Record<string, unknown>
    expect(scope[WINDOW], `the node project has a global ${WINDOW}`).toBeUndefined()
    expect(scope[DOCUMENT], `the node project has a global ${DOCUMENT}`).toBeUndefined()
  })
})
