// @module-tag tree-scan
/** ONE bridge, ONE way into chat — asserted against the tree, not against intent.
 *
 *  Two dual paths are cheap to reintroduce and expensive to find later: a host that
 *  listens to raw `message` and interprets `widget-action` itself (bypassing the
 *  provenance validator), and a non-chat host that navigates to chat its own way
 *  (bypassing `ne:launch-chat`). Both would look like ordinary local code in review.
 *  This rail names the single owner of each. */
import { join } from 'node:path'
import { describe, it, expect } from 'vitest'
import ts from 'typescript'
import { readWidgetMessage } from './useWidgetActionBridge'
import { clearStagedWidgetAction, stageWidgetAction } from './stagedAction'
import { chatLaunchPath } from '../../app/linkState'
import { filesUnder, readSource } from '../../test/sourceTree'
import { bare, lineOf, nodesOf, stringsOf, syntaxOf, textOf } from '../../test/sourceSyntax'

const SRC = join(process.cwd(), 'src')
const BRIDGE = join('ui', 'widget', 'useWidgetActionBridge.ts')
/** The LEAF that owns the `[UI]` turn dialect (prefix, byte cap, event name, publisher).
 *  Split out of the bridge because the bridge imports `appSdk` for `launchChat` and
 *  `appSdk` imports the genui renderer, which needs the dialect — a five-module import cycle
 *  that cost one module its exports. The "exactly one owner" claim below is UNCHANGED; only
 *  which file that owner is moved, and the bridge re-exports it so consumers keep one path. */
const DIALECT = join('ui', 'widget', 'actionTurn.ts')

function sourceFiles(dir: string): string[] {
  return filesUnder(dir, (entry) => /\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry))
}

const files = sourceFiles(SRC).map((path) => ({
  rel: path.slice(SRC.length + 1),
  text: readSource(path),
}))

// The scans below read each file's syntax tree (`test/sourceSyntax.ts`), not its text: a comment is
// no node and a string is one token whatever it holds, so no write hides behind a `'src/*.ts'` or a
// `"a//b"`, and a `+` of strings is read as the words it makes.

const KEY = 'widget_action'

/** Every line of the writer's module that names the nonce's key, read or write, trimmed, in order:
 *  `LAUNCH_KEYS.widgetAction` or `LAUNCH_KEYS['widgetAction']`; `LAUNCH_KEYS` taken any other way (an
 *  alias, a computed key, a walk of its entries), which may name it; and `widget_action` itself, a name
 *  or in a string. The key's own entry in the keys' declaration is not one. */
function nonceKeyLines(text: string): string[] {
  const sf = syntaxOf('linkState.ts', text)
  const all = nodesOf(sf)
  const decl = all.find((n): n is ts.VariableDeclaration => ts.isVariableDeclaration(n) && ts.isIdentifier(n.name) && n.name.text === 'LAUNCH_KEYS')
  const keys = decl?.initializer && bare(decl.initializer)
  const own = keys && ts.isObjectLiteralExpression(keys)
    ? keys.properties.find((p) => !!p.name && ts.isIdentifier(p.name) && p.name.text === 'widgetAction') : undefined
  const named: ts.Node[] = []
  for (const n of all) {
    if (ts.isIdentifier(n) && n.text === 'LAUNCH_KEYS' && n !== decl?.name) {
      const p = n.parent
      if (ts.isPropertyAccessExpression(p) && p.expression === n) { if (p.name.text === 'widgetAction') named.push(n) }
      else if (ts.isElementAccessExpression(p) && p.expression === n && ts.isStringLiteralLike(p.argumentExpression)) {
        if (p.argumentExpression.text === 'widgetAction') named.push(n)
      } else named.push(n)
    }
    if (ts.isIdentifier(n) && n.text === KEY) named.push(n)
  }
  for (const s of stringsOf(sf)) if (s.words.includes(KEY) && !(own && s.node.parent === own)) named.push(s.node)
  const lines: string[] = []
  for (const n of named.sort((x, y) => x.getStart() - y.getStart())) if (!lines.includes(lineOf(n))) lines.push(lineOf(n))
  return lines
}

