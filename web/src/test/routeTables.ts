import { dirname, join, resolve } from 'node:path'
import { withoutComments } from './domCensus'

// ── The app's surfaces, as its route tables name them ───────────────────────────────────────────
//
// A census that a shared helper is wired in everywhere is only as good as its list of
// "everywhere". A list written by hand misses the page added after it, so the censuses that need
// one derive it from the two tables the app itself routes by, read from source:
//
//   · `renderPage` in `app/App.tsx`, one `case '<route>': return <Page …/>` per top-level route;
//   · `SUBPAGES` in `pages/settings/SettingsPage.tsx`, one `{ id: '<id>', …, render: … <Panel …/> }`
//     per settings sub-page, routed as `settings/<id>`.
//
// Each entry's component is followed to the module it comes from (a named import, or a
// `lazy(() => import(…))`), and from there along the module's relative imports to everything the
// surface can reach. The tree is a value (`Tree`), so a census can be run over a planted one as
// well as the real one, which is read through `sourceTree.ts`. An import may be quoted either way,
// so a test can plant a tree without its imports reading as the test's own (`domCensus.ts` reads
// only the single-quoted ones this codebase writes).

export interface Tree {
  /** Every file in the tree (absolute paths). */
  readonly files: readonly string[]
  readonly read: (path: string) => string
}

/** One entry of a route table: the route, the component it renders, and that component's module. */
export interface RouteRoot {
  readonly route: string
  readonly component: string
  readonly module: string
}

const EXTENSIONS = ['', '.ts', '.tsx', '/index.ts', '/index.tsx']
const known = new WeakMap<Tree, Set<string>>()

/** A relative import resolved to a file of the tree, or null. */
function resolveSpec(tree: Tree, from: string, spec: string): string | null {
  if (!spec.startsWith('.')) return null
  let files = known.get(tree)
  if (!files) known.set(tree, (files = new Set(tree.files)))
  const base = resolve(dirname(from), spec)
  return EXTENSIONS.map((ext) => base + ext).find((candidate) => files.has(candidate)) ?? null
}

/** The module `name` comes from in `file`: a named static import, or a `lazy(() => import(…))`. */
function componentModule(tree: Tree, file: string, name: string): string | null {
  const code = withoutComments(tree.read(file))
  const lazy = code.match(new RegExp(`\\bconst\\s+${name}\\s*=\\s*lazy\\(\\s*\\(\\)\\s*=>\\s*import\\(\\s*['"]([^'"]+)['"]`))
  if (lazy) return resolveSpec(tree, file, lazy[1])
  for (const m of code.matchAll(/\bimport\s+\{([^}]*)\}\s+from\s+['"]([^'"]+)['"]/g)) {
    const names = m[1].split(',').map((n) => n.trim().replace(/^type\s+/, '').split(/\s+as\s+/).pop())
    if (names.includes(name)) return resolveSpec(tree, file, m[2])
  }
  return null
}

/** The text of a block that opens at `start` and closes at the matching bracket. */
function block(code: string, start: number): string {
  const open = code.slice(start).search(/[{[]/) + start
  const pair: Record<string, string> = { '{': '}', '[': ']' }
  const stack: string[] = []
  for (let i = open; i < code.length; i++) {
    const c = code[i]
    if (c === '{' || c === '[') stack.push(pair[c])
    else if (c === stack[stack.length - 1]) {
      stack.pop()
      if (!stack.length) return code.slice(open, i + 1)
    }
  }
  return code.slice(open)
}

/** Every entry of `renderPage` (in `app`) and of `SUBPAGES` (in `settings`), in table order. Each
 *  table must be found and read, or this throws: a census over a table it could not read would
 *  pass over nothing. */
export function routeRoots(tree: Tree, src: string): RouteRoot[] {
  const app = join(src, 'app', 'App.tsx')
  const settings = join(src, 'pages', 'settings', 'SettingsPage.tsx')
  const out: RouteRoot[] = []
  const add = (file: string, route: string, component: string) => {
    const module = componentModule(tree, file, component)
    if (!module) throw new Error(`${file}: the route ${route} renders ${component}, which is imported from no module here`)
    out.push({ route, component, module })
  }

  const appCode = withoutComments(tree.read(app))
  const at = appCode.indexOf('function renderPage(')
  if (at < 0) throw new Error(`${app}: no renderPage`)
  const cases = [...block(appCode, at).matchAll(/case\s+'([^']+)'\s*:\s*return\s+<(\w+)/g)]
  if (!cases.length) throw new Error(`${app}: renderPage has no cases`)
  for (const [, route, component] of cases) add(app, route, component)

  const settingsCode = withoutComments(tree.read(settings))
  const sub = settingsCode.search(/\bconst\s+SUBPAGES\b[^=]*=\s*\[/)
  if (sub < 0) throw new Error(`${settings}: no SUBPAGES`)
  const entries = [...block(settingsCode, settingsCode.indexOf('[', settingsCode.indexOf('=', sub))).matchAll(/\{\s*id:\s*'([^']+)'[^\n]*?render:[^\n]*?<(\w+)/g)]
  if (!entries.length) throw new Error(`${settings}: SUBPAGES has no entries`)
  for (const [, id, component] of entries) add(settings, `settings/${id}`, component)
  return out
}

const IMPORT = /\b(?:import|export)\s+(type\s+)?(?:[^'";]*?\sfrom\s+)?['"]([^'"]+)['"]|\bimport\(\s*['"]([^'"]+)['"]\s*\)/g

/** Each module's relative imports, resolved within the tree (type-only imports reach nothing). */
export function importGraph(tree: Tree): Map<string, string[]> {
  const graph = new Map<string, string[]>()
  for (const file of tree.files) {
    if (!/\.tsx?$/.test(file) || /\.test\.tsx?$/.test(file)) continue
    const deps: string[] = []
    for (const m of withoutComments(tree.read(file)).matchAll(IMPORT)) {
      const dep = m[1] ? null : resolveSpec(tree, file, m[2] ?? m[3])
      if (dep) deps.push(dep)
    }
    graph.set(file, deps)
  }
  return graph
}

/** Everything `from` reaches along the import graph, itself included, with how many imports away. */
export function reach(graph: Map<string, string[]>, from: string): Map<string, number> {
  const seen = new Map([[from, 0]])
  const queue = [from]
  for (let i = 0; i < queue.length; i++) {
    const depth = seen.get(queue[i])! + 1
    for (const dep of graph.get(queue[i]) ?? []) {
      if (seen.has(dep)) continue
      seen.set(dep, depth)
      queue.push(dep)
    }
  }
  return seen
}

/** The route that reaches `module` in the fewest imports (a sub-page over the page that holds it),
 *  the first in table order on a tie, or null when no route reaches it. */
export function nearestRoute(routes: ReadonlyArray<{ route: string; reached: Map<string, number> }>, module: string): string | null {
  let best: { route: string; depth: number } | null = null
  for (const { route, reached } of routes) {
    const depth = reached.get(module)
    if (depth !== undefined && (!best || depth < best.depth)) best = { route, depth }
  }
  return best?.route ?? null
}
