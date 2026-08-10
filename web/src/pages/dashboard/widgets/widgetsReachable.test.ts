import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── Every widget module must be reachable from somewhere ──────────────────────
//
// This started as a dedup: `MemoryWidget` and `KnowledgeWidget` each declared a private `Stat`,
// byte-identical across all 8 lines (their only difference was annotating `icon` as
// `typeof Layers` vs `typeof Boxes` — both just `LucideIcon`, each file having grabbed whichever
// icon it happened to import). Lifting it into `kit.tsx` was straightforward and HTML-identical.
//
// Then the live check refused to render either widget, and the reason was not a data gap:
// **nothing imports them.** `DashboardPage` mounts `TasksWidget` and `ScheduleWidget`; these two
// have no importer, no lazy loader, no registry entry, and no test. Their strings ("Memory Pulse",
// "Knowledge Pulse") do not appear in the built bundle at all — Rollup had been tree-shaking them
// out of every release. `git log` shows they arrived unreferenced in the initial public commit and
// were never wired up.
//
// So the honest fix was deletion, not deduplication: the repo's clean-break tenet is explicit that
// dead code does not stay, and deduplicating two files nobody renders is tidying a room no one
// enters. Both were removed; typecheck and the full suite stayed green, which is the proof nothing
// referenced them.
//
// This test is what stops the next unreferenced widget from sitting there for another release. It
// is deliberately a REACHABILITY check rather than a dedup rail — the `Stat` duplication was a
// symptom, and a rail against copied helpers would have let me "fix" it while leaving the real
// problem in place.

const WIDGETS = join(process.cwd(), 'src/pages/dashboard/widgets')
const SRC = join(process.cwd(), 'src')

/** Every source file under src/, so a reference from anywhere counts. */
function allSources(): Array<{ path: string; text: string }> {
  const out: Array<{ path: string; text: string }> = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!/\.tsx?$/.test(e.name)) continue
      out.push({ path: p, text: readFileSync(p, 'utf8') })
    }
  }
  walk(SRC)
  return out
}

describe('dashboard widget modules', () => {
  const sources = allSources()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(sources.length).toBeGreaterThan(100)
    expect(sources.some((s) => s.path.endsWith(join('widgets', 'kit.tsx')))).toBe(true)
  })

  it('every module is referenced from outside its own file', () => {
    const modules = readdirSync(WIDGETS)
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
    // Guard the guard: if the directory listing ever comes back empty the loop below passes
    // vacuously, which is how a reachability rail quietly stops guarding anything.
    expect(modules.length).toBeGreaterThan(5)

    const orphans: string[] = []
    for (const file of modules) {
      const name = file.replace(/\.tsx?$/, '')
      const own = join(WIDGETS, file)
      const referenced = sources.some((s) =>
        s.path !== own && !/\.test\.tsx?$/.test(s.path) &&
        new RegExp(`\\b${name}\\b`).test(s.text))
      if (!referenced) orphans.push(file)
    }
    expect(
      orphans,
      'These widget modules have no importer anywhere in src/, so they are tree-shaken out of ' +
        'every build — dead code that still costs review attention and drifts against its live ' +
        `siblings:\n  ${orphans.join('\n  ')}`,
    ).toEqual([])
  })

  it('the widgets DashboardPage mounts are among them', () => {
    // The counterpart direction: this rail must not pass because the directory went empty or the
    // dashboard stopped mounting anything. Naming two known-live widgets keeps it anchored.
    const page = readFileSync(join(SRC, 'pages/dashboard/DashboardPage.tsx'), 'utf8')
    for (const w of ['TasksWidget', 'ScheduleWidget']) {
      expect(page, `DashboardPage should still mount ${w}`).toMatch(new RegExp(`\\b${w}\\b`))
      expect(readdirSync(WIDGETS)).toContain(`${w}.tsx`)
    }
  })
})
