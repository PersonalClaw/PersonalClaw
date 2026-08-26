import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { CACHE_NAMESPACES, namespaceOf } from './keys'

// ── The completeness ratchets for ONE data layer ───────────────────────────────────────────────
//
// DSC-14's claim is not "a better hook exists" — it is that there is now exactly ONE data layer and
// the old one is gone. That claim rots the moment a second cache reappears, so it is held by count
// here rather than by intention in a doc.
//
//   §1  the deleted helper stays deleted            (any call site → red)
//   §2  every literal cache key's namespace is declared in `keys.ts`
//   §3  the remaining hand-rolled server-data caches are a FIXED, NAMED list that may only shrink
//   §4  the stale-paint label's adoption may only grow, and its named surfaces stay named
//
// 🪤 EVERY SCAN HERE STRIPS COMMENTS FIRST. This file's own siblings quote the deleted helper's
// name and the pre-fix idiom in prose — deliberately, because the contrast is the documentation —
// and a text scan that counts those is a rail measuring itself. Each census below also carries a
// VACUITY assertion (it must find a known-present thing), because a regex that matches nothing
// reads exactly like a clean tree.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) ? [p] : []
  })

const isTest = (p: string) => /\.(test|doc)\./.test(p)
const rel = (p: string) => p.slice(SRC.length + 1)
/** Source with block and line comments removed. */
const codeOf = (p: string) =>
  readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

