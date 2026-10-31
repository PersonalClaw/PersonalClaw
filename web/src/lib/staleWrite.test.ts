// @module-tag tree-scan
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'
import { withoutComments } from '../test/domCensus'
import { importGraph, nearestRoute, reach, routeRoots, type Tree } from '../test/routeTables'
import { filesUnder, readSource } from '../test/sourceTree'
import {
  basedOn, differencePatch, documentText, isStaleWrite, mergeRecord, mergeText, presentSecrets, rebaseList, rebaseRecord,
  rebaseText, sameDocument,
} from './staleWrite'

// ── Re-applying a refused change onto what is stored now ─────────────────────────────────────
//
// A save refused as stale (`409 stale_write`) keeps the user's change; "Reload and reapply" puts it
// back on top of the copy another tab saved. These pin the merges that decide what that means —
// and, as important, when they refuse to decide (`null`), because a merge that picked a side where
// both changed the same part would be the silent overwrite this whole contract removes.
//
// Every case of each helper is a row of one table, and each table asserts how many rows it holds,
// so a row deleted from it fails here instead of shrinking what is proved. The census at the end
// holds every surface that saves a whole document to these helpers. What each surface sends, and
// that it keeps the draft, is `pages/staleWriteSurfaces.test.tsx`'s; the notice and the guard are
// `ui/staleWriteNotice.test.tsx`'s.

const lines = (...l: string[]) => l.join('\n')
const BASE = lines('# Skill', 'one', 'two', 'three', 'four')
const NOTE = 'Always cite sources.'

/** mergeText(base, mine, theirs): `merged`, or `null` where it must refuse to choose. */
const MERGES: Array<{ row: string; base: string; mine: string; theirs: string; merged: string | null }> = [
  { row: 'keeps both edits when they touch different lines',
    base: BASE, mine: BASE.replace('one', 'ONE'), theirs: BASE.replace('four', 'FOUR'),
    merged: lines('# Skill', 'ONE', 'two', 'three', 'FOUR') },
  { row: 'keeps an insertion from each side',
    base: BASE, mine: `${BASE}\nfive`, theirs: BASE.replace('# Skill', '# Skill\nintro'),
    merged: lines('# Skill', 'intro', 'one', 'two', 'three', 'four', 'five') },
  { row: 'refuses to choose when both changed the same line differently',
    base: BASE, mine: BASE.replace('two', 'mine'), theirs: BASE.replace('two', 'theirs'), merged: null },
  { row: 'keeps edits of neighbouring lines — each line has one author, so there is one outcome',
    base: BASE, mine: BASE.replace('two', 'TWO'), theirs: BASE.replace('three', 'THREE'),
    merged: lines('# Skill', 'one', 'TWO', 'THREE', 'four') },
  // The shape a server-side appender produces (an accepted instruction, a workflow run's note): the
  // user edits what they see while a line lands after it.
  { row: 'keeps my edit of the last line when the other side appended after it',
    base: NOTE, mine: 'Always cite your sources.', theirs: `${NOTE}\nPrefer primary sources.`,
    merged: 'Always cite your sources.\nPrefer primary sources.' },
  { row: 'refuses two different insertions at one point — which comes first is a choice',
    base: BASE, mine: `${BASE}\nmine`, theirs: `${BASE}\ntheirs`, merged: null },
  { row: 'refuses an insertion inside lines the other side replaced',
    base: BASE, mine: BASE.replace('two\nthree', 'TWO-THREE'), theirs: BASE.replace('two\nthree', 'two\nnew\nthree'), merged: null },
  { row: 'the same edit on both sides is one edit',
    base: BASE, mine: BASE.replace('two', 'TWO'), theirs: BASE.replace('two', 'TWO'), merged: BASE.replace('two', 'TWO') },
  { row: 'no edit of mine leaves theirs exactly',
    base: BASE, mine: BASE, theirs: BASE.replace('three', '3'), merged: BASE.replace('three', '3') },
  // At one point, an insertion goes before the lines the other side replaced from there.
  { row: 'keeps a line I inserted just before a line the other side replaced',
    base: lines('a', 'b'), mine: lines('a', 'new', 'b'), theirs: lines('a', 'B'), merged: lines('a', 'new', 'B') },
  { row: 'refuses my insertion inside lines the other side replaced',
    base: BASE, mine: BASE.replace('two\nthree', 'two\nnew\nthree'), theirs: BASE.replace('two\nthree', 'TWO-THREE'), merged: null },
]

