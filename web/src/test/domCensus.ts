import { dirname, join, relative, resolve, sep } from 'node:path'
import lists from './domCensus.json'
import { filesUnder, readSource } from './sourceTree'

// ── Which test files run without a DOM ──────────────────────────────────────────────────────────
//
// Vitest gives a test file the environment its project names, and building a jsdom window for a
// file is most of what a file that never touches one pays for. So a `.test.ts` file runs in the
// `node` project when neither it nor any module it imports, transitively within `src/`, reaches
// the DOM, and in the `dom` project (jsdom) otherwise. Every `.test.tsx` file renders, and stays
// in `dom` whatever it imports.
//
// "Reaches the DOM" is read from the source, with comments blanked:
//
// * a DOM global named as a word (`window`, `document`, `navigator`, storage, the observers,
//   `matchMedia`, element and event classes, animation frames), including inside `typeof`: code
//   that feature-detects the DOM would quietly take its no-DOM branch under node and still pass,
//   so a check is counted as use;
// * an import of a package that is not on the short list below. A package can reach the DOM
//   anywhere inside it, and following its code is not this census's business, so a package is
//   DOM until it is listed here, and listing one is a reviewed change;
// * an import of a component module (`.tsx`) or an asset (a stylesheet, `?raw`, an image);
// * an environment pragma other than `node` anywhere in the test file's text, which Vitest obeys
//   wherever it finds it (see `ENV_PRAGMA`).
//
// A word inside a string counts too (`kind: 'document'`): that keeps such a file in `dom`, which
// is only slower, never wrong. What the census cannot see is a DOM global reached by a computed
// name (`globalThis['win' + 'dow']`); no module here does that.
//
// `vitest.config.ts` reads this module to build its two projects, and `domCensus.test.ts` plants
// the shapes it must catch. It reads the tree through `sourceTree.ts`, the suite's one walker.

// The three lists live in `domCensus.json`, as data: written here, the globals' names would make
// this module, and every test file that imports it, a module that names the DOM.

/** Global names that exist in a browser and not in Node, and are no ordinary word in this code:
 *  naming one anywhere counts. */
export const DOM_GLOBALS: readonly string[] = lists.domGlobals

/** Browser globals whose names are also ordinary words here (`prompt` is a model's prompt,
 *  `history` a chat's). Using one Node lacks throws a ReferenceError, which fails the file under
 *  node and shows in the comparison each move is checked by; what would pass unseen is a check for
 *  one, so only a check counts: `typeof prompt`, `globalThis.prompt`, `'prompt' in …`. */
export const DOM_GLOBALS_CHECKED_ONLY: readonly string[] = lists.domGlobalsCheckedOnly

/** Packages a `node` file may import. Each one is DOM-free in what this suite imports from it. */
export const NODE_SAFE_PACKAGES: readonly string[] = lists.nodeSafePackages

