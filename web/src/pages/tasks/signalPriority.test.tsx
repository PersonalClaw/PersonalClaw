import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
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

describe('every BROWSING view is signal-only; the detail view is not', () => {
  // The four views of #/tasks — List, Cards, Kanban, Dependency graph — plus the panel.
  it.each([
    ['TasksListPage.tsx', 2],   // MetaLine (list rows) + TaskCard (cards view)
    ['TaskBoard.tsx', 1],       // kanban card
    ['TaskGraph.tsx', 1],       // dependency-graph node
  ])('%s calls signalPriority (%i site(s))', (rel, n) => {
    const src = read(rel)
    expect([...src.matchAll(/signalPriority\(/g)].length, `${rel} must use the signal-only helper`).toBeGreaterThanOrEqual(n)
    expect(src, `${rel} must not fall back to the always-render helper`).not.toMatch(/=\s*priorityMeta\(/)
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

  it('makes the leading dot conditional on something preceding it', () => {
    // The `·` used to be hard-coded onto `due`/`criteria`, which was safe only because priority
    // ALWAYS rendered. With the lead group now able to be empty, a literal prefix would produce a
    // row reading "· 0/2 criteria".
    expect(src).toMatch(/\(lead\.length > 0 \|\| i > 0\) \? '· ' : ''/)
  })

  it('renders nothing at all when the whole line would be empty', () => {
    expect(src).toMatch(/if \(lead\.length === 0 && tail\.length === 0 && !comments\) return null/)
  })

  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/function MetaLine\(/)
    expect(src.length).toBeGreaterThan(2000)
  })
})
