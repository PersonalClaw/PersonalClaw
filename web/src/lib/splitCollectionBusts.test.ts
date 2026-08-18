import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── One collection, two namespaces, and a bust that could only ever reach one ─────────────────────
//
// Fourth and last instance of the family opened in ux-682: a key named after its READER is invisible
// to its collection's invalidation. The first three were found by chasing a specific surface. This one
// was found by asking the general question — *which collections are read under keys in more than one
// namespace?* — over all 74 collection reads in the tree. Ten came back; two were real:
//
//   api.tasks()        `tasks` (list)              + `tasks-all` (create form, **persist: true**)
//   api.modelsLoaded() `settings:models-loaded`     + `dashboard:on-this-machine`
//
// 🔴 TASKS. All three busts read `invalidateCache('tasks')` — exact-key mode, so `tasks-all` was never
// dropped by anything, ever. That key feeds `DependencyEditor`, the *only* place a task's dependencies
// are chosen, and it is `persist: true`.
//
// 🪤 AND HERE IS WHERE THIS CYCLE ALMOST SHIPPED A FALSE CLAIM. The obvious write-up is "creating a
// task and then depending on it was impossible". Driven in the browser against the PRE-FIX build, the
// just-created task **was** offered — because `useCachedData` revalidates on EVERY mount, so a missing
// bust cannot produce a durably wrong list. It produces a wrong FIRST PAINT, for the length of one
// refetch, and `persist: true` means a hard reload — the gesture a user makes when a list looks wrong —
// paints that same wrong list again before correcting itself. That is the real, smaller defect. It is
// worth fixing and it is not worth overstating. (The two sibling cycles that opened this family, #1682
// and #1686, describe their staleness as surviving a reload; by this measurement the survival is one
// revalidation window, not indefinite.)
//
// 🔴 MODELS. Two surfaces made the byte-identical `api.modelsLoaded()` read under two surface-named
// keys, and BOTH offer Unload. Each only called `refresh()` — its own key — so each left the other's
// cached copy describing freed memory as still resident, to be painted on its next mount until that
// mount's refetch landed. `OnThisMachine`'s own doc claims the two surfaces "cannot drift into
// disagreeing about which model is reclaimable"; shared derivations keep the rendering honest, but
// nothing kept the cached data honest. Fixed by sharing ONE key rather than making two keys mutually
// reachable: the reads are identical, so two keys bought nothing but the drift and a duplicate fetch.
//
// 🪤 A BUST IS ONLY LOAD-BEARING WHILE A READER IS MOUNTED. Neither pair is ever co-mounted (list vs
// `#/tasks/new`; dashboard vs `#/settings/models`), and the hook has no cross-instance subscribers —
// so no amount of invalidation propagates to a live sibling. Anyone extending this family should check
// co-mounting first: that is where a missing bust becomes durable rather than transient.
//
// 🪤 THE GENERAL CHECK IS NOT "TWO KEYS" — IT IS "TWO KEYS OVER THE SAME CONTENT". `api.uLoops()` is
// also read under two namespaces (`loops` and `code:projects`) and is NOT a defect: the two keys hold
// disjoint filtered subsets (`kind !== 'code'` vs code-only), so a mutation in one cannot staleten the
// other. Converging those would have destroyed a real distinction. Asserted below so no later pass
// "fixes" it.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
  const p = join(d, n)
  if (statSync(p).isDirectory()) return walk(p)
  return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
})
const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the task collection is busted as a collection, not as one key', () => {
  const list = () => codeOf('pages/tasks/TasksListPage.tsx')

  it('every bust in the list page is prefix mode', () => {
    const code = list()
    const busts = code.match(/invalidateCache\('tasks'[^)]*\)/g) ?? []
    expect(busts.length, 'the loader plus both LoadError retries').toBe(3)
    for (const b of busts) expect(b, 'exact-key mode cannot reach `tasks-all`').toMatch(/'tasks', true\)/)
  })

  it('creating a task busts the collection — it used to bust nothing at all', () => {
    const code = codeOf('pages/tasks/TaskCreatePage.tsx')
    const at = code.indexOf('await api.createTask(')
    expect(at, 'the create must still be here').toBeGreaterThan(-1)
    expect(code.slice(at, at + 300), "the task you just made is the one you want to depend on")
      .toMatch(/invalidateCache\('tasks', true\)/)
  })

  it('the prefix reaches both keys and clears the persisted copy', async () => {
    const { invalidateCache, writeCache, peekCache } = await import('./useCachedData')
    writeCache('tasks', ['list'])
    writeCache('tasks-all', ['dependency picker'])
    writeCache('triggers', ['untouched'])

    invalidateCache('tasks', true)

    expect(peekCache('tasks')).toBeUndefined()
    // 🔑 The one nothing in the tree could reach before.
    expect(peekCache('tasks-all'), "the dependency picker's copy is dropped").toBeUndefined()
    expect(peekCache('triggers'), 'an unrelated collection must survive').toEqual(['untouched'])
  })

  it('persist:true means the wrong list is repainted after a reload — so storage is cleared too', async () => {
    const { invalidateCache, writeCache } = await import('./useCachedData')
    writeCache('tasks-all', ['stale'])
    invalidateCache('tasks', true)
    const leftovers = Object.keys(sessionStorage).filter((k) => k.includes('tasks'))
    expect(leftovers, `sessionStorage still holds ${leftovers.join(', ')}`).toEqual([])
  })
})