const REC = { name: 'Ada', model: 'a', skills: ['s1', 's2'], prompt: 'x\ny\nz' }
const { model: _gone, ...REC_WITHOUT_MODEL } = REC

/** mergeRecord(base, mine, theirs), field by field. */
const RECORD_MERGES: Array<{ row: string; base?: Record<string, unknown>; mine: Record<string, unknown>; theirs: Record<string, unknown>; merged: Record<string, unknown> | null }> = [
  { row: 'takes the fields I changed and theirs for every other one',
    mine: { ...REC, model: 'b' }, theirs: { ...REC, name: 'Grace' }, merged: { ...REC, model: 'b', name: 'Grace' } },
  // I removed s2 and added s3; they added s4.
  { row: 'treats a list of names as a set: my addition joins theirs, my removal leaves it',
    mine: { ...REC, skills: ['s1', 's3'] }, theirs: { ...REC, skills: ['s1', 's2', 's4'] }, merged: { ...REC, skills: ['s1', 's4', 's3'] } },
  { row: 'merges a text field by line',
    mine: { ...REC, prompt: 'X\ny\nz' }, theirs: { ...REC, prompt: 'x\ny\nZ' }, merged: { ...REC, prompt: 'X\ny\nZ' } },
  { row: 'refuses a field both sides changed to different values',
    mine: { ...REC, model: 'b' }, theirs: { ...REC, model: 'c' }, merged: null },
  { row: 'a field I deleted stays deleted when they left it alone',
    mine: REC_WITHOUT_MODEL, theirs: { ...REC, name: 'Grace' }, merged: { ...REC_WITHOUT_MODEL, name: 'Grace' } },
  // A field that is no text, list of names or record: taken whole from the side that changed it.
  { row: 'takes their change to a field I left alone, whatever its type', base: { ...REC, limit: 1 },
    mine: { ...REC, limit: 1 }, theirs: { ...REC, limit: 2 }, merged: { ...REC, limit: 2 } },
  { row: 'the same change on both sides is one change, whatever its type', base: { ...REC, limit: 1 },
    mine: { ...REC, limit: 2 }, theirs: { ...REC, limit: 2 }, merged: { ...REC, limit: 2 } },
  { row: 'merges a record field by field', base: { ...REC, budget: { calls: 1, tokens: 1 } },
    mine: { ...REC, budget: { calls: 2, tokens: 1 } }, theirs: { ...REC, budget: { calls: 1, tokens: 2 } },
    merged: { ...REC, budget: { calls: 2, tokens: 2 } } },
]

/** rebaseList(before, after) re-applied onto `theirs`. */
const LIST_REBASES: Array<{ row: string; before: string[]; after: string[]; theirs: string[]; rebased: string[] }> = [
  { row: 're-applies names in and out onto a list another tab changed', before: ['a', 'b'], after: ['a', 'c'], theirs: ['a', 'b', 'd'], rebased: ['a', 'd', 'c'] },
  { row: 'never duplicates a name the other tab added too', before: ['a'], after: ['a', 'b'], theirs: ['a', 'b'], rebased: ['a', 'b'] },
]

describe('the header and the refusal', () => {
  it('names the base revision in If-Match, quoted', () => {
    expect(basedOn('a1b2')).toEqual({ 'If-Match': '"a1b2"' })
  })

  const REFUSALS: Array<{ row: string; error: unknown; stale: boolean }> = [
    { row: 'the refusal code, whatever its sentence', error: Object.assign(new Error('anything'), { code: 'stale_write' }), stale: true },
    { row: 'another code with a stale-sounding sentence', error: Object.assign(new Error('changed since this copy was read'), { code: 'bad_request' }), stale: false },
    { row: 'the code as a bare string', error: 'stale_write', stale: false },
  ]
  it('holds every refusal shape', () => expect(REFUSALS).toHaveLength(3))
  it.each(REFUSALS.map((r) => [r.row, r] as const))('recognises the refusal by its code, not its sentence: %s', (_row, { error, stale }) => {
    expect(isStaleWrite(error)).toBe(stale)
  })
})