const DOM_WORD = new RegExp(`(?<![\\w$.])(?:${DOM_GLOBALS.join('|')})(?![\\w$])`)
const CHECKED = [...DOM_GLOBALS, ...DOM_GLOBALS_CHECKED_ONLY].join('|')
const DOM_CHECK = new RegExp(
  `\\btypeof\\s+(?:${CHECKED})(?![\\w$])|\\bglobalThis\\s*(?:\\?\\.|\\.)\\s*(?:${CHECKED})(?![\\w$])|['"](?:${CHECKED})['"]\\s+in\\b`,
)
// The environment pragma exactly as Vitest reads it: the FIRST match anywhere in a test file's
// text, comment or string alike, and it overrides the project's environment. So a file whose text
// names any environment other than node anywhere is left in `dom`; the census cannot promise what
// such a file will run in.
const ENV_PRAGMA = /@(?:vitest|jest)-environment\s+([\w-]+)\b/
const IMPORT =
  /\b(import|export)\s+(type\s+)?(?:[^'";]*?\sfrom\s+)?'([^']+)'|\bimport\(\s*'([^']+)'\s*\)|\brequire\(\s*'([^']+)'\s*\)/g
const ASSET = /\.(css|svg|png|jpe?g|gif|webp|woff2?|ttf|html|md|txt)$/

/** Comments blanked, keeping line breaks, so a sentence ABOUT `window` is not a use of it. */
export function withoutComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:\\'"`])\/\/[^\n]*/g, '$1')
}

/** What one module's own text says: the DOM word it names (or null), and what it imports at run
 *  time (a type-only import is erased, so it reaches nothing). */
export function readModule(text: string): { domWord: string | null; imports: string[] } {
  const code = withoutComments(text)
  const word = DOM_WORD.exec(code) ?? DOM_CHECK.exec(code)
  const imports: string[] = []
  for (const m of code.matchAll(IMPORT)) {
    if (m[2]) continue
    const spec = m[3] ?? m[4] ?? m[5]
    if (spec) imports.push(spec)
  }
  return { domWord: word ? word[0] : null, imports }
}

function isNodeSafePackage(spec: string): boolean {
  if (spec.startsWith('node:')) return true
  return NODE_SAFE_PACKAGES.some((name) => spec === name || spec.startsWith(`${name}/`))
}

const EXTENSIONS = ['', '.ts', '.tsx', '/index.ts', '/index.tsx']

/** Judges test files against a tree: `exists` says whether a path is a file in it, `read` gives
 *  its text. Each module is read once, and its verdict kept. */
export class DomCensus {
  /** Why a module reaches the DOM (its own reason or an import's), or null when it does not. */
  private readonly verdicts = new Map<string, string | null>()

  constructor(
    private readonly exists: (path: string) => boolean,
    private readonly read: (path: string) => string,
  ) {}

  /** Null when `path` (an absolute `.test.ts` path) may run without a DOM, else why not. */
  judge(path: string): string | null {
    if (!path.endsWith('.ts')) return `${path}: renders (.tsx)`
    const pragma = ENV_PRAGMA.exec(this.read(path))?.[1]
    if (pragma && pragma !== 'node') return `${path}: asks for ${pragma}`
    return this.reaches(path, [])
  }

  /** What a module itself does that needs a DOM, and the modules of `src` it imports. */
  private own(path: string): { reason: string | null; deps: string[] } {
    if (path.endsWith('.tsx')) return { reason: `${path}: a component module (.tsx)`, deps: [] }
    const { domWord, imports } = readModule(this.read(path))
    if (domWord) return { reason: `${path}: names \`${domWord}\``, deps: [] }
    const deps: string[] = []
    for (const spec of imports) {
      if (!spec.startsWith('.')) {
        if (!isNodeSafePackage(spec)) return { reason: `${path}: imports the package ${spec}`, deps }
        continue
      }
      if (spec.includes('?') || ASSET.test(spec)) return { reason: `${path}: imports an asset (${spec})`, deps }
      const base = resolve(dirname(path), spec)
      if (base.endsWith('.json')) continue
      const hit = EXTENSIONS.map((ext) => base + ext).find((candidate) => this.exists(candidate))
      if (!hit) return { reason: `${path}: imports ${spec}, which is no module here`, deps }
      deps.push(hit)
    }
    return { reason: null, deps }
  }

  /** Depth first over the imports. A module met again while it is still being judged (an import
   *  cycle) adds nothing new on that path: what it reaches is judged where the walk first met it.
   *  A clean verdict is kept only once the walk that reached it has finished, so a module judged
   *  clean part-way round a cycle is judged again by the next test that imports it. */
  private reaches(path: string, stack: string[]): string | null {
    if (this.verdicts.has(path)) return this.verdicts.get(path) ?? null
    if (stack.includes(path)) return null
    const { reason, deps } = this.own(path)
    let verdict = reason
    if (verdict === null) {
      stack.push(path)
      for (const dep of deps) {
        verdict = this.reaches(dep, stack)
        if (verdict !== null) break
      }
      stack.pop()
    }
    if (verdict !== null || stack.length === 0) this.verdicts.set(path, verdict)
    return verdict
  }
}

/** The `.test.ts` files under `web/src` that run in `node` (paths relative to `web`,
 *  `/`-separated), and every other test file with the reason it stays in `dom`. `files` is every
 *  file under `web/src`, as `filesUnder` lists them (the config passes the run's index). */
export function census(
  web: string,
  files: readonly string[] = filesUnder(join(web, 'src')),
): { node: string[]; dom: Map<string, string> } {
  const known = new Set(files)
  const judge = new DomCensus((path) => known.has(path), readSource)
  const node: string[] = []
  const dom = new Map<string, string>()
  for (const path of files) {
    if (!/\.test\.tsx?$/.test(path)) continue
    const rel = relative(web, path).split(sep).join('/')
    const reason = judge.judge(path)
    if (reason === null) node.push(rel)
    else dom.set(rel, reason.split(web + sep).join(''))
  }
  return { node, dom }
}
