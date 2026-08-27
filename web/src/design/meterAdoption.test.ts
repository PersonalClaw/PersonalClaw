import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── A determinate progress bar goes through ui/Meter ──────────────────────────
//
// `role="progressbar"` existed in exactly three files, ALL of them under `src/ui/` —
// `Meter.tsx`, `ProgressRing.tsx`, `WavyProgress.tsx`. Two earlier cycles hardened the
// latter two. Meanwhile ELEVEN page-level determinate bars re-typed the same track by
// hand (a fixed height + `overflow-hidden` + a rounded pill + a surface fill, wrapping an
// `h-full` child whose `width` is a percentage) with **no role, no aria-valuemin/max, and
// no aria-valuenow**.
//
// That is why neither earlier cycle reached them, and it is the whole trap this rail
// exists to hold shut: axe's `aria-progressbar-name` fires on a progressbar that has no
// NAME. A div with no role is not a progressbar, so axe has nothing to fire on and the
// surface audits clean while a sighted user reads a fill and a screen-reader user gets
// silence. **The absence of a role hides the absence of a name.** No amount of axe
// coverage finds these; only a source rail does.
//
// Measured on the live gateway (`#/dashboard`, system tile, seeded home) before the fix:
//   the CPU/Memory/Disk bars → role=null, aria-valuenow=null, accessible name ""
// after: role="progressbar", aria-valuenow="8", aria-valuemin="0", aria-valuemax="100",
//   name "CPU usage".
//
// SCOPE — deliberately narrow, keyed on the TRACK+FILL pair rather than on "any div with
// a percentage width", because a rail that cries wolf gets ignored. Four call sites are
// listed as owner-deferred rather than silently excluded, each for a stated reason:
//
//  · `pages/settings/ModelsPanel.tsx`  — 6px track on `bg-surface-container`, not
//    `bg-surface-high`. Adopting Meter would retint the track, so it moves pixels.
//  · `pages/tasks/TaskDetail.tsx`      — 6px track that IS byte-equivalent to Meter's
//    default. Held back only because the brief scoped it out; it is a one-line follow-up.
//  · `ui/genui/components.tsx`         — 8px track. Meter has no 8px rung and inventing
//    one for a single model-authored widget would be speculative API.
//  · `pages/loops/RunProgress.tsx`     — square corners, `duration-500`, and its only
//    conveyance of progress is fill width plus a `title` on a non-interactive div (which
//    is not an accessible name at all). It needs a shape decision, not an adoption.
//
// The rail's job is that this list does not GROW. Adding an entry to it is the forbidden
// move; deleting one by adopting Meter is the intended one.

const SRC = join(process.cwd(), 'src')

/** Every `.tsx` under src/, excluding tests. */
function sourceFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!/\.tsx$/.test(e.name) || /\.test\.tsx$/.test(e.name)) continue
      out.push(p)
    }
  }
  walk(SRC)
  return out
}

/** The primitives that legitimately own a progressbar role. */
const PRIMITIVES = [
  join('ui', 'Meter.tsx'),
  join('ui', 'ProgressRing.tsx'),
  join('ui', 'WavyProgress.tsx'),
]

/** Owner-deferred hand-rolled bars, each with a stated reason above. NEVER add to this. */
const DEFERRED = [
  join('pages', 'settings', 'ModelsPanel.tsx'),
  join('pages', 'tasks', 'TaskDetail.tsx'),
  join('pages', 'loops', 'RunProgress.tsx'),
  join('ui', 'genui', 'components.tsx'),
]

/** A bar TRACK: a small fixed height, a background, and a clip or a rounding. */
const TRACK = /\bh-(?:0\.5|1|1\.5|2|2\.5|3|\[\d+px\])\b/
/** The FILL that rides inside it: full-height, driven by a percentage width. */
const FILL = /\bh-full\b/

/** Files that hand-roll a determinate bar, as `path:line`. */
function handRolled(files: string[]): string[] {
  const hits: string[] = []
  for (const f of files) {
    const lines = readFileSync(f, 'utf8').split('\n')
    lines.forEach((ln, i) => {
      const m = ln.match(/className=(?:"([^"]*)"|\{`([^`]*)`\}|\{cx\(([^)]*)\))/)
      const cls = m ? (m[1] ?? m[2] ?? m[3] ?? '') : ''
      if (!cls || !TRACK.test(cls) || !/\bbg-/.test(cls)) return
      // The element as a whole — the fill usually sits on the next line or two.
      const win = lines.slice(i, i + 4).join('\n')
      if (!FILL.test(win)) return
      if (!/width/.test(win)) return
      hits.push(`${f.slice(SRC.length + 1)}:${i + 1}`)
    })
  }
  return hits
}