describe('mergeText', () => {
  it('holds every merge', () => expect(MERGES).toHaveLength(11))
  it.each(MERGES.map((r) => [r.row, r] as const))('%s', (_row, { base, mine, theirs, merged }) => {
    expect(mergeText(base, mine, theirs)).toBe(merged)
    // The Rebase a text editor saves through is this merge, started from the same base.
    expect(rebaseText(base, mine)(theirs)).toBe(merged)
  })
})

describe('mergeRecord', () => {
  it('holds every merge', () => expect(RECORD_MERGES).toHaveLength(8))
  // Strictly equal: a field deleted must be absent, not present as `undefined`.
  it.each(RECORD_MERGES.map((r) => [r.row, r] as const))('%s', (_row, { base = REC, mine, theirs, merged }) => {
    expect(mergeRecord(base, mine, theirs)).toStrictEqual(merged)
    expect(rebaseRecord(base, mine)(theirs)).toStrictEqual(merged)
  })
})

describe('rebaseList', () => {
  it('holds every rebase', () => expect(LIST_REBASES).toHaveLength(2))
  it.each(LIST_REBASES.map((r) => [r.row, r] as const))('%s', (_row, { before, after, theirs, rebased }) => {
    expect(rebaseList(before, after)(theirs)).toEqual(rebased)
  })
})

describe('showing the difference', () => {
  it('compares content, not key order', () => {
    expect(sameDocument({ a: 1, b: [2] }, { b: [2], a: 1 })).toBe(true)
    expect(documentText({ b: 1, a: 2 })).toBe('{\n  "a": 2,\n  "b": 1\n}')
  })

  const PATCHES: Array<{ row: string; from: unknown; to: unknown; patch: string }> = [
    { row: 'marks what went and what came', from: ['alpha', 'beta'], to: ['alpha', 'gamma'], patch: ' [\n   "alpha",\n-  "beta"\n+  "gamma"\n ]' },
    { row: 'nothing when they read the same', from: 'same', to: 'same', patch: '' },
    // An empty text has no lines: a first note added is no lone "-" against nothing.
    { row: 'an empty text added to', from: '', to: 'Use for incident retros.', patch: '+Use for incident retros.' },
    { row: 'a text emptied', from: 'Seeded.', to: '', patch: '-Seeded.' },
  ]
  it('holds every difference', () => expect(PATCHES).toHaveLength(4))
  it.each(PATCHES.map((r) => [r.row, r] as const))('%s', (_row, { from, to, patch }) => {
    expect(differencePatch(from, to)).toBe(patch)
  })

  it('shows a stored secret as saved and never shows a typed one', () => {
    const present = presentSecrets((k) => k === 'api_key' || k === 'token', ['api_key'])
    // The form holds a stored secret BLANK, meaning "keep it" — the review read it as emptied.
    expect(present({ endpoint: 'e1', api_key: '', token: 'fake-key-typed-now' })).toEqual({
      endpoint: 'e1', api_key: '(saved, unchanged)', token: '(new value, hidden)',
    })
    expect(present({ token: '' }), 'a secret field with nothing stored and nothing typed').toEqual({ token: '' })
  })

  it('collapses a long unchanged run', () => {
    const before = Array.from({ length: 30 }, (_, i) => `line ${i}`).join('\n')
    const after = before.replace('line 15', 'LINE 15')
    const patch = differencePatch(before, after).split('\n')
    expect(patch).toContain('-line 15')
    expect(patch).toContain('+LINE 15')
    expect(patch.filter((l) => l === '…').length).toBe(2)
    expect(patch.length).toBeLessThan(12)
  })
})

