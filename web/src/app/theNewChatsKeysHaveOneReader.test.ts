// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import ts from 'typescript'
import { filesUnder, readSource } from '../test/sourceTree'
import { bare, bindingOf, heldBy, nodesOf, spelledOf, stringsOf, syntaxOf, textOf } from '../test/sourceSyntax'
import { chatLaunchPath, readChatLaunch } from './linkState'

// ── A chat's launch keys have one reader, and none of them sends ──────────────────────────────────
//
// The keys a chat's address names for it to start from (`seed`, `agent`, `widget_action`,
// `project`, `from_widget`, and `attach`, the library items a link attaches) were read and written
// where each was first needed: the shell wrote three in its launch handler, the chat page read four
// from the query, and seven other places wrote a chat's address by hand (a missing chat's way on, a
// suggestion, a loop's Build with chat, a project's Chat, a project row's Start a chat here, the
// palette's project rows, the apps' chat embed). They are read and
// written in `linkState.ts` alone now, and a scan of the whole tree holds every page to it. A
// nonce reaches a chat's address only as the staged turn's own (`widgetBridgeSinglePath`).
//
// The scan reads each file's syntax tree (`test/sourceSyntax.ts`), not its text: a comment is no
// node, a string is one token whatever it holds, and a name is the query's by the scope that binds
// it, so no shape of a read hides behind a `'src/*.ts'`, a `"a//b"` or a callback that has closed.

const SRC = join(import.meta.dirname, '..')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** Every source file in the web tree, listed live, as the tree rails list it. */
const SOURCES = filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.(test|doc)\.tsx?$/.test(name))
  .map((path) => ({ rel: relative(SRC, path), text: readSource(path) }))

const KEYS = ['seed', 'agent', 'widget_action', 'project', 'attach', 'from_widget']
/** The keys no page of its own uses: a `.set` or `.append` of one, or a `URLSearchParams` built with
 *  one, writes a chat's address. */
const CHAT_ONLY = ['seed', 'widget_action', 'attach', 'from_widget']
/** A chat's address with a launch key, in the words a string makes: `chat/new?seed=`, `#/chat?project=`,
 *  `chat/${…}?attach=`. */
const CHAT_ADDRESS = new RegExp(`(?:^|[/#])chat(?:/[^?\\s]*)?\\?.*\\b(?:${KEYS.join('|')})=`)
/** A query that starts with one of the chat's own keys (`?seed=`, `&attach=`), whatever address it is
 *  joined to: a `search: '?seed=' + s` beside a chat's pathname writes the chat's address too. */
const CHAT_QUERY = new RegExp(`^[?&](?:${CHAT_ONLY.join('|')})=`)
/** A query string with one of the chat's own keys (`seed=${s}`), as a `URLSearchParams` is built from. */
const CHAT_PAIRS = new RegExp(`(?:^|[?&])(?:${CHAT_ONLY.join('|')})=`)
const ITEM_CALLBACKS = new Set(['map', 'filter', 'forEach', 'some', 'every', 'find', 'findIndex', 'findLast', 'findLastIndex', 'flatMap', 'reduce'])

/** A property's or binding's name as written (`seed`, `'seed'`), or a computed one the source spells
 *  out (`[K]` with `const K = 'seed'`). null when it is computed from a value. */
function nameOf(node: ts.PropertyName | ts.Expression | undefined): string | null {
  if (!node) return null
  if (ts.isIdentifier(node) || ts.isStringLiteralLike(node) || ts.isNumericLiteral(node)) return node.text
  if (ts.isComputedPropertyName(node)) return spelledOf(node.expression)
  return ts.isPrivateIdentifier(node) ? null : spelledOf(node as ts.Expression)
}

/** Whether `param` is the parameter of a callback an array hands each item to (`rows.map((q) => …)`):
 *  there the name is the item, a queued message or a row, not the query. */
