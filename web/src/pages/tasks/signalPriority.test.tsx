import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { signalPriority, priorityMeta } from './taskMeta'

// ── A default value rendered as if it were a choice ────────────────────────────────────
//
// `medium` is the task default: `models.py` declares `priority: TaskPriority = MEDIUM` and
// normalizes a missing value with `d.get("priority", "medium")`. So a medium task is
// **indistinguishable from one whose priority was never set** — the chip asserts an intent that
// may not exist.
//
// Measured on the validation home: **28 of 30 tasks are medium (93%)**. Every browsing view drew a
// semantic-coloured "Medium" on nearly every row, which means the colour carried no information and
// the two `high` tasks the chip exists to surface did not stand out at all.
//
// 🪤 THE PRECEDENT WAS FOUR LINES BELOW THE DEFECT. `MetaLine` already hides the assignee with the
// comment: "on a single-user install every task is the owner's, and '@you' on every row is noise."
// Same file, same function, same reasoning — priority just never got it.
//
// Every EXPLICIT rung still renders, `low` and `trivial` included: deliberately deprioritising
// something is a real signal, and so is any unrecognised string (the backend persists priority
// verbatim). Only the default is silent.

describe('signalPriority', () => {
  it('is silent for the default', () => {
    expect(signalPriority('medium')).toBeNull()
  })

  it('is silent when unset — the backend cannot tell that from medium', () => {
    expect(signalPriority(undefined)).toBeNull()
    expect(signalPriority('')).toBeNull()
  })

  it.each(['critical', 'high', 'low', 'trivial'])('still shows %s — an explicit choice', (k) => {
    const pm = signalPriority(k)
    expect(pm, `${k} is a deliberate choice and must stay visible`).not.toBeNull()
    expect(pm!.key).toBe(k)
  })

  it('shows an unrecognised rung verbatim (the backend keeps any string)', () => {
    const pm = signalPriority('p0')
    expect(pm).not.toBeNull()
    expect(pm!.label).toBe('p0')
  })

  it('agrees with priorityMeta wherever it is not silent', () => {
    // The helper must not become a second source of labels/tones.
    for (const k of ['critical', 'high', 'low', 'trivial', 'p0']) {
      expect(signalPriority(k)).toEqual(priorityMeta(k))
    }
  })
})

// ── The call-site half ────────────────────────────────────────────────────────────────

const read = (rel: string) => readFileSync(join(process.cwd(), 'src/pages/tasks', rel), 'utf8')

/** The whole `src/` tree, for the derived census below. Anchored on `import.meta.dirname` rather than
 *  `process.cwd()`, which differs between a root `npm run test:web` and a `cd web && vitest`. */
const SRC_ROOT = join(import.meta.dirname, '..', '..')
const stripComments = (t: string) =>
  t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
/** The rival shape: a `Record`-style literal keying priority rungs straight to colours. ONE
 *  definition, used by both the census and the synthetic guard below — a guard holding a copy of the
 *  pattern proves a copy correct while the census drifts. `critical` + `high` are the two rungs that
 *  carry a semantic colour, so two of them in one literal is a tone map rather than an unrelated
 *  lookup. Not `/g` — a stateful regex would skip every other call to `.test`. */
const RIVAL_TONE_MAP = /critical:\s*'var\(--color-[^)]+\)'[\s\S]{0,160}?high:\s*'var\(--color-/