// ── The census: every whole-document save goes through the one recovery ─────────────────────
//
// A surface that saves a whole document and does not hold the guard puts its stale copy back over
// whatever was stored since, the loss this whole contract removes; one that rebuilds the recovery
// for itself drifts from it. So every module that calls a revisioned write — an `api` method that
// sends `basedOn(…)`, or one that calls such a method — must use `useStaleWriteGuard` itself or
// through a module it imports (a viewer whose save it hands the write to), and the notice must be
// rendered there or by a module that imports it. A module may instead hand the write UP: the
// function that sends it is called by an importer that holds the guard and shows the notice (the
// theme provider's `updateCustomScheme`, called by the Design panel's guard). Which writes are
// revisioned is read from `lib/api.ts`, never listed, and each caller is placed on the route that
// reaches it, from the app's own route tables (`test/routeTables.ts`), so a page added to either
// table is counted.

/** A caller that sends a revisioned write and holds no guard of its own, and why that is right. */
const NO_GUARD_OF_ITS_OWN: Record<string, string> = {
  'pages/chat/bundledModelDownload.tsx': 'it writes the chat chain only while the chain is empty, so there is no edit of the user’s to keep or re-apply',
  'ui/widget/WidgetFrame.tsx': 'a widget holds no draft: a snapshot over a body that moved is refused as the widget’s own error',
}