function isItemParameter(param: ts.Node): boolean {
  if (!ts.isParameter(param)) return false
  const fn = param.parent
  if (!ts.isArrowFunction(fn) && !ts.isFunctionExpression(fn)) return false
  const call = fn.parent
  return ts.isCallExpression(call) && call.arguments.includes(fn) && ts.isPropertyAccessExpression(call.expression)
    && ITEM_CALLBACKS.has(call.expression.name.text)
}

/** Whether the pattern `p` binds `name` to the query: as its `query` (`{ query: r }`, at any depth), or
 *  as the rest of a pattern that takes the query apart (`const { ...rest } = query`). */
function patternBindsQuery(p: ts.BindingPattern, name: string, fromQuery: boolean): boolean {
  for (const el of p.elements) {
    if (ts.isOmittedExpression(el)) continue
    const key = el.propertyName ? nameOf(el.propertyName) : ts.isIdentifier(el.name) ? el.name.text : null
    if (ts.isIdentifier(el.name)) {
      if (el.name.text === name) return (fromQuery && !!el.dotDotDotToken && ts.isObjectBindingPattern(p)) || (!el.dotDotDotToken && key === 'query')
    } else if (patternBindsQuery(el.name, name, key === 'query')) return true
  }
  return false
}

/** Each assignment in `sf` (not a declaration) to a name bound by `bound`: `r = query` after `let r`. */
const assigned = new WeakMap<ts.SourceFile, ts.BinaryExpression[]>()
function assignmentsTo(id: ts.Identifier, bound: ts.Node): ts.Expression[] {
  const sf = id.getSourceFile()
  if (!assigned.has(sf)) assigned.set(sf, nodesOf(sf).filter((n): n is ts.BinaryExpression => ts.isBinaryExpression(n)
    && n.operatorToken.kind === ts.SyntaxKind.EqualsToken && ts.isIdentifier(n.left)))
  return assigned.get(sf)!.filter((n) => (n.left as ts.Identifier).text === id.text && bindingOf(n.left as ts.Identifier) === bound).map((n) => n.right)
}

/** Whether `expr` is the chat's query: `query` (or `props.query`) itself; `q`, the chat page's name
 *  for it, wherever it is not an array callback's own item; a copy that spreads it (`{ ...query }`); or
 *  a name bound to any of these, by its declaration, by a pattern (`{ query: r }`, `{ ...rest } =
 *  query`), or by an assignment after it (`let r = {}; r = query`). */
function isQuery(expr: ts.Expression, depth = 0): boolean {
  const e = bare(expr)
  if (depth > 4) return false
  if (ts.isPropertyAccessExpression(e)) return e.name.text === 'query'
  if (ts.isObjectLiteralExpression(e)) return e.properties.some((p) => ts.isSpreadAssignment(p) && isQuery(p.expression, depth + 1))
  if (!ts.isIdentifier(e)) return false
  if (e.text === 'query') return true
  const bound = bindingOf(e)
  if (e.text === 'q') return !(bound && isItemParameter(bound))
  if (!bound) return false
  if ((ts.isVariableDeclaration(bound) || ts.isParameter(bound)) && !ts.isIdentifier(bound.name)) {
    const off = ts.isVariableDeclaration(bound) && !!bound.initializer && isQuery(bound.initializer, depth + 1)
    if (patternBindsQuery(bound.name, e.text, off)) return true
  } else if (ts.isVariableDeclaration(bound) && bound.initializer && isQuery(bound.initializer, depth + 1)) return true
  return assignmentsTo(e, bound).some((rhs) => isQuery(rhs, depth + 1))
}

/** The keys a pattern takes off the query (`{ seed }`, `{ attach: refs }`), and off the query a
 *  pattern takes apart further (`{ query: { seed } }`), at any depth; `[k]` computed from a value
 *  counts as a read of an unknown key. */
function patternReads(p: ts.BindingPattern, fromQuery: boolean): string[] {
  const out: string[] = []
  for (const el of p.elements) {
    if (ts.isOmittedExpression(el) || el.dotDotDotToken) continue
    const key = el.propertyName ? nameOf(el.propertyName) : ts.isIdentifier(el.name) ? el.name.text : null
    if (fromQuery && ts.isObjectBindingPattern(p) && (key === null || KEYS.includes(key))) out.push(key ?? '[computed]')
    if (!ts.isIdentifier(el.name)) out.push(...patternReads(el.name, key === 'query'))
  }
  return out
}

