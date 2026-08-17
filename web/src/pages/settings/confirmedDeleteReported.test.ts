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

  it('the whole confirmed-delete family is now clean — the app-wide ratchet', () => {
    // Replaces the per-slice worklist: sweep every page for a destructive call behind a confirm whose
    // rejection is discarded. This is what stops the shape coming back somewhere new.
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const abs = join(dir, name)
        if (statSync(abs).isDirectory()) walk(abs, out)
        else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
      }
      return out
    }
    const offenders: string[] = []
    for (const abs of walk(PAGES)) {
      const src = readFileSync(abs, 'utf8')
      for (const m of src.matchAll(/api\.(delete|purge|revoke)[A-Z]\w*\(/g)) {
        if (!/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(src.slice(m.index!, m.index! + 160))) continue
        const before = src.slice(Math.max(0, m.index! - 900), m.index!)
        if (/(confirmDelete|await confirm\()/.test(before)) offenders.push(`${abs.replace(PAGES + '/', '')}: ${m[0]}`)
      }
    }
    expect(offenders, 'a confirmed destructive action may not swallow its rejection').toEqual([])
  })

  it('no OTHER settings panel swallows a CONFIRMED delete — the ratchet', () => {
    const files = readdirSync(join(PAGES, 'settings')).filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))
    expect(files.length, 'the settings panels must be discoverable').toBeGreaterThan(20)
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(join(PAGES, 'settings', f), 'utf8')
      for (const m of src.matchAll(/api\.(delete|purge|revoke)[A-Z]\w*\(/g)) {
        const chain = src.slice(m.index!, m.index! + 160)
        if (!/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain)) continue
        const before = src.slice(Math.max(0, m.index! - 900), m.index!)
        if (/(confirmDelete|await confirm\()/.test(before)) offenders.push(`${f}: ${m[0]}`)
      }
    }
    expect(offenders, 'a confirmed delete may not swallow its rejection').toEqual([])
  })
})
