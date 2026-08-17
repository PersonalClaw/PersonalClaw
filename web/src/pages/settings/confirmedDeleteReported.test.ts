import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
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
// 🪤 WHY ONLY MemoryPanel THIS TIME, stated so the next pass does not read it as a half-done sweep: the
// other four sites have **no error surface at all** (`notify` 0, `setErr` 0 in the three loops files), so
// converging them means introducing a mechanism, not removing drift. That is a bigger call and belongs in
// its own change. `MemoryPanel.removeSelected` is a complete pass over a coherent subset: one function,
// three sibling branches, one idiom already in the file.

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

  it('the four sites left for the next slice are still swallowing — the scope claim', () => {
    // The vacuity floor for the boundary in the header. If one of these gains an error surface elsewhere,
    // this list is stale and the "no error surface at all" reasoning needs re-checking rather than
    // silently passing. If one is FIXED, delete it from here deliberately.
    const remaining: Array<[string, string]> = [
      [join('knowledge', 'KnowledgeListPage.tsx'), 'deleteKnowledgeCollection'],
      [join('loops', 'DesignCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopsListPage.tsx'), 'deleteULoop'],
    ]
    const still: string[] = []
    for (const [rel, call] of remaining) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      const at = src.indexOf(`api.${call}(`)
      if (at < 0) continue
      if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(src.slice(at, at + 160))) still.push(`${rel}:${call}`)
    }
    expect(still.length, 'the next slice is still there, as the header says').toBe(4)
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
