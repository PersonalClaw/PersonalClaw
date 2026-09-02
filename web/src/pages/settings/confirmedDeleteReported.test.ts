import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A destructive action the user CONFIRMED must not fail silently ────────────────────────────────
//
// `saveFailureReported` owns the sibling contract for optimistic WRITES, and scopes itself out of
// "reads, deletes and confirm-flows". So this shape was **unclaimed, not settled** — and it is a
// different failure, not a milder one:
//
//   a lying control   shows a value the server refused          (that rail's case)
//   a silent delete   an action that did not happen at all      (this one)
//
// The second is arguably worse, because the user was stopped and asked to confirm it first.
//
// Measured shape in `MemoryPanel.removeSelected`, all three branches:
//
//     if (!(await confirmDelete('memory', key))) return
//     await api.deleteSemantic(key).catch(() => {})     ← swallowed
//     …
//     setSelUid(null); reloadAll()                      ← selection cleared, list refetched
//
// So a failed delete read as: dialog closes, selection vanishes, list refetches — and the memory comes
// back, with nothing said. The panel already reports its two settings writes with
// `notify(…, 'error')` 1100 lines further down, so this converges onto its own idiom rather than
// importing one.
//
// 🔑 THE POPULATION AND ITS BOUNDARY, censused rather than guessed. Fourteen swallowed destructive calls
// app-wide; **seven sit behind a confirm** and seven do not, and the confirm is what makes the silence
// indefensible:
//
//   BEHIND A CONFIRM (the family)          settings/MemoryPanel ×3   ← fixed here, complete for this file
//                                          knowledge/KnowledgeListPage  deleteKnowledgeCollection
//                                          loops/{DesignCockpit,LoopCockpit,LoopsList}  deleteULoop ×3
//   NO CONFIRM (deliberately out of scope)  deleteTerminal ×4 — cleanup on unmount, fire-and-forget
//                                          removeFromKnowledgeCollection, deleteKnowledgeAnnotation,
//                                          deleteArtifact — high-frequency, dismissal-like
//
// ── SECOND SLICE: the four sites that had no error surface at all ─────────────────────────────────
//
// The first pass fixed `MemoryPanel` and pinned the other four as still-swallowing, because they had
// `notify` 0 / `setErr` 0 — a fix there had to INTRODUCE an affordance, not rewire a `.catch`. They now
// route through `notify(…, 'error')`, the app's imperative toast (52 files already reach for it, and
// `ui/Toaster` renders errors into a `role="alert"` live region, so "the user was told" is true for
// assistive tech too, not just sighted users).
//
// 🔑 TWO OF THE FOUR WERE WORSE THAN SILENT — they performed the post-SUCCESS step anyway:
//
//   loops/DesignCockpitPage.del()   `onDeleted?.()`            closed the cockpit
//   loops/LoopCockpitPage.del()     `onDeleted() : onBack()`   navigated back
//
// So a failed delete took the user off a loop that still existed, which actively asserts it worked. Both
// now report and STAY, with the two-step arm still armed so a retry is one click. The other two
// (`LoopsListPage`, `KnowledgeListPage`) had the milder reappear-unexplained shape.
//
// 🪤 `KnowledgeListPage` LOOKED like it had a surface — a `setErr` at line 957 — but that belongs to a
// different form component further down the file and is not in scope at the delete. Grepping a file for
// "does it have an error surface" answers the wrong question; the surface has to be in scope AT the call.

const PAGES = join(process.cwd(), 'src', 'pages')
const MEM = readFileSync(join(PAGES, 'settings', 'MemoryPanel.tsx'), 'utf8')