const walkSrc = (d: string = SRC_ROOT): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walkSrc(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

describe('every BROWSING view is signal-only; the detail view is not', () => {
  // 🪤 THIS LIST WAS COMPLETE FOR `pages/tasks/` AND THAT WAS THE PROBLEM. `read()` resolves against
  // `src/pages/tasks`, so a browsing view of tasks living anywhere ELSE was not merely missing from the
  // list — it was structurally unreachable by this census. The dashboard's `TasksWidget` was exactly
  // that: it kept a rival four-rung `PRIORITY_TONE` map, painted `--color-info` on every `medium` task
  // (the backend default, so indistinguishable from unset — the very thing `signalPriority` exists to
  // stop) and dropped `trivial` entirely. An enumerated population cannot catch a member outside the
  // directory it enumerates, so the tree-wide check below derives the population instead.
  //
  // The four views of #/tasks — List, Cards, Kanban, Dependency graph — plus the two browsing views
  // of tasks that live OUTSIDE this directory and were therefore invisible to the original list.
  // Paths are relative to `src/`, so a member anywhere in the tree can be named.
  it.each([
    ['pages/tasks/TasksListPage.tsx', 2],                 // MetaLine (list rows) + TaskCard (cards)
    ['pages/tasks/TaskBoard.tsx', 1],                     // kanban card
    ['pages/tasks/TaskGraph.tsx', 1],                     // dependency-graph node
    ['pages/dashboard/widgets/TasksWidget.tsx', 1],       // the home dashboard's task preview
    ['pages/companion/CompanionSections.tsx', 1],         // the companion's open-task list
  ])('%s calls signalPriority (%i site(s))', (rel, n) => {
    const src = readFileSync(join(SRC_ROOT, rel), 'utf8')
    expect([...src.matchAll(/signalPriority\(/g)].length, `${rel} must use the signal-only helper`).toBeGreaterThanOrEqual(n)
    expect(src, `${rel} must not fall back to the always-render helper`).not.toMatch(/=\s*priorityMeta\(/)
  })

  it('NOWHERE in the tree keeps a rival priority→tone map', () => {
    // The derived half. Rather than listing browsing views — which is what let a whole directory slip
    // past — this asks the whole tree a mechanical question: does any file other than `taskMeta`
    // define an object literal mapping the priority rungs to colours? That is precisely the shape the
    // dashboard widget had, and it is what makes a second source of truth possible at all.
    const offenders: string[] = []
    for (const abs of walkSrc()) {
      if (abs.endsWith('taskMeta.tsx')) continue
      const src = stripComments(readFileSync(abs, 'utf8'))
      // Two rungs keyed to a `var(--color-…)` inside one literal is enough to be a tone map; one
      // could be an unrelated lookup. `critical`/`high` are the two that carry a semantic colour.
      if (RIVAL_TONE_MAP.test(src)) {
        offenders.push(abs.slice(abs.indexOf('/src/') + 5))
      }
    }
    expect(
      offenders,
      'these files map priority rungs to colours themselves. `taskMeta` owns that map — import ' +
        '`signalPriority` (browsing) or `priorityMeta` (an editor) instead:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('and the tree-wide scan actually reads the tree (vacuity floor)', () => {
    // Without this, a broken walk makes the check above pass over nothing — the exact failure mode
    // that let the original census miss a directory.
    const files = walkSrc()
    expect(files.length, 'the .tsx sweep found nothing — the scan root is wrong').toBeGreaterThan(200)
  })

  it('the rival-map detector fires on the shape it was written from', () => {
    // 🪤 SYNTHETIC, AND IT HAS TO BE. My first attempt anchored this on `taskMeta.tsx` — "the detector
    // must match the canonical map" — and it failed against correct code, because the two shapes are
    // NOT the same: the canonical form is a keyed ARRAY (`{ key: 'critical', tone: … }`) while the
    // rival was a `Record<string, string>` (`critical: 'var(…)'`). Once the rival is deleted there is
    // no real file left holding that shape, so the only honest way to prove the detector still fires
    // is to hand it a sample. Both directions are pinned, because a detector that matched the
    // canonical array too would red `taskMeta` the moment someone dropped the exclusion.
    const rival = `const PRIORITY_TONE: Record<string, string> = {
      critical: 'var(--color-danger)', high: 'var(--color-warn)',
      medium: 'var(--color-info)', low: 'var(--color-on-surface-low)',
    }`
    expect(RIVAL_TONE_MAP.test(rival), 'the detector no longer catches a rival Record map').toBe(true)

    const canonicalShape = `export const PRIORITIES: PriorityMeta[] = [
      { key: 'critical', label: 'Critical', tone: 'var(--color-danger)' },
      { key: 'high', label: 'High', tone: 'var(--color-warn)' },
    ]`
    expect(
      RIVAL_TONE_MAP.test(canonicalShape),
      'the detector matches the canonical keyed-array form, so it would red taskMeta itself',
    ).toBe(false)
  })

  it('TaskDetail deliberately keeps priorityMeta — a field value belongs in its editor', () => {
    // NOT drift. Blanking it in the panel that SETS the priority would read as "unset" rather than
    // "medium", and the detail view is where the value is inspected and changed.
    const src = read('TaskDetail.tsx')
    expect(src, 'the detail panel must keep showing the current value').toMatch(/priorityMeta\(task\.priority\)/)
    expect(src, 'and must not adopt the browsing-surface helper').not.toMatch(/signalPriority/)
  })
})

describe('the meta line cannot strand its separator', () => {
  const src = read('TasksListPage.tsx')

  it('carries no leading dot at all — the conditional one still stranded', () => {
    // 🔴 RETARGETED. This asserted the conditional `(lead.length > 0 || i > 0) ? '· ' : ''`, because a
    // hard-coded `·` produced rows reading "· 0/2 criteria" once the lead group could be empty. That
    // was a real defect and the conditional was a real fix for it — but only for the EMPTY-LEAD case.
    // It tests PRESENCE, not LINE POSITION, so it never addressed wrapping: measured with the
    // conditional in place, 2 of 4 rows stranded at 360px and 4 of 4 at 320px, the width SC 1.4.10
    // requires. Dropping the glyph satisfies BOTH the old property and the wrapping one, absolutely —
    // there is no dot left to lead a row or a line.
    // 🪤 Comments stripped first. The removed guard is QUOTED in the comment that explains why it
    // went, so a raw match finds the defect in its own documentation — measured three times in this
    // programme now, and twice inside a fresh negative assertion. A scanner that cannot tell a
    // statement from a sentence about a statement is measuring the wrong thing.
    const code = src
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    expect(code, 'no glyph, so neither an empty lead nor a wrap can strand one')
      .not.toMatch(/'· '/)
    expect(code, 'and the presence guard that only half-solved it is gone')
      .not.toMatch(/lead\.length > 0 \|\| i > 0/)
  })

  it('renders nothing at all when the whole line would be empty', () => {
    expect(src).toMatch(/if \(lead\.length === 0 && tail\.length === 0 && !comments\) return null/)
  })

  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/function MetaLine\(/)
    expect(src.length).toBeGreaterThan(2000)
  })
})