/** The methods of `api` that replace a whole document over a base revision. */
function revisionedWrites(apiText: string): string[] {
  const code = withoutComments(apiText)
  const body = code.slice(code.indexOf('export const api = {'))
  const keys = [...body.matchAll(/^ {2}(\w+)\s*[:(]/gm)]
  const methods = new Map(keys.map((m, i) => [m[1], body.slice(m.index, keys[i + 1]?.index ?? body.length)]))
  const writes = new Set([...methods].filter(([, text]) => text.includes('basedOn(')).map(([name]) => name))
  for (let grew = true; grew;) {
    grew = false
    for (const [name, text] of methods) {
      if (!writes.has(name) && [...writes].some((w) => new RegExp(`\\bapi\\.${w}\\b`).test(text))) {
        writes.add(name)
        grew = true
      }
    }
  }
  return [...writes].sort()
}

/** The named functions in `code` that send one of `calls`: for each call, the nearest declaration
 *  above it that is indented less than the call's line. Empty when a call sits in none. */
function senders(code: string, calls: string[]): string[] {
  const lines = code.split('\n')
  const indent = (line: string) => line.length - line.trimStart().length
  const out = new Set<string>()
  lines.forEach((line, i) => {
    if (!calls.some((w) => new RegExp(`\\bapi\\.${w}\\b`).test(line))) return
    for (let j = i; j >= 0; j--) {
      const decl = /^\s*(?:export\s+)?(?:const|let|function|async\s+function)\s+(\w+)/.exec(lines[j])
      if (decl && (j < i ? indent(lines[j]) < indent(line) : false)) { out.add(decl[1]); return }
    }
    out.add('')
  })
  return out.has('') || !out.size ? [] : [...out]
}

interface Census {
  writes: string[]
  /** Each module that calls a revisioned write: what it calls, and the route that reaches it. */
  callers: Array<{ module: string; calls: string[]; route: string }>
  /** Each caller that breaks the contract, and how. */
  violations: string[]
}

function staleWriteCensus(tree: Tree, src: string): Census {
  const rel = (path: string) => relative(src, path).split('\\').join('/')
  const code = new Map(tree.files.map((f) => [f, withoutComments(tree.read(f))]))
  const writes = revisionedWrites(tree.read(join(src, 'lib', 'api.ts')))
  const graph = importGraph(tree)
  const importers = new Map<string, string[]>()
  for (const [from, deps] of graph) for (const dep of deps) importers.set(dep, [...(importers.get(dep) ?? []), from])
  const roots = routeRoots(tree, src)
  const shell = reach(graph, join(src, 'app', 'App.tsx'))
  const reaches = roots.map((r) => ({ route: r.route, reached: reach(graph, r.module) }))
  // A call of the hook, in any module but the hook's own (whose definition reads like one).
  const hook = join(src, 'lib', 'useStaleWriteGuard.ts')
  const holdsGuard = (m: string) => m !== hook && /\buseStaleWriteGuard\s*[<(]/.test(code.get(m) ?? '')
  const rendersNotice = (m: string) => /<StaleWriteNotice\b/.test(code.get(m) ?? '')

  const callers: Census['callers'] = []
  const violations: string[] = []
  for (const module of graph.keys()) {
    if (module === join(src, 'lib', 'api.ts')) continue
    const calls = writes.filter((w) => new RegExp(`\\bapi\\.${w}\\b`).test(code.get(module) ?? ''))
    if (!calls.length) continue
    const route = nearestRoute(reaches, module) ?? (shell.has(module) ? 'the app shell' : '')
    callers.push({ module: rel(module), calls, route })
    if (!route) violations.push(`${rel(module)}: sends ${calls.join(', ')} from no route the app renders`)
    if (rel(module) in NO_GUARD_OF_ITS_OWN) continue
    const near = [module, ...(graph.get(module) ?? [])]
    // Handed up: each function sending a write is called by an importer that guards it — one that
    // names the function without declaring a function of that name itself.
    const sent = senders(code.get(module) ?? '', calls)
    const handedUp = sent.length > 0 && sent.every((fn) => (importers.get(module) ?? []).some((up) => {
      const text = code.get(up) ?? ''
      return holdsGuard(up) && rendersNotice(up) && new RegExp(`\\b${fn}\\b`).test(text)
        && !new RegExp(`\\b(?:const|let|var|function)\\s+${fn}\\b`).test(text)
    }))
    if (handedUp) continue
    if (!near.some(holdsGuard)) violations.push(`${rel(module)}: sends ${calls.join(', ')} without useStaleWriteGuard`)
    else if (![...near, ...(importers.get(module) ?? [])].some(rendersNotice)) violations.push(`${rel(module)}: holds the guard but no StaleWriteNotice shows its refusal`)
  }
  for (const exempt of Object.keys(NO_GUARD_OF_ITS_OWN)) {
    if (!callers.some((c) => c.module === exempt)) violations.push(`${exempt}: exempted, but sends no revisioned write`)
  }
  return { writes, callers, violations }
}

const SRC = join(import.meta.dirname, '..')
const TREE: Tree = { files: filesUnder(SRC, (name) => /\.tsx?$/.test(name)), read: readSource }

describe('the census of saves that replace what they read', () => {
  const census = staleWriteCensus(TREE, SRC)

  it('reads every revisioned write from lib/api.ts', () => {
    // The control: the reader finds the writes it was written over, a wrapper among them.
    expect(census.writes).toContain('savePrompt')
    expect(census.writes, 'a method that only calls a revisioned one is revisioned too').toContain('setProjectionRules')
    expect(census.writes).toHaveLength(37)
  })

  it('every caller holds the guard, shows the notice, and is reached from a route', () => {
    expect(census.violations).toEqual([])
    expect(census.callers.map((c) => `${c.route} › ${c.module}`)).toHaveLength(41)
    // Placed by the route tables, not by a list: a settings panel on its sub-page, a page on its
    // route, onboarding on the shell.
    expect(census.callers).toContainEqual(expect.objectContaining({ module: 'pages/settings/ProjectionRulesPanel.tsx', route: 'settings/tool-output' }))
    expect(census.callers).toContainEqual(expect.objectContaining({ module: 'pages/prompts/PromptDetail.tsx', route: 'prompts' }))
    expect(census.callers).toContainEqual(expect.objectContaining({ module: 'app/onboarding/EssentialsStep.tsx', route: 'the app shell' }))
  })

  // Planted trees: the real route tables' shape, with one page each that breaks the contract.
  const planted = (pages: Record<string, string>): Tree => {
    const files: Record<string, string> = {
      // Its imports are double-quoted, so the DOM census does not read them as this file's own.
      'app/App.tsx': `const PromptsSection = lazy(() => import("../pages/prompts/PromptsSection").then((m) => ({ default: m.PromptsSection })))
        import { NotesPage } from "../pages/notes/NotesPage"
        function renderPage(active: string, r: RouteProps) {
          switch (active) {
            case 'prompts': return <PromptsSection {...r} />
            case 'notes': return <NotesPage {...r} />
          }
        }`,
      'pages/settings/SettingsPage.tsx': `import { DesignPanel } from "./DesignPanel"
        const SUBPAGES: SubPage[] = [
          { id: 'design', label: 'Design', icon: Palette, render: () => <DesignPanel /> },
        ]`,
      'pages/settings/DesignPanel.tsx': 'export function DesignPanel() { return null }',
      'lib/api.ts': `export const api = {
  savePrompt: (name: string, body: unknown, base: string) => put('/api/prompts/' + name, body, basedOn(base)),
  saveNote: (id: string, body: unknown, base: string) => api.savePrompt(id, body, base),
  readNote: (id: string) => get('/api/notes/' + id),
}`,
      'pages/prompts/PromptsSection.tsx': `export function PromptsSection() { const g = useStaleWriteGuard({ write: (v, b) => api.savePrompt('p', v, b) }); return <StaleWriteNotice guard={g} what="This prompt" /> }`,
      'pages/notes/NotesPage.tsx': 'export function NotesPage() { return null }',
      ...pages,
    }
    return { files: Object.keys(files).map((f) => join('/planted/src', f)), read: (p) => files[relative('/planted/src', p)] }
  }
  const violationsOf = (pages: Record<string, string>) => {
    const tree = planted(pages)
    const { violations } = staleWriteCensus(tree, '/planted/src')
    return violations.filter((v) => !v.includes('exempted, but'))
  }

  it('the planted tree itself is clean', () => expect(violationsOf({})).toEqual([]))

  it('names a surface that drops the guard', () => {
    expect(violationsOf({
      'pages/prompts/PromptsSection.tsx': `export function PromptsSection() { return <button onClick={() => api.savePrompt('p', {}, 'r1')} /> }`,
    })).toEqual(['pages/prompts/PromptsSection.tsx: sends savePrompt without useStaleWriteGuard'])
  })

  it('names a new page on a route that saves through a wrapper without the guard', () => {
    expect(violationsOf({
      'pages/notes/NotesPage.tsx': `export function NotesPage() { return <button onClick={() => api.saveNote('n', {}, 'r1')} /> }`,
    })).toEqual(['pages/notes/NotesPage.tsx: sends saveNote without useStaleWriteGuard'])
  })

  it('names a guard whose refusal no notice shows', () => {
    expect(violationsOf({
      'pages/notes/NotesPage.tsx': `export function NotesPage() { useStaleWriteGuard({ write: (v, b) => api.saveNote('n', v, b) }); return null }`,
    })).toEqual(['pages/notes/NotesPage.tsx: holds the guard but no StaleWriteNotice shows its refusal'])
  })

  it('a write handed up to an importer that guards it passes, and fails when that importer does not', () => {
    // A provider sends the write from a function it hands out; the page that imports it calls that
    // function from its guard, as the Design panel calls the theme provider's `updateCustomScheme`.
    const provider = `export function useNotes() {
  const saveNoteOver = async (v: unknown, b: string) => {
    await api.saveNote('n', v, b)
  }
  return { saveNoteOver }
}`
    const guarded = `import { useNotes } from "./useNotes"
      export function NotesPage() { const { saveNoteOver } = useNotes(); const g = useStaleWriteGuard({ write: saveNoteOver }); return <StaleWriteNotice guard={g} what="This note" /> }`
    expect(violationsOf({ 'pages/notes/useNotes.ts': provider, 'pages/notes/NotesPage.tsx': guarded })).toEqual([])
    const unguarded = `import { useNotes } from "./useNotes"
      export function NotesPage() { const { saveNoteOver } = useNotes(); return <button onClick={() => saveNoteOver({}, 'r1')} /> }`
    expect(violationsOf({ 'pages/notes/useNotes.ts': provider, 'pages/notes/NotesPage.tsx': unguarded }))
      .toEqual(['pages/notes/useNotes.ts: sends saveNote without useStaleWriteGuard'])
  })
})