describe('both readers of the loaded-model set share one key', () => {
  it('neither key is named after its surface any more', () => {
    const panel = codeOf('pages/settings/ModelsPanel.tsx')
    const widget = codeOf('pages/dashboard/widgets/OnThisMachine.tsx')
    expect(panel).toMatch(/useCachedData\('models:loaded'/)
    expect(widget).toMatch(/useCachedData\('models:loaded'/)
    expect(panel + widget, 'the surface-named keys are gone')
      .not.toMatch(/settings:models-loaded|dashboard:on-this-machine/)
  })

  it('both Unload paths bust it, not just their own hook', () => {
    for (const rel of ['pages/settings/ModelsPanel.tsx', 'pages/dashboard/widgets/OnThisMachine.tsx']) {
      const code = codeOf(rel)
      const at = code.indexOf('await api.unloadModelProvider(')
      expect(at, `${rel} must still unload`).toBeGreaterThan(-1)
      // 🪤 Asserted per call site, not tree-wide: `refresh()` alone is the exact bug, and it reads as
      // a complete handler. The two surfaces are never mounted together, so nothing can notify the
      // other's hook — the shared key is what makes its next mount correct.
      expect(code.slice(at, at + 420), `${rel}: refresh() only refetches this surface`)
        .toMatch(/invalidateCache\('models:loaded'\)/)
    }
  })
})

describe('the general check, so the fifth instance is caught by a test', () => {
  it('no collection is read under keys in two different namespaces', () => {
    // The question that found this cycle's two defects. A namespace is the segment before the first
    // `:` (or the whole key when there is none), so `tasks` and `tasks-all` count as one and
    // `settings:x` vs `dashboard:y` do not.
    const byCall = new Map<string, Set<string>>()
    let reads = 0
    for (const abs of walk(SRC)) {
      const code = codeOf(abs.slice(SRC.length + 1))
      for (const m of code.matchAll(/useCachedData(?:<[^>]*>)?\(\s*'([^']+)'\s*,\s*([\s\S]{0,120}?)\)\s*(?:,|\))/g)) {
        const [, key, body] = m
        const call = body.match(/api\.(\w+)\(/)?.[1]
        if (!call) continue
        reads++
        const ns = key.includes(':') ? key.slice(0, key.indexOf(':')) : key.replace(/-.*$/, '')
        if (!byCall.has(call)) byCall.set(call, new Set())
        byCall.get(call)!.add(ns)
      }
    }
    // Vacuity floor: if the matcher stops resolving reads this test silently passes on nothing.
    expect(reads, 'the sweep must actually have found the collection reads').toBeGreaterThan(40)

    // `uLoops` is the recorded DISTINCTION: two keys over disjoint subsets (see the test below).
    const KNOWN_DISTINCTIONS = new Set([
      'uLoops',              // disjoint filtered subsets — see the test below
      'system',              // `.platform` cannot change while the tab is open; no writer exists
      'modelProviderTypes',  // a code-shipped registry; no writer exists
    ])

    // All five remaining config/registry collections were judged in a later pass. Two came off the
    // list; three stay, each for a stated reason:
    //
    //   · system, modelProviderTypes — REMOVED (distinctions, confirmed by tracing writers). No
    //     mutation path exists for either: `system:platform` reads a host's platform string, which
    //     cannot change while the tab is open, and `modelProviderTypes` is a code-shipped registry of
    //     provider TYPES. Nothing can staleten a reader, so the split costs nothing. They are named in
    //     KNOWN_DISTINCTIONS below rather than left pending.
    //   · appCatalog — real: onboarding installs apps, and `app-catalog` (persist:true) is a separate
    //     key. Its own cycle.
    //   · dashboardConfig, personalclawConfig — real, and NOT separable. `settings:chat` is a
    //     COMPOSITE read (`Promise.all([api.dashboardConfig(), api.personalclawConfig()])`), so it
    //     belongs to BOTH collections and can sit in neither one's namespace. Converging one
    //     collection alone would leave the composite half-covered while looking handled, so these two
    //     must move together in one cycle. The sharpest instance is fixed below in the meantime.
    //
    // 🔑 This is a RATCHET, not an allowlist: an entry may only ever be removed. A collection that
    // starts splitting across namespaces is not on the list, so it fails here.
    const PENDING_JUDGMENT = new Set([
      'appCatalog', 'dashboardConfig', 'personalclawConfig',
    ])
    const split = [...byCall].filter(([c, ns]) => ns.size > 1 && !KNOWN_DISTINCTIONS.has(c))
      .map(([c]) => c)

    const unexpected = split.filter((c) => !PENDING_JUDGMENT.has(c))
    expect(unexpected, `these newly read one collection under two namespaces:\n${unexpected.join('\n')}`)
      .toEqual([])
    // The two this cycle closed must never come back — the ratchet direction, asserted.
    expect(split, 'the task collection is one namespace again').not.toContain('tasks')
    expect(split, 'the loaded-model set is one key again').not.toContain('modelsLoaded')
    // And the list may not grow silently: a stale entry means someone judged it without pruning.
    expect(split.length, `PENDING_JUDGMENT is stale — prune the entries that are now single-namespace`)
      .toBe(PENDING_JUDGMENT.size)
  })

  it('the loops split stays split — it encodes disjoint subsets', () => {
    // Not laziness: `loops` holds the non-code loops and `code:projects` the code ones, so neither
    // can go stale from a mutation in the other. Flattening them would merge two lists that are
    // deliberately different.
    const code = codeOf('pages/loops/LoopsListPage.tsx')
    expect(code, 'the non-code filter is what makes the two keys disjoint')
      .toMatch(/useCachedData<GoalLoop\[\]>\('loops'[\s\S]{0,160}?kind !== 'code'/)
  })
})

describe('the sharpest instance in the un-separable pair, fixed ahead of it', () => {
  it("saving a chat setting busts the key that decides how streamed text reveals", () => {
    // `stream_reveal` is written on `#/settings/chat` and consumed by ChatPage under its own
    // `chat:stream-reveal` key, which NOTHING invalidated. Unlike the rest of this family that is not
    // a display value — it changes behaviour — and the key is `persist: true`, so the pre-change value
    // survived a reload and was used again.
    const code = codeOf('pages/settings/ChatPanel.tsx')
    const writers = [...code.matchAll(/api\.saveDashboardConfig\(patch\)/g)]
    expect(writers.length, 'both dashboard-config writers in this panel').toBe(2)
    for (const w of writers) {
      expect(code.slice(w.index!, w.index! + 420), 'each writer must reach the consumer')
        .toMatch(/invalidateCache\('chat:stream-reveal'\)/)
    }
  })

  it('`settings:chat` is a COMPOSITE, which is why the pair cannot be converged one at a time', () => {
    // Pinned so a later pass does not move this key into one collection's namespace: it reads TWO
    // collections, and naming it after either one makes the other's writers miss it.
    const code = codeOf('pages/settings/ChatPanel.tsx')
    const at = code.indexOf("useCachedData('settings:chat'")
    expect(at, 'the composite read must still be here').toBeGreaterThan(-1)
    const seg = code.slice(at, at + 600)
    expect(seg).toMatch(/api\.dashboardConfig\(\)/)
    expect(seg).toMatch(/api\.personalclawConfig\(\)/)
  })
})