/** Each read of a launch key in `text`, by its text: off the query by name (`query.seed`, `q?.attach`),
 *  by key (`q['agent']`, `q[K]` with `const K = 'agent'`), by `.get('seed')` on anything, or taken
 *  apart (`const { seed } = q`, `({ query: { seed } }) =>`). A key computed from a value
 *  (`query[k]`) is read as `computed: <text>`, a site only a listed reason may hold. */
function readsIn(rel: string, text: string): string[] {
  const out: string[] = []
  for (const n of nodesOf(syntaxOf(rel, text))) {
    if (ts.isPropertyAccessExpression(n) && KEYS.includes(n.name.text) && isQuery(n.expression)) out.push(textOf(n))
    else if (ts.isElementAccessExpression(n) && isQuery(n.expression)) {
      const key = spelledOf(n.argumentExpression)
      if (key === null) out.push(`computed: ${textOf(n)}`)
      else if (KEYS.includes(key)) out.push(textOf(n))
    } else if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && n.expression.name.text === 'get'
      && n.arguments[0] && KEYS.includes(spelledOf(n.arguments[0]) ?? '')) out.push(textOf(n))
    else if (ts.isVariableDeclaration(n) && !ts.isIdentifier(n.name)) {
      if (patternReads(n.name, !!n.initializer && isQuery(n.initializer)).length) out.push(textOf(n))
    } else if (ts.isParameter(n) && !ts.isIdentifier(n.name) && patternReads(n.name, false).length) out.push(textOf(n))
  }
  return out
}

/** The reads in the chat's route of a key computed from a value, each with why it is no launch key:
 *  every other such read is reported, because no static read can tell which key it takes. */
const COMPUTED_READS: { site: string; why: string }[] = [
  { site: 'pages/chat/panes/useChatPanes.ts: computed: query[k]', why: "Focus mode reads which of the docked panes' own keys (DOCKED_PANES, map) are open, to close them." },
  { site: 'pages/chat/panes/useChatPanes.ts: computed: query[k]', why: "Focus mode carries each open docked pane's own value into what it closed." },
  { site: 'pages/chat/panes/useChatPanes.ts: computed: query[k]', why: 'Leaving focus mode reopens only the docked panes it closed that are not open again.' },
]

/** Each hand write of a launch key in `text`: a string that makes a chat's address with one (a `+`
 *  chain, a template or a `.join` read whole), a query that starts with a chat's own key, a `.set` or
 *  `.append` of one (by name or through a constant), or a `URLSearchParams` built with one, from an
 *  object, entries or a string, written in place or through a name bound to it. */
function writesIn(rel: string, text: string): string[] {
  const sf = syntaxOf(rel, text)
  const out = stringsOf(sf).filter((s) => CHAT_ADDRESS.test(s.words) || CHAT_QUERY.test(s.words)).map((s) => textOf(s.node))
  for (const n of nodesOf(sf)) {
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && ['set', 'append'].includes(n.expression.name.text)
      && n.arguments[0] && CHAT_ONLY.includes(spelledOf(n.arguments[0]) ?? '')) out.push(textOf(n))
    else if (ts.isNewExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'URLSearchParams' && n.arguments?.[0]) {
      const init = heldBy(n.arguments[0])
      const keys = ts.isObjectLiteralExpression(init) ? init.properties.map((p) => (p.name ? nameOf(p.name) : null))
        : ts.isArrayLiteralExpression(init) ? init.elements.map((el) => {
          const pair = heldBy(el)
          return ts.isArrayLiteralExpression(pair) && pair.elements[0] ? spelledOf(pair.elements[0]) : null
        }) : []
      const words = stringsOf(init).map((x) => x.words)
      if (keys.some((k) => CHAT_ONLY.includes(k ?? '')) || words.some((w) => CHAT_PAIRS.test(w))) out.push(textOf(n))
    }
  }
  return out
}