describe('the determinate progress primitive', () => {
  const files = sourceFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(50)
    // Vacuity floor: the detector must still FIRE on the four deferred sites. If a future
    // refactor breaks the regex, this fails loudly instead of the sweep reading clean.
    const deferredHits = handRolled(files.filter((f) => DEFERRED.some((d) => f.endsWith(d))))
    expect(
      DEFERRED.filter((d) => !deferredHits.some((h) => h.startsWith(d.replace(/\\/g, '/')) || join(SRC, h.split(':')[0]).endsWith(d))),
      'the track+fill detector no longer matches a known hand-rolled bar — the rail has gone vacuous',
    ).toEqual([])
    expect(deferredHits.length).toBeGreaterThanOrEqual(DEFERRED.length)
    // The allowlist itself is pinned. Every allowlist rail has the same escape hatch —
    // add your new offender to the list and the sweep goes green — so the count is
    // asserted, not merely documented. Adopting Meter at a deferred site LOWERS this.
    expect(DEFERRED.length, 'never add to DEFERRED; adopt ui/Meter instead').toBe(4)
  })

  it('has no hand-rolled determinate bar outside the primitives and the deferred four', () => {
    const scanned = files.filter(
      (f) => !PRIMITIVES.some((p) => f.endsWith(p)) && !DEFERRED.some((d) => f.endsWith(d)),
    )
    const offenders = handRolled(scanned)
    expect(
      offenders,
      'A determinate progress bar must render through `ui/Meter` (or ProgressRing/WavyProgress). ' +
        'A hand-rolled track ships with no role and no aria-valuenow, so axe cannot even see ' +
        'that the progressbar is unnamed — the missing role HIDES the missing name:\n  ' +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the seven migrated call sites reach for the primitive', () => {
    // Named explicitly, so a revert of any one fails here by name and not by a generic sweep.
    const adopters: Array<[string, RegExp]> = [
      [join('pages', 'ChatPage.tsx'), /label=\{`Uploading \$\{u\.name\}`\}/],
      [join('pages', 'ChatPage.tsx'), /label="Prompt budget used by attached knowledge"/],
      [join('pages', 'chat', 'WorkflowProgressCard.tsx'), /steps done`\}/],
      [join('pages', 'files', 'FilesSection.tsx'), /label=\{`Uploading \$\{u\.name\}`\}/],
      [join('pages', 'knowledge', 'KnowledgeCreatePage.tsx'), /label="Upload progress"/],
      [join('pages', 'tasks', 'TasksListPage.tsx'), /label=\{`Exit criteria: /],
      [join('ui', 'SystemWidget.tsx'), /label=\{`\$\{label\} usage`\}/],
    ]
    for (const [rel, nameRe] of adopters) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel} should render <Meter`).toMatch(/<Meter\b/)
      // A progressbar with no name is a NEW axe finding, not a fix — so the rail checks
      // that each adoption passes a real label, not merely that it imports the primitive.
      expect(src, `${rel} should name its meter`).toMatch(nameRe)
    }
  })

  it('every Meter call site passes a label', () => {
    // `label` is a required prop, so TypeScript covers the omission — but not `label=""`
    // or `label={undefined as string}`. An empty name is exactly the axe finding this
    // whole change exists to prevent, so it gets its own assertion.
    const bad: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      if (!/<Meter\b/.test(src)) continue
      if (/<Meter\b[^>]*\blabel=(?:""|\{''\}|\{``\}|\{undefined\})/s.test(src)) bad.push(f.slice(SRC.length + 1))
    }
    expect(bad, 'a Meter with an empty label is an unnamed progressbar').toEqual([])
  })

  it('no page invents a fill percentage it cannot compute', () => {
    // `ModelsPanel` parked its bar at a hardcoded `'40%'` whenever the re-index phase had
    // no total — a bar claiming near-half progress it had no basis for, which then jumped
    // BACKWARDS once a real total arrived. A literal percentage in a fill width is the
    // signature of that lie.
    const bad: string[] = []
    for (const f of files) {
      if (PRIMITIVES.some((p) => f.endsWith(p))) continue
      const lines = readFileSync(f, 'utf8').split('\n')
      lines.forEach((ln, i) => {
        if (!/\bh-full\b/.test(ln) && !/\bwidth:/.test(ln)) return
        const win = lines.slice(Math.max(0, i - 1), i + 2).join('\n')
        if (!/\bh-full\b/.test(win) || !/\bwidth:/.test(win)) return
        // A quoted literal percentage ANYWHERE in the width expression — not just as its
        // whole value. The first draft of this rail keyed on `width: '40%'` and a mutation
        // test caught it dead: the real defect was a TERNARY, `width: total > 0 ? `…%` :
        // '40%'`, so the literal never sat directly after the colon. 0% and 100% are the
        // honest degenerate ends (empty / full); a literal strictly between them is a
        // fabricated fraction, which is the whole signature.
        for (const lit of win.match(/['"](\d+(?:\.\d+)?)%['"]/g) ?? []) {
          const n = Number(lit.replace(/['"%]/g, ''))
          if (n > 0 && n < 100) { bad.push(`${f.slice(SRC.length + 1)}:${i + 1} → ${lit}`); break }
        }
      })
    }
    expect(
      bad,
      'A determinate fill pinned to a literal percentage invents progress it does not have. ' +
        'Render the indeterminate WavyProgress, or nothing:\n  ' + bad.join('\n  '),
    ).toEqual([])
  })
})