const PRODUCTION = walk(SRC).filter((p) => !isTest(p))
const EVERY = walk(SRC)

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('§1 the deleted helper stays deleted', () => {
  // The four names the old module exported, as CALLS, plus its module path as an import.
  const GONE = [
    /\buseCachedData\s*[<(]/,
    /\binvalidateCache\s*\(/,
    /\bpeekCache\s*\(/,
    /\bwriteCache\s*\(/,
    /from '[^']*lib\/useCachedData'/,
  ]

  it('has no call site anywhere in the tree — tests and docs included', () => {
    const hits: string[] = []
    for (const p of EVERY) {
      const code = codeOf(p)
      for (const re of GONE) if (re.test(code)) hits.push(`${rel(p)} :: ${re.source}`)
    }
    // The migration's completeness measure. At authoring the atom counted 123 files reaching for
    // the helper; the census on the branch point measured 124. All 124 moved, the module was
    // deleted, and this is what stops a 125th appearing.
    expect(hits, 'a call site of the deleted helper reappeared').toEqual([])
  })

  it('and the module file itself is gone', () => {
    expect(() => readFileSync(join(SRC, 'lib', 'useCachedData.ts'), 'utf8'))
      .toThrow()
  })

  it('VACUITY: the same scan finds the REPLACEMENT everywhere, so it is not matching nothing', () => {
    // Measured on this branch: 124 files REFERENCED the deleted helper (the count the atom sized
    // itself on, taken with a plain grep that also sees prose); 100 files reference the replacement
    // in CODE after comments are stripped, of which 71 are production modules and the rest are the
    // tests that mock or scan them. The gap is exactly the prose: ~24 of the 124 named the helper
    // only in a comment describing a defect, and those comments now describe it in the past tense.
    // Floors, not equalities, so adding a surface is not a baseline edit.
    const adopters = EVERY.filter((p) => /\buseQuery\b/.test(codeOf(p)) && !p.includes('app/useQueryState'))
    expect(adopters.length, 'the replacement must be everywhere the old helper was').toBeGreaterThanOrEqual(95)
    const prod = PRODUCTION.filter((p) => /\buseQuery\s*[<(]/.test(codeOf(p)) && !p.includes('app/useQueryState'))
    expect(prod.length, 'and it must be the production surfaces, not only their tests').toBeGreaterThanOrEqual(68)
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('§2 every cache key namespace is declared', () => {
  /** Every string literal handed to the layer as a key, plus module-level key constants resolved
   *  to their value — `learning:*` keys are built by `pages/learning/proposalCache.ts`, so a
   *  census of literal first arguments alone would silently miss a whole namespace. That is not
   *  hypothetical: it happened during this migration and `learning` went undeclared until a test
   *  reddened on the always-stale fallback. */
  const keyLiterals = (): { key: string; at: string }[] => {
    const out: { key: string; at: string }[] = []
    const indirect = new Set<string>()
    for (const p of PRODUCTION) {
      const code = codeOf(p)
      for (const m of code.matchAll(/(?:useQuery(?:<[^>]*>)?|writeQuery|peekQuery|peekEntry|invalidateKeys)\(\s*(['"`])([A-Za-z][\w:-]*)/g)) {
        out.push({ key: m[2], at: rel(p) })
      }
      // A key handed in through an IDENTIFIER rather than a literal — `useQuery(WEEK_KEY, …)`.
      // Resolved by name against `const NAME = '…'` anywhere in the tree, and ONLY for names that
      // a layer call actually passes: scanning every SCREAMING_CASE constant instead swept up
      // `NAV_COLLAPSED_KEY` and six other UI-preference storage keys, which are not cache keys and
      // whose namespaces must never be declared here.
      for (const m of code.matchAll(/(?:useQuery(?:<[^>]*>)?|writeQuery|peekQuery|peekEntry|invalidateKeys)\(\s*([A-Z][A-Z0-9_]*)\b/g)) {
        indirect.add(m[1])
      }
    }
    for (const p of PRODUCTION) {
      const code = codeOf(p)
      for (const name of indirect) {
        const m = code.match(new RegExp(`const ${name} = '([A-Za-z][\\w:-]*)'`))
        if (m) out.push({ key: m[1], at: rel(p) })
      }
    }
    return out
  }

  it('resolves the keys passed by NAME too — VACUITY on the indirection', () => {
    // `learning` reaches the layer only through `proposalCache.ts`'s exported constants. It went
    // undeclared during this migration for exactly that reason, and the always-stale fallback is
    // what surfaced it — via a red test, three files away. The census has to follow the name.
    expect(keyLiterals().map((k) => k.key)).toContain('learning:week')
  })

  it('finds a substantial population — VACUITY', () => {
    const found = keyLiterals()
    expect(found.length, 'the key census must actually find keys').toBeGreaterThan(120)
    expect(new Set(found.map((k) => namespaceOf(k.key))).size).toBeGreaterThan(25)
  })

  it('every namespace in use has a declared freshness policy', () => {
    const undeclared = keyLiterals()
      .filter((k) => !(namespaceOf(k.key) in CACHE_NAMESPACES))
      .map((k) => `${k.key}  (${k.at})`)
    // An undeclared namespace falls back to "always stale", which is safe but means the surface
    // wears a label forever. Declaring it is one line and a decision about the data.
    expect([...new Set(undeclared)], 'declare these in lib/data/keys.ts').toEqual([])
  })

  it('and nothing is declared that no key uses', () => {
    const used = new Set(keyLiterals().map((k) => namespaceOf(k.key)))
    const dead = Object.keys(CACHE_NAMESPACES).filter((n) => !used.has(n))
    // A registry that accumulates entries for namespaces nobody reads is the same rot as a dead
    // allowlist: it stops describing the app and starts describing its history.
    expect(dead, 'remove these from CACHE_NAMESPACES, or use them').toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('§3 the remaining hand-rolled server-data caches are a shrinking, named list', () => {
  // The atom's own words: "a ratchet counts remaining direct call sites and fails on a rise".
  // §1 makes the old HELPER's count zero. This is the harder half — the pattern the helper was
  // itself an instance of: a module that caches server responses in `sessionStorage` under its own
  // prefix, with its own reader, writer and (if you are lucky) its own invalidation.
  //
  // Two of those existed on the branch point and are NOT here any more:
  //   pages/ChatPage.tsx      `chat-detail:` — transcript seed, no age recorded. Now
  //                           `chat:detail:<key>` in the shared store, seeded via fresh-only
  //                           `peekQuery`, and reachable by `invalidateKeys('chat:', true)`.
  //   lib/useCachedData.ts    `cache:` — the helper itself, deleted.
  //
  // One remains, deliberately, and this is the honest accounting of it.
  const REMAINING: { file: string; why: string }[] = [
    {
      file: 'pages/files/filesData.ts',
      why: 'useDirCache: a per-PATH listing cache with hierarchical subtree invalidation, a 400-path'
        + ' bound, and per-path generation guards so a load that started before an invalidate cannot'
        + ' write back. The shared layer has none of those three, and inventing them inside it for one'
        + ' consumer would be the wrong trade. It is named here rather than quietly excluded: it still'
        + ' paints a stale listing with no label, which is the same defect class, one surface wide.',
    },
  ]

  /** Modules that write to web storage AND talk to the api, but store a UI PREFERENCE rather than
   *  server data. Named, not silently excluded, so a NEW storage writer has to be classified on
   *  purpose — either it lands here with a reason, or §3 fails and it is a second cache. */
  const PREFERENCE_STORES: Record<string, string> = {
    'app/App.tsx': 'nav rail collapsed',
    'app/appearance.tsx': 'per-scheme appearance overrides',
    'pages/tasks/TasksListPage.tsx': 'view / sort / scope choice',
    'pages/terminal/TerminalPage.tsx': 'user-renamed terminal tab labels',
    'pages/files/FilesSection.tsx': 'which files tab was last open',
    'pages/files/browse/useFileTabs.ts': 'the set of open file tabs and the active one',
    'pages/loops/DesignCockpitPage.tsx': 'canvas card order within one loop',
    // MOBILE-COMPANION MC-5. The detector's own discriminator is "did the stored value come
    // from `api.*` in this module" — and here it did not: the id is MINTED locally
    // (`Math.random()`) and identifies this browser profile to the push-subscribe route. No
    // server response is cached, so there is nothing that could paint stale.
    'app/pushClient.ts': "this browser profile's own push device id",
  }

  /** A module that writes server data into web storage under a cache-ish prefix of its own. */
  const handRolled = () => PRODUCTION.filter((p) => {
    const code = codeOf(p)
    if (!/(session|local)Storage\.setItem/.test(code)) return false
    if (!/(session|local)Storage\.getItem/.test(code)) return false
    // A UI PREFERENCE is not a data cache: panel widths, sort order, theme, open tabs. The
    // discriminator is whether the stored value came from `api.*` in this module.
    return /\bapi\.\w+\(/.test(code) || /\bFsEntry\b|\bChatDetail\b/.test(code)
  }).map(rel).filter((r) => !(r in PREFERENCE_STORES))

  it('VACUITY: the detector still recognises the shape it is counting', () => {
    // The one known instance must be found, or this whole section is a rail matching nothing.
    expect(handRolled(), 'the detector found no hand-rolled cache at all').toContain('pages/files/filesData.ts')
  })

  it('the list is EXACTLY the named remainder — a new one fails, and removing one fails too', () => {
    expect(handRolled().sort()).toEqual(REMAINING.map((r) => r.file).sort())
  })

  it('every named preference store still exists — the allowlist cannot rot silently', () => {
    const all = PRODUCTION.map(rel)
    for (const f of Object.keys(PREFERENCE_STORES)) {
      expect(all, `${f} is allowlisted as a preference store but no longer exists`).toContain(f)
    }
  })

  it('each remaining entry states WHY, at length', () => {
    for (const r of REMAINING) {
      expect(r.why.length, `${r.file} needs a real reason, not a shrug`).toBeGreaterThan(120)
    }
  })

  it('the two that were converged are gone for good', () => {
    const chat = codeOf(join(SRC, 'pages', 'ChatPage.tsx'))
    expect(chat, 'the private chat-detail prefix must not come back').not.toMatch(/'chat-detail:'/)
    expect(chat, 'and the transcript seed goes through the shared store').toMatch(/peekQuery</)
  })
})

// ─────────────────────────────────────────────────────────────────────────────────────────────
describe('§4 the stale-paint label is adopted and its adoption only grows', () => {
  const adopters = () => PRODUCTION.filter((p) => /<StaleNotice\b/.test(codeOf(p))).map(rel)
  const passesStale = () => PRODUCTION.filter((p) => /\bstale=\{/.test(codeOf(p))).map(rel)

  it('the shared primitives carry it, so their consumers inherit it', () => {
    // Two chokepoints rather than N call sites: the list-page controls bar and the settings bento
    // card. One edit each, and every surface that already routes through them gets the label.
    expect(adopters()).toEqual(expect.arrayContaining([
      'ui/ListControls.tsx',
      'pages/settings/bento.tsx',
    ]))
  })

  it('the surfaces that PASS a stale flag are a growing set, floor pinned', () => {
    // Same ratchet shape DSC-13 used for its windowing adoption: a count that may only rise.
    // 24 of the 28 settings bento tiles plus three list surfaces at the time of writing; the four
    // remaining tiles read no server data at all (identity, appearance, log level, a static card).
    const n = passesStale().length
    expect(n, 'a surface stopped labelling its stale paint').toBeGreaterThanOrEqual(4)
  })

  it('and the named list surfaces label theirs BY NAME', () => {
    const named = [
      'pages/inbox/InboxPage.tsx',
      'pages/knowledge/KnowledgeListPage.tsx',
      'pages/workflows/WorkflowsListPage.tsx',
      'pages/settings/settingsWidgets.tsx',
    ]
    expect(passesStale()).toEqual(expect.arrayContaining(named))
  })

  it('nothing keys a freshness label on `loading` or `revalidating`', () => {
    // 🪤 The measured trap. `loading` is false the moment anything is cached, so a label keyed on
    // it looks right and never fires; `revalidating` is true on EVERY mount, so a label keyed on
    // it fires over genuinely fresh data. Only `stale` answers "is what you see current?".
    const wrong: string[] = []
    for (const p of PRODUCTION) {
      const code = codeOf(p)
      for (const m of code.matchAll(/stale=\{([^}]*)\}/g)) {
        const expr = m[1]
        // `stale={!loading && !!stale}` is CORRECT and is what `BentoCard` does — a first load has
        // nothing on screen to be stale about, so the label is suppressed while loading. What is
        // wrong is DERIVING the label from those flags, i.e. an expression that mentions them and
        // never mentions staleness at all.
        if (/\bloading\b|\brevalidating\b/.test(expr) && !/stale/i.test(expr)) {
          wrong.push(`${rel(p)}: stale={${expr}}`)
        }
      }
    }
    expect(wrong, 'pass `stale`, not `loading`/`revalidating`').toEqual([])
  })
})