/** The files that read a launch key from the chat's query, past the listed computed reads. The
 *  chat's route is the shell and the chat page with its parts: elsewhere a page's own `project` or
 *  `agent` is that page's key. */
const chatRoute = (rel: string) => rel === join('app', 'App.tsx') || rel === join('pages', 'ChatPage.tsx') || rel.startsWith(join('pages', 'chat') + '/')
function readers(of: readonly { rel: string; text: string }[], listed: readonly string[] = COMPUTED_READS.map((c) => c.site)): string[] {
  const left = [...listed]
  return of.filter((f) => chatRoute(f.rel) && readsIn(f.rel, f.text).filter((r) => {
    const i = left.indexOf(`${f.rel}: ${r}`)
    if (i < 0) return true
    left.splice(i, 1)
    return false
  }).length > 0).map((f) => f.rel).sort()
}
/** The files that write a launch key into a chat's address by hand, anywhere in the tree. The
 *  catalogue of shipped addresses lists addresses, and makes none. */
function writers(of: readonly { rel: string; text: string }[]): string[] {
  return of.filter((f) => f.rel !== join('app', 'linkState.ts') && f.rel !== join('app', 'shippedRoutes.ts') && writesIn(f.rel, f.text).length > 0)
    .map((f) => f.rel).sort()
}
/** The files that call the chat's launch writer. */
const callers = (of: readonly { rel: string; text: string }[]) => of.filter((f) => f.text.includes('chatLaunchPath')
  && nodesOf(syntaxOf(f.rel, f.text)).some((n) => ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'chatLaunchPath'))
  .map((f) => f.rel)