describe('a confirmed delete reports its failure', () => {
  it('all three memory deletes are wrapped, not swallowed', () => {
    for (const call of ['deleteSemantic', 'deleteEpisodic', 'deleteLesson']) {
      expect(MEM, `${call} must still perform the delete`).toMatch(new RegExp(`api\\.${call}\\(`))
      const at = MEM.indexOf(`api.${call}(`)
      const chain = MEM.slice(at - 90, at + 160)
      expect(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain), `${call}: a silent catch hides a refused delete`).toBe(false)
      expect(/try \{ await api\./.test(chain), `${call}: the rejection must be captured`).toBe(true)
    }
  })

  it('the failure is reported with this file’s own idiom and the server’s message', () => {
    expect(MEM).toMatch(/notify\(`Couldn't delete this \$\{what\}: \$\{String\(\(e as Error\)\?\.message \|\| e\)\}`, 'error'\)/)
  })

  it('each branch names WHAT failed, not a generic "item"', () => {
    // Three kinds share one handler; a single word decides whether the message is actionable.
    for (const what of ["fail\\('memory', e\\)", "fail\\('episodic memory', e\\)", "fail\\('lesson', e\\)"])
      expect(MEM, `missing ${what}`).toMatch(new RegExp(what))
  })

  it('a failed delete KEEPS the selection so the row can be retried from', () => {
    // `fail()` deliberately does not `setSelUid(null)`. If it ever does, the user loses the item they
    // just tried to delete and the error names something no longer on screen.
    const at = MEM.indexOf('const fail = (what: string, e: unknown)')
    expect(at, 'the fail helper exists').toBeGreaterThan(-1)
    const body = MEM.slice(at, at + 260)
    expect(body, 'the selection must survive a failure').not.toMatch(/setSelUid\(null\)/)
    expect(body, 'but the list still refetches, to show the server’s truth').toMatch(/reloadAll\(\)/)
  })

  it('the happy path still clears the selection and refetches', () => {
    // A guard that only ever blocks would be worse than the bug.
    expect(MEM).toMatch(/\} else return\s*\n\s*setSelUid\(null\); reloadAll\(\)/)
  })

  it('the four second-slice sites now report instead of swallowing', () => {
    // These were PINNED as still-swallowing by the first slice. Now they must all be fixed — the same
    // list, flipped from a worklist into a ratchet.
    const slice: Array<[string, string]> = [
      [join('knowledge', 'KnowledgeListPage.tsx'), 'deleteKnowledgeCollection'],
      [join('loops', 'DesignCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopsListPage.tsx'), 'deleteULoop'],
    ]
    const swallowing: string[] = []
    for (const [rel, call] of slice) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      const at = src.indexOf(`api.${call}(`)
      expect(at, `${rel} must still perform the delete`).toBeGreaterThan(-1)
      if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(src.slice(at, at + 160))) swallowing.push(`${rel}:${call}`)
      expect(src, `${rel} must import the toast it reports through`)
        .toMatch(/import \{ notify \} from '\.\.\/\.\.\/app\/appSdk'/)
      expect(src, `${rel} must report the failure`).toMatch(/notify\(`Couldn't delete/)
    }
    expect(swallowing, 'no second-slice delete may swallow its rejection').toEqual([])
  })

  it('the two cockpits no longer navigate away on a FAILED delete', () => {
    // The worst half of the slice: they called the post-success step regardless, which asserts to the
    // user that the delete worked. The `return` in the catch is what stops that.
    for (const rel of [join('loops', 'DesignCockpitPage.tsx'), join('loops', 'LoopCockpitPage.tsx')]) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      const at = src.indexOf('api.deleteULoop(')
      const chain = src.slice(at, at + 260)
      // 🪤 NOT `[^}]*` — the notify argument is a template literal containing `${…}`, so a
      // negated-`}` class stops inside it and matches nothing. Same shape as `[^>]*` dying on an arrow
      // function. Scan lazily to the `return`.
      expect(chain, `${rel}: the failure path must not fall through to the navigation`)
        .toMatch(/catch \(e\) \{ notify\([\s\S]{0,160}?\); return \}/)
    }
  })

  // ── THIRD SLICE: the ratchet filtered by VERB, matched a NAME, and knew only ONE confirm idiom ───
  //
  // 🪤 THREE DEFECTS IN THIS RAIL, all found by turning it on a call it should have caught:
  //
  //   1. `api\.(delete|purge|revoke)[A-Z]\w*` is a VERB filter. `AuditPanel.rotate` — a confirmed
  //      rotation of the audit-log signing key — swallowed its rejection and was invisible purely
  //      because it is spelled `selRotate`. **A rail that filters by verb misses every future verb**,
  //      the sibling of the recorded "a rail that pins a whole prop string forbids every future prop".
  //      The sweep now matches the SHAPE: any `api.*` behind a confirm.
  //
  //   2. The gate was `/(confirmDelete|await confirm\()/` over the preceding 900 chars, which matches
  //      `const [confirmDelete, setConfirmDelete] = useState(false)` — a state DECLARATION. Widening to
  //      every `api.*` exposed it: `DesignCockpitPage`'s `api.project` and `api.uLoopDesignTokens` are
  //      plain reads in `loadLoop`/`loadTokens`, reported as confirmed-destructive because a boolean of
  //      that name is declared ~700 chars above. Grep proximity is not proof. Both are pinned below.
  //
  //   3. 🔑 THE ONE THAT MATTERED: **the app has TWO confirm idioms and this sweep knew one.** A dialog
  //      (`await confirm({…})`) and a two-step arm (`if (!confirmDelete) { setConfirmDelete(true);
  //      return }` — the button relabels to "Confirm delete?" and the second click commits). Every
  //      arm-gated destructive action was outside the ratchet entirely; the three in `loops/` were
  //      covered only by the hand-written slice list above, and **two more were covered by nothing at
  //      all** (`chat/SdlcProgressCard`, `knowledge/KnowledgeListPage`'s two intent deletes). None is
  //      currently swallowing, so this adds no new defect — it closes the hole a new one would land in.
  //
  // The window is still 900 chars of proximity, not scope analysis — so the vacuity floor is
  // load-bearing: if either predicate breaks, the sweep matches nothing and reads as a clean pass.

  const DIALOG_CONFIRM = /(?:await confirm\(|(?<![\w.$])confirmDelete\()/
  const ARMED_CONFIRM = /if \([^)\n]{0,70}confirm[A-Za-z]*[^)\n]{0,70}\)[^\n]{0,220}return/
  const SWALLOWED = /\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/

  const allPages = (): string[] => {
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const abs = join(dir, name)
        if (statSync(abs).isDirectory()) walk(abs, out)
        else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
      }
      return out
    }
    return walk(PAGES)
  }

  type Gated = { rel: string; line: number; call: string; idiom: 'dialog' | 'arm'; swallowed: boolean }

  // Every `api.*` call sitting behind either confirm idiom, app-wide. `=>` is neutralised first: the
  // catch bodies are arrow functions, and a bounded scan that stops at `>` matches nothing.
  const confirmGatedCalls = (): Gated[] => {
    const out: Gated[] = []
    for (const abs of allPages()) {
      const src = readFileSync(abs, 'utf8').replace(/=>/g, '⇒')
      for (const m of src.matchAll(/api\.(\w+)\(/g)) {
        const before = src.slice(Math.max(0, m.index! - 900), m.index!)
        const dialog = DIALOG_CONFIRM.test(before)
        if (!dialog && !ARMED_CONFIRM.test(before)) continue
        out.push({
          rel: abs.replace(PAGES + '/', ''),
          line: src.slice(0, m.index).split('\n').length,
          call: m[1],
          idiom: dialog ? 'dialog' : 'arm',
          swallowed: SWALLOWED.test(src.slice(m.index!, m.index! + 200)),
        })
      }
    }
    return out
  }

  it('the sweep is not vacuous — the pages, BOTH idioms, and a spread of areas', () => {
    expect(allPages().length, 'the page tree must be discoverable').toBeGreaterThan(150)
    const gated = confirmGatedCalls()
    expect(gated.filter((g) => g.idiom === 'dialog').length, 'the dialog predicate must match').toBeGreaterThan(20)
    // Without this floor, defect 3 is silently reintroduced the moment the arm regex stops matching.
    expect(gated.filter((g) => g.idiom === 'arm').length, 'the two-step-arm predicate must match too').toBeGreaterThan(3)
    expect(new Set(gated.map((g) => g.rel.split('/')[0])).size, 'and reach across areas').toBeGreaterThan(3)
  })

  it('NO confirmed action anywhere swallows its rejection — by shape, both idioms', () => {
    const offenders = confirmGatedCalls()
      .filter((g) => g.swallowed)
      .map((g) => `${g.rel}:${g.line} (api.${g.call}, ${g.idiom})`)
    expect(offenders, 'the user was stopped and asked to confirm — silence is indefensible').toEqual([])
  })

  it('the confirmed audit-log rotation the verb filter could not see now reports', () => {
    // Also must not run the post-success steps: nothing archived, so there is nothing to invalidate.
    const src = readFileSync(join(PAGES, 'settings', 'AuditPanel.tsx'), 'utf8')
    expect(src, 'the rotation must still be confirmed first').toMatch(/await confirm\(\{/)
    expect(src, 'and the rejection captured, not discarded').toMatch(/try \{\s+const res = await api\.selRotate\(\)/)
    expect(src, 'reported with the server’s own message').toMatch(/notify\(`Couldn't archive the audit log: \$\{msg\}`, 'error'\)/)
    const at = src.indexOf('api.selRotate(')
    expect(src.slice(at, at + 640), 'a failed rotation must not invalidate or reload').toMatch(/return {3}\/\/ nothing archived/)
    // And it is genuinely inside the sweep now, not merely fixed by hand.
    expect(confirmGatedCalls().some((g) => g.rel === join('settings', 'AuditPanel.tsx') && g.call === 'selRotate'))
      .toBe(true)
  })

  it('the two-step arm sites are inside the sweep — including the two no list covered', () => {
    // Pins defect 3. These were reachable by no ratchet before: the app-wide sweep only knew the dialog
    // idiom, and the slice list above names only the three in `loops/`.
    const arm = confirmGatedCalls().filter((g) => g.idiom === 'arm')
    for (const [rel, call] of [
      [join('chat', 'SdlcProgressCard.tsx'), 'deleteULoop'],
      [join('knowledge', 'KnowledgeListPage.tsx'), 'deleteKnowledgeIntent'],
      [join('loops', 'LoopsListPage.tsx'), 'deleteULoop'],
      [join('loops', 'DesignCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopCockpitPage.tsx'), 'deleteULoop'],
    ]) {
      expect(arm.some((g) => g.rel === rel && g.call === call), `${rel}:${call} must be in scope`).toBe(true)
    }
  })

  it('the gate does not fire on a read that merely sits near a confirm STATE variable', () => {
    // Pins defect 2. `DesignCockpitPage` declares `const [confirmDelete, setConfirmDelete] = useState`
    // and loads two reads below it; a name-proximity gate called both confirmed-destructive.
    const flagged = confirmGatedCalls().filter(
      (g) => g.rel === join('loops', 'DesignCockpitPage.tsx') && ['project', 'uLoopDesignTokens'].includes(g.call),
    )
    expect(flagged, 'these are reads in loadLoop/loadTokens, not confirmed deletes').toEqual([])
  })

  // The settings-only `delete|purge|revoke` sweep that used to sit here is GONE, not weakened: the
  // app-wide shape sweep walks `settings/` too and matches every verb and both idioms, so it is a
  // strict superset. `saveFailureReported`'s widened sweep covers those panels for UNconfirmed writes.
})