/** Where a mention of the key sits: the read, field, member or call it is part of. */
function siteOf(node: ts.Node): string {
  const p = node.parent
  if (ts.isIdentifier(node)) return textOf(p).replace(/[;,]$/, '')
  if (ts.isPropertyAssignment(p) || ts.isCallExpression(p) || ts.isArrayLiteralExpression(p)) return textOf(p)
  return textOf(node)
}

/** Every mention of `widget_action` in `of`, a name (a field read, a property, a shorthand, a type's
 *  member, a binding, an attribute) or in a string (a `+` chain read whole), by file and site. */
function nonceKeyMentions(of: readonly { rel: string; text: string }[]): string[] {
  const out: string[] = []
  for (const f of of) {
    if (!/widget|_action/.test(f.text)) continue
    const sf = syntaxOf(f.rel, f.text)
    const at: ts.Node[] = nodesOf(sf).filter((n) => ts.isIdentifier(n) && n.text === KEY)
    for (const s of stringsOf(sf)) if (s.words.includes(KEY)) at.push(s.node)
    for (const n of at.sort((x, y) => x.getStart() - y.getStart())) out.push(`${f.rel}: ${siteOf(n)}`)
  }
  return out
}

/** The tree's mentions of `widget_action`, each with what it is. The key names two things: the nonce a
 *  chat's address carries (written only by `chatLaunchPath`, behind the stage's check), and the mark a
 *  turn a widget raised carries to and from the gateway (`widget_action: true` on a send, a queued
 *  message's and a history turn's own field). Any mention not listed here, a new write in any shape
 *  among them, is reported because it is new. */
const KEY_SITES: { site: string; why: string }[] = [
  { site: "app/linkState.ts: widgetAction: 'widget_action'", why: "The launch keys' declaration: the one name of the nonce's key on a chat's address." },
  { site: 'lib/api.ts: widget_action: true', why: "A send's own field: the gateway's mark that the turn came from a widget, which grants it none of the owner's say." },
  { site: 'lib/api.ts: widget_action?: boolean', why: "A queued message's own mark in a chat's detail, as the gateway returns it." },
  { site: 'pages/chat/chatTypes.ts: m.meta?.widget_action', why: "A history turn's own mark, read to draw it as a widget's turn." },
  { site: 'pages/chat/chatTypes.ts: widget_action?: boolean', why: "A rewound turn's meta in a turn's rewind record, as the gateway returns it." },
  { site: 'pages/chat/chatTypes.ts: widget_action?: boolean', why: "A rewound turn's meta in a history message's rewind record, as the gateway returns it." },
  { site: 'pages/chat/chatTypes.ts: widget_action?: boolean', why: "A history message's own meta, as the gateway returns it." },
  { site: 'pages/chat/composer/useChatComposer.ts: meta.widget_action', why: "The mark on the turn drawn while a widget's turn sends." },
  { site: 'pages/chat/composer/useChatComposer.ts: widget_action: true', why: "The send's own field for a widget's turn, handed to lib/api.ts's send." },
  { site: 'pages/chat/transcript/RewindDivider.tsx: m.meta?.widget_action', why: "A rewound turn's own mark, read to draw it as a widget's turn." },
  { site: 'pages/chat/useChatStream.ts: d.widget_action', why: "A queued message's own mark in the gateway's queued event." },
  { site: 'pages/chat/useChatStream.ts: d.widget_action', why: "A sent message's own mark in the gateway's sent event." },
  { site: 'pages/chat/useChatStream.ts: q.widget_action', why: "A queued message's own mark, as a chat's detail lists its queue." },
]

/** Each call of the chat's launch writer, `chatLaunchPath(...)`, by file: its arguments' text, whether
 *  any part of them spreads, whether they name a nonce, and whether each is read field by field (an
 *  object, or a choice between objects). Also each place the writer is taken as a value, not called. */