describe("the new chat's launch keys", () => {
  it('🔑 are read and written in linkState.ts alone, across the whole tree', () => {
    expect(SOURCES.length, 'vacuity floor: the tree was listed').toBeGreaterThan(500)
    const links = read('app/linkState.ts')
    for (const key of KEYS) expect(links, key).toContain(`'${key}'`)
    expect(readers(SOURCES), 'read off the chat\'s query outside linkState.ts').toEqual([])
    // Each listed read in the chat's route of a key computed from a value is still there, and says why
    // it is no launch key.
    const computed = SOURCES.filter((f) => chatRoute(f.rel)).flatMap((f) => readsIn(f.rel, f.text).filter((r) => r.startsWith('computed: ')).map((r) => `${f.rel}: ${r}`))
    expect(computed.sort(), 'the computed reads are the listed ones').toEqual(COMPUTED_READS.map((c) => c.site).sort())
    for (const c of COMPUTED_READS) expect(c.why.length, `${c.site} says why`).toBeGreaterThan(20)
    expect(writers(SOURCES), 'written into a chat\'s address by hand').toEqual([])
    // Each page that opens a chat with something to start from goes through the writer.
    const through = callers(SOURCES)
    for (const rel of ['app/App.tsx', 'app/appSdk.tsx', 'app/paletteProjectRows.tsx', 'pages/chat/MissingChat.tsx',
      'pages/dashboard/widgets/Suggestions.tsx', 'pages/loops/LoopsSection.tsx', 'pages/projects/ProjectList.tsx',
      'pages/projects/ProjectsSection.tsx']) expect(through, rel).toContain(rel)
    expect(read('pages/ChatPage.tsx')).toContain('readChatLaunch(query)')
  })

  it('🔴 control: a hand-written read anywhere in the chat\'s route is reported, in each shape', () => {
    const chat = join('pages', 'chat', 'composer', 'Somewhere.tsx')
    for (const [rel, text] of [
      [chat, 'const s = query?.seed || ""'],
      [chat, "const s = props.query['seed']"],
      [chat, "const p = new URLSearchParams(location.search).get('attach')"],
      // Off `q`, the chat page's name for its query, in any file of the chat's route.
      [join('pages', 'ChatPage.tsx'), 'const a = q.attach'],
      [join('pages', 'ChatPage.tsx'), "const a = q['agent']"],
      [join('pages', 'chat', 'panes', 'useChatPanes.ts'), 'const q = query\nconst s = q?.seed'],
      [join('app', 'App.tsx'), 'const q = query; [1].map((q) => q); const n = q.widget_action'],
      [join('pages', 'chat', 'Somewhere.tsx'), 'rows.map((r) => r.id)\nconst p = q.project'],
      // The reviewer's: a read after an array callback that bound `q` has closed is the query's.
      [chat, 'const ids = rows.map((q) => q.id); const s = q.seed'],
      [chat, 'if (items.some(q => q.ok) && q.attach) go()'],
      // A function's own parameter, a callback no array hands an item to, and a name bound to the query.
      [chat, 'function f(q: Record<string, string>) { return q.seed }'],
      [chat, 'withQuery((q) => q.seed)'],
      [chat, 'const r = query ?? {}\nconst s = r.attach'],
      // Taken apart (the reviewer's nit): off `q`, off `query`, renamed, behind a fallback.
      [chat, 'const { seed } = q'],
      [chat, 'const { seed } = query'],
      [chat, 'const { attach: refs } = query ?? {}'],
      // A `/*` or a `//` in a string hides nothing after it.
      [chat, "const g = 'src/*.ts'\nconst s = query.seed\n/** a doc */"],
      [chat, 'const u = "a//b"; const s = query.seed'],
      // The reviewer's (fix-4 round): nested, a parameter, a rest, a spread copy, a later alias, a computed key.
      [chat, 'const { query: { seed } } = props'],
      [chat, 'function C({ query: { seed } }: RouteProps) { return seed }'],
      [chat, 'const { ...rest } = query; const s = rest.seed'],
      [chat, 'const copy = { ...query }; const s = copy.seed'],
      [chat, 'let r = {}; r = query; const s = r.seed'],
      [chat, 'const s = query[k]'],
      // And beside them: an arrow's parameter, a renamed query, a key through a constant.
      [chat, 'const C = ({ query: { attach } }: RouteProps) => attach'],
      [chat, 'const { query: r } = props; const s = r.seed'],
      [chat, "const K = 'seed'; const s = q[K]"],
      [chat, "const K = 'attach'; const v = new URLSearchParams(location.search).get(K)"],
      [chat, 'const { query: { ...all } } = props; const s = all.project'],
    ]) expect(readers([{ rel, text }], []), text).toEqual([rel])
    // A listed computed read holds only at its own site, as often as it is listed.
    const panes = join('pages', 'chat', 'panes', 'useChatPanes.ts')
    expect(readers([{ rel: panes, text: 'const a = query[k]' }]), 'a listed site').toEqual([])
    expect(readers([{ rel: chat, text: 'const a = query[k]' }]), 'the same text elsewhere').toEqual([chat])
    expect(readers([{ rel: panes, text: 'const a = query[k]; const b = query[k]; const c = query[k]; const d = query[k]' }]), 'a fourth').toEqual([panes])
    // A queued message's own field, inside the array callback that names it `q`, is no launch key; nor
    // is a key a comment names, or a page's own key on its own route.
    for (const [rel, text] of [
      [join('pages', 'chat', 'useChatStream.ts'),
        'd.queue.filter((q) => q && q.id).map((q) => queuedMessage(q.id, q.content, q.pastes, undefined, q.widget_action))'],
      [chat, 'const marks = rows.map(function (q) { return q.widget_action })'],
      [chat, '// const s = query.seed\n/* q.attach */'],
      [join('pages', 'tasks', 'TasksPage.tsx'), 'const p = query.project'],
      // A key through a constant that is no launch key, a rest of the props, and the chat page's own props.
      [chat, "const k = 'activity'; const a = query[k]"],
      [chat, 'const { ...rest } = props; const s = rest.seed'],
      [join('pages', 'ChatPage.tsx'), 'export function ChatPage({ sub, navigate, query, setQuery }: Props) { const q = query ?? {}; return q }'],
    ]) expect(readers([{ rel, text }], []), text).toEqual([])
  })

  it('🔴 control: a hand-written write anywhere is reported, in each shape', () => {
    for (const text of [
      'navigate(`chat/new?seed=${encodeURIComponent(s)}`)',
      "navigate(`chat?project=${encodeURIComponent(id)}`)",
      'navigate(`chat/${key}?attach=${refs}`)',
      "navigate('#/chat/new?' + 'seed=' + encodeURIComponent(s))",
      "const q = new URLSearchParams(); q.set('attach', refs)",
      "const q = new URLSearchParams(); q.append('from_widget', '1')",
      'const q = new URLSearchParams({ attach: refs })',
      "const q = new URLSearchParams([['seed', s]])",
      // A `/*` or a `//` in a string hides nothing after it.
      "const g = 'src/*.ts'\nnavigate(`chat/new?seed=${s}`)\n/** a doc */",
      'const u = "a//b"; navigate(`chat/new?seed=${s}`)',
      // The reviewer's (fix-4 round): a string init, an init through a name, a key through a constant,
      // and the run-time shapes the source still spells out.
      'const p = new URLSearchParams(`seed=${s}`)',
      'const init = { seed: s }; const p = new URLSearchParams(init)',
      "const K = 'seed'; p.set(K, s)",
      "navigate({ pathname: 'chat/new', search: '?seed=' + s })",
      "navigate(['chat/new?seed', s].join('='))",
      "navigate(`chat/new?${'seed'}=${s}`)",
      "const pairs = [['attach', refs]]; const p = new URLSearchParams(pairs)",
    ]) expect(writers([{ rel: join('pages', 'x', 'Planted.tsx'), text }]), text).toEqual([join('pages', 'x', 'Planted.tsx')])
    // A page's own key on its own route is no chat's, and an address a comment names is no write.
    for (const text of ['navigate(`tasks?project=${id}`)', '// navigate(`chat/new?seed=${s}`)\n/** chat?attach=x */'])
      expect(writers([{ rel: join('pages', 'x', 'Tasks.tsx'), text }]), text).toEqual([])
  })

  it('🔑 attach with project keeps both, and with a draft keeps the draft', () => {
    const launch = readChatLaunch({ project: 'p-0a1b2c3d', attach: 'knowledge:k1,artifact:lisbon', seed: 'Plan the trip' })
    expect(launch).toEqual({
      seed: 'Plan the trip', agent: '', widgetAction: '', project: 'p-0a1b2c3d', fromWidget: false, attach: ['knowledge:k1', 'artifact:lisbon'],
    })
    // Read back from the address its writer makes.
    const path = chatLaunchPath(launch)
    expect(path.startsWith('chat/new?')).toBe(true)
    expect(readChatLaunch(Object.fromEntries(new URLSearchParams(path.split('?')[1])))).toEqual(launch)
  })

  it('reads each ref once, in the link\'s order, and an empty attach as none', () => {
    expect(readChatLaunch({ attach: ' artifact:a , knowledge:k1,,artifact:a ' }).attach).toEqual(['artifact:a', 'knowledge:k1'])
    expect(readChatLaunch({ attach: '' }).attach).toEqual([])
    expect(readChatLaunch(undefined)).toEqual({ seed: '', agent: '', widgetAction: '', project: '', fromWidget: false, attach: [] })
  })

  it('🔑 nothing sends: no key names a send, and attach is never a draft', () => {
    const launch = readChatLaunch({ attach: 'knowledge:k1', send: '1', auto: '1', submit: 'yes' })
    expect(Object.keys(launch).sort()).toEqual(['agent', 'attach', 'fromWidget', 'project', 'seed', 'widgetAction'])
    expect(launch.seed, 'an attached item is a chip, not words in the draft').toBe('')
    // A session the launch names keeps its own address, and its keys ride along.
    expect(chatLaunchPath({ session: 'chat 7', seed: 'hi', attach: ['artifact:a'] })).toBe('chat/chat%207?seed=hi&attach=artifact%3Aa')
    expect(chatLaunchPath({})).toBe('chat/new')
    // A nonce is written only when it is the staged turn's own, whoever passes it.
    expect(chatLaunchPath({ seed: 'hi', widgetAction: 'a-guess' })).toBe('chat/new?seed=hi')
  })
})