function launchCalls(of: readonly { rel: string; text: string }[]): { rel: string; args: string; spreads: boolean; namesNonce: boolean; opaque: boolean }[] {
  const out: ReturnType<typeof launchCalls> = []
  const literal = (e: ts.Expression): boolean => {
    const x = bare(e)
    return ts.isObjectLiteralExpression(x) || (ts.isConditionalExpression(x) && literal(x.whenTrue) && literal(x.whenFalse))
  }
  for (const f of of) {
    if (!f.text.includes('chatLaunchPath')) continue
    for (const n of nodesOf(syntaxOf(f.rel, f.text))) {
      if (!ts.isIdentifier(n) || n.text !== 'chatLaunchPath') continue
      const p = n.parent
      if (ts.isCallExpression(p) && p.expression === n) {
        const inner = p.arguments.flatMap((a) => nodesOf(a))
        out.push({
          rel: f.rel,
          args: p.arguments.map((a) => textOf(a, 400)).join(', '),
          spreads: inner.some((x) => ts.isSpreadAssignment(x) || ts.isSpreadElement(x)),
          namesNonce: inner.some((x) => ts.isIdentifier(x) && x.text === 'widgetAction'),
          opaque: !p.arguments.every(literal),
        })
      } else if (!ts.isImportSpecifier(p) && !(ts.isFunctionDeclaration(p) && p.name === n)) {
        out.push({ rel: f.rel, args: `(taken as a value: ${lineOf(n)})`, spreads: false, namesNonce: false, opaque: true })
      }
    }
  }
  return out
}

describe('widget bridge: single path', () => {
  it('scanned a real tree', () => {
    // Vacuity floor: a rail that matches nothing reads exactly like a clean rail.
    expect(files.length).toBeGreaterThan(200)
    expect(files.some((f) => f.rel === BRIDGE)).toBe(true)
  })

  it('gives the ne:widget-action event exactly one owner', () => {
    const owners = files.filter((f) => f.text.includes("'ne:widget-action'")).map((f) => f.rel)
    expect(owners).toEqual([DIALECT])
    // The bridge REACHES the name, it does not re-declare it — a second literal is exactly
    // the dual path this rail exists to catch, and it would still satisfy "one owner" above
    // if the list were merely widened.
    const bridge = files.find((f) => f.rel === BRIDGE)?.text ?? ''
    expect(bridge).toContain("from './actionTurn'")
    expect(bridge).not.toContain("= 'ne:widget-action'")
  })

  it('lets nobody but the bridge interpret a widget-action off a raw message listener', () => {
    const raw = files.filter((f) => f.rel !== BRIDGE && /'widget-action'|"widget-action"/.test(f.text))
    expect(raw.map((f) => f.rel)).toEqual([join('ui', 'widget', 'widgetSrcdoc.ts')]) // the child that POSTS it
  })

  it('routes non-chat hosts through the ONE ne:launch-chat path', () => {
    const bridge = files.find((f) => f.rel === BRIDGE)?.text ?? ''
    expect(bridge).toContain("'ne:launch-chat'")
    // The fallback must not grow its own navigation: a `navigate(`/hash write here
    // would be a second way into chat that no other launcher shares.
    expect(bridge).not.toMatch(/navigate\(|location\.hash\s*=|history\.(push|replace)State/)
    // …and `ne:launch-chat` is raised in two places only, both into the ONE handler (App.tsx): the
    // apps' SDK helper, and the bridge, whose launch carries the nonce its new chat drains a
    // staged action by (an option the apps' SDK does not offer).
    const emitters = files
      .filter((f) => f.text.includes('new CustomEvent(') && f.text.includes("'ne:launch-chat'"))
      .map((f) => f.rel)
      .sort()
    expect(emitters).toEqual([join('app', 'appSdk.tsx'), BRIDGE].sort())
  })

  it('lets a chat\'s address name a staged action only through the stage\'s own check', () => {
    // An app raises `ne:launch-chat` too, and any page may build a chat's address; only a nonce the
    // bridge staged may reach the new chat. The chat's one launch writer (`app/linkState.ts`
    // `chatLaunchPath`) writes the key only for the staged turn's own nonce, whoever calls it.
    const links = files.find((f) => f.rel === join('app', 'linkState.ts'))?.text ?? ''
    expect(links).toContain("widgetAction: 'widget_action'")
    expect(links).toContain("import { isStagedWidgetNonce } from '../ui/widget/stagedAction'")
    // The module names the key on two lines alone: its reader's one read, and its one write, behind the
    // stage's check. A write in any other shape (an append, a template, an entry) is a line this list
    // does not hold.
    expect(nonceKeyLines(links), 'the key is read once and written once, behind the stage\'s check').toEqual([
      'widgetAction: word(LAUNCH_KEYS.widgetAction),',
      'if (isStagedWidgetNonce(launch.widgetAction)) qs.set(LAUNCH_KEYS.widgetAction, launch.widgetAction)',
    ])
    // And by what it writes, whatever shape it takes: a nonce nobody staged is never written, and the
    // staged turn's own is.
    expect(chatLaunchPath({ seed: 'hi', widgetAction: 'not-staged' })).toBe('chat/new?seed=hi')
    stageWidgetAction({ text: '[UI] refresh', at: 0, nonce: 'n-staged' })
    try {
      expect(chatLaunchPath({ seed: 'hi', widgetAction: 'n-staged' })).toBe('chat/new?seed=hi&widget_action=n-staged')
      expect(chatLaunchPath({ seed: 'hi', widgetAction: 'n-other' })).toBe('chat/new?seed=hi')
    } finally { clearStagedWidgetAction() }
    // No file names the key anywhere else: every mention of it in the tree is a listed site.
    expect(nonceKeyMentions(files).sort(), 'each mention of the key is a listed site; a new one is a new site')
      .toEqual(KEY_SITES.map((k) => k.site).sort())
    for (const k of KEY_SITES) expect(k.why.length, `${k.site} says why it may name the key`).toBeGreaterThan(20)
    // And only the shell's launch hands the writer a nonce, field by field, with no spread that
    // could carry an event's own `widgetAction` past what it names.
    const calls = launchCalls(files)
    expect(calls.length, 'vacuity floor: the writer\'s calls were found').toBeGreaterThanOrEqual(6)
    expect(calls.filter((c) => c.spreads).map((c) => c.rel), 'no call spreads into the writer').toEqual([])
    expect(calls.filter((c) => c.opaque).map((c) => `${c.rel}: ${c.args}`), 'each call hands the writer its fields by name').toEqual([])
    expect([...new Set(calls.filter((c) => c.namesNonce).map((c) => c.rel))], 'only the shell\'s launch names a nonce')
      .toEqual([join('app', 'App.tsx')])
    const app = files.find((f) => f.rel === join('app', 'App.tsx'))?.text ?? ''
    const named = app.split('\n').filter((line) => /widget_action|widgetAction/.test(line) && !line.trim().startsWith('//'))
    expect(named.map((line) => line.trim())).toEqual(["widgetAction: typeof d.widgetAction === 'string' ? d.widgetAction : '',"])
  })

  it('🔴 control: a key line in the writer\'s module, in each shape, is reported', () => {
    const links = files.find((f) => f.rel === join('app', 'linkState.ts'))!.text
    const guarded = '  if (isStagedWidgetNonce(launch.widgetAction)) qs.set(LAUNCH_KEYS.widgetAction, launch.widgetAction)'
    expect(links, 'the guarded write is where the plants put theirs').toContain(guarded)
    for (const [stray, line] of [
      ['  if (launch.widgetAction) qs.set(LAUNCH_KEYS.widgetAction, launch.widgetAction)', ''],
      ["  const tail = launch.widgetAction ? `&${LAUNCH_KEYS.widgetAction}=${launch.widgetAction}` : ''", ''],
      ["  if (launch.widgetAction) qs.set(LAUNCH_KEYS['widgetAction'], launch.widgetAction)", ''],
      ["  if (launch.widgetAction) qs.set('widget_action', launch.widgetAction)", ''],
      ["  if (launch.widgetAction) qs.set('widget_' + 'action', launch.widgetAction)", ''],
      // The reviewer's nit: an alias of the keys, a computed key, a walk of the entries.
      ['  const K = LAUNCH_KEYS; if (launch.widgetAction) qs.set(K.widgetAction, launch.widgetAction)', ''],
      ["  for (const k of ['widgetAction'] as const) if (launch[k]) qs.set(LAUNCH_KEYS[k], String(launch[k]))", ''],
      ["  for (const [field, key] of Object.entries(LAUNCH_KEYS)) if (field === 'widgetAction') qs.set(key, launch.widgetAction ?? '')", ''],
      // The reviewer's: a `/*` or a `//` in a string hides nothing after it.
      ["  const g = 'src/*.ts'\n  if (launch.widgetAction) qs.set('widget_action', launch.widgetAction)\n  /** a doc */",
        "if (launch.widgetAction) qs.set('widget_action', launch.widgetAction)"],
      ['  const u = "a//b"; if (launch.widgetAction) qs.set(\'widget_action\', launch.widgetAction)', ''],
      // A key the source spells out in parts.
      ["  if (launch.widgetAction) qs.set(['widget', 'action'].join('_'), launch.widgetAction)", ''],
      ["  if (launch.widgetAction) qs.set(`widget${'_'}action`, launch.widgetAction)", ''],
    ]) expect(nonceKeyLines(links.replace(guarded, `${guarded}\n${stray}`)), stray).toContain(line || stray.trim())
    expect(nonceKeyLines('// qs.set(LAUNCH_KEYS.widgetAction, x)\n/** `widget_action` */'), 'a comment is no line').toEqual([])
  })

  it('🔴 control: a mention of the key anywhere, in each shape of write, is reported', () => {
    for (const text of [
      "navigate(chatLaunchPath({ seed: s }) + '&widget_action=' + s)",
      'navigate(`chat/new?widget_action=${n}`)',
      "const p = new URLSearchParams(); p.append('widget_action', n)",
      "const k = 'widget_' + 'action'; p.set(k, n)",
      'const patch = { [\'widget_action\']: n }',
      // The reviewer's: a `/*` or a `//` in a string hides nothing after it.
      "const g = 'src/*.ts'\np.set('widget_action', n)\n/** a doc */",
      'const u = "a//b"; p.set(\'widget_action\', n)',
      // The reviewer's: an object, entries, a key spelled apart from its `&`, a nested patch.
      'const p = new URLSearchParams({ seed: s, widget_action: n })',
      "const p = new URLSearchParams([['widget_action', n]])",
      "const tail = '&' + 'widget_action=' + n",
      'setQuery({ opts: { a: 1 }, widget_action: n })',
      // The coordinator's: a nested patch with the key as a shorthand.
      'setQuery({ x: { a: 1 }, widget_action })',
      // A send's own field is a site too, once it is listed: here it is new.
      'send({ widget_action: true })',
      // The reviewer's (fix-4 round): keys the source spells out in parts, and an address as an object.
      "const k = ['widget', 'action'].join('_'); p.set(k, n)",
      "const k = `widget${'_'}action`; p.set(k, n)",
      "navigate({ pathname: 'chat/new', search: '?widget_action=' + n })",
      "const K = 'widget_action'; p.set(K, n)",
    ]) expect(nonceKeyMentions([{ rel: 'x.tsx', text }]).length, text).toBeGreaterThan(0)
    expect(nonceKeyMentions([{ rel: 'x.tsx', text: '// the shell stages it (`?widget_action=`)\n/** widget_action */\nconst a = 1' }]),
      'a comment is no mention').toEqual([])
    // A listed site in another file is a new site.
    const site = KEY_SITES[0]!.site
    const [rel, at] = [site.slice(0, site.indexOf(': ')), site.slice(site.indexOf(': ') + 2)]
    const host = files.find((f) => f.rel === rel)!
    expect(nonceKeyMentions([{ rel: 'x.tsx', text: host.text }]).filter((m) => m === `x.tsx: ${at}`).length, 'the plant took').toBeGreaterThan(0)
  })

  it('🔴 control: a second caller naming a nonce, a spread, an opaque launch and the writer passed on are each reported', () => {
    const app = files.find((f) => f.rel === join('app', 'App.tsx'))!
    const second = { rel: join('pages', 'dashboard', 'widgets', 'Suggestions.tsx'), text: 'navigate(chatLaunchPath({ seed: s, widgetAction: n }))' }
    expect(launchCalls([second]).map((c) => [c.args, c.namesNonce])).toEqual([['{ seed: s, widgetAction: n }', true]])
    const spread = { ...app, text: app.text.replace("widgetAction: typeof d.widgetAction === 'string' ? d.widgetAction : '',", "widgetAction: typeof d.widgetAction === 'string' ? d.widgetAction : '', ...d,") }
    expect(spread.text, 'the plant took').not.toBe(app.text)
    expect(launchCalls([spread]).some((c) => c.spreads)).toBe(true)
    for (const text of ['navigate(chatLaunchPath(d))', 'navigate(chatLaunchPath(keep ?? {}))', 'navigate(chatLaunchPath(ok ? { seed: s } : d))',
      'const go = chatLaunchPath; navigate(go({ seed: s }))', "navigate(chatLaunchPath({ seed: s, 'x': [...rest] }))"])
      expect(launchCalls([{ rel: 'x.tsx', text }]).some((c) => c.opaque || c.spreads), text).toBe(true)
    // The writer's own declaration and its import are neither a call nor the writer passed on.
    expect(launchCalls([{ rel: 'x.tsx', text: "import { chatLaunchPath } from './linkState'\nexport function chatLaunchPath(l: object) { return '' }" }])).toEqual([])
  })

  it('keeps the chat host a consumer of the hook, never a second listener', () => {
    // The chat page and its composer region (`chat/composer/useChatComposer.ts`), which sends the action.
    const page = files.find((f) => f.rel === join('pages', 'ChatPage.tsx'))?.text ?? ''
    const chat = files.find((f) => f.rel === join('pages', 'chat', 'composer', 'useChatComposer.ts'))?.text ?? ''
    expect(chat).toContain('useWidgetActionBridge(')
    expect(chat).toContain('takePendingWidgetAction(widgetActionNonce)')
    expect(chat).not.toContain("addEventListener('message'")
    expect(page, 'the page is not a second host').not.toContain('useWidgetActionBridge(')
    expect(page).not.toContain("addEventListener('message'")
  })
})

/** The contract as a FIXTURE: one entry per child→parent message. The validator and the
 *  architecture doc are both checked against it, so the table a widget author reads
 *  cannot drift from the code that enforces it. */
const WIRE_CONTRACT = [
  { type: 'widget-height', sample: { type: 'widget-height', height: 120, width: 300 } },
  { type: 'widget-action', sample: { type: 'widget-action', action: 'refresh', payload: { a: 1 } } },
  { type: 'widget-error', sample: { type: 'widget-error', message: 'boom' } },
] as const
const RESERVED_PARENT_PREFIX = '__edit_mode_'

describe('widget wire contract: doc and validator agree', () => {
  const doc = readSource(join(process.cwd(), '..', 'docs', 'architecture', 'widgets.md'))

  it('accepts every message in the fixture', () => {
    const frame = document.createElement('iframe')
    document.body.appendChild(frame)
    for (const { sample } of WIRE_CONTRACT) {
      const e = new MessageEvent('message', { data: sample, source: frame.contentWindow })
      expect(readWidgetMessage(e, frame), `${sample.type} must validate`).not.toBeNull()
    }
  })

  it('documents every message, the reserved namespace, and the payload cap', () => {
    for (const { type } of WIRE_CONTRACT) expect(doc).toContain(`\`${type}\``)
    expect(doc).toContain(RESERVED_PARENT_PREFIX)
    expect(doc).toContain('16 KiB')
    expect(doc).toContain('isTrusted')
    expect(doc).toContain('ne:launch-chat')
  })
})
