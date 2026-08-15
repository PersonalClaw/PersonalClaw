import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A `·` may only render when something precedes it ─────────────────────────────
//
// The middle dot is this app's meta-line separator: `gpt-4o · Reviews my translations`. It is written
// as a literal prefix on the SECOND item, which is correct only while the FIRST item always renders.
// When the first item is optional the prefix survives on its own and the row opens with a separator
// that separates nothing.
//
// Measured with a DOM sweep of 17 surfaces on a real build (`#/agents`, `#/tasks`, `#/triggers`,
// `#/artifacts`, `#/prompts`, `#/knowledge`, `#/tools`, `#/inbox`, `#/loops`, `#/workflows`,
// `#/projects`, `#/skills`, `#/apps`, `#/files`, `#/notifications`, `#/learning`, `#/dashboard`),
// counting every visible element whose text starts with `·` and checking whether anything visible
// rendered before it inside its parent:
//
//     106 separators rendered · 8 DANGLING · all 8 on #/agents · all 8 from one line
//
// The 8 were every native agent row: `agent.model` is optional (each built-in inherits its model from
// Settings → Models, so it is absent for all of them) while the `· ` was hard-coded onto
// `agent.description`. `#/tasks` is the same shape done right — cycle 69's `MetaLine` emits
// `(lead.length > 0 || i > 0) ? '· ' : ''` — which is why 98 of the 106 are fine.
//
// ── UPDATE: the sweep above asked the wrong question on one axis ────────────────────────────────
//
// "Did anything visible render before it inside its parent?" tests DOM precedence. What a reader
// actually experiences is VISUAL precedence: in a `flex-wrap` meta row an item can start a new
// visual LINE while something still precedes it in DOM order, and then its leading dot dangles at
// that line's start anyway. Re-running the same 17-surface sweep, but grouping each parent's
// children into visual lines by their vertical centre (10px tolerance) and asking whether anything
// preceded on the SAME line:
//
//     1440×900 (desktop)  102 separators · 34 orphaned  — #/knowledge 26/26 · #/prompts 8/40
//      390×844 (phone)    102 separators · 74 orphaned  — #/knowledge 26/26 · #/prompts 40/40 · #/tasks 8/9
//
// `#/knowledge` was 26 of 26 at BOTH widths, i.e. not a narrow-viewport edge case: its summary span
// is `truncate` (white-space:nowrap) inside the wrap row, so its intrinsic width always exceeds the
// room left beside the type label, it wraps to its own line, and its `· ` prefix separates nothing
// — the exact defect this rail names, in a shape the DOM-precedence detector could not see. Fixed
// the same way `#/agents` was: drop the prefix that can never earn its place. Pinned below.
//
// Still open, deliberately NOT fixed here (one concern per change, and both need a judgement the
// two shapes below do not): `#/prompts` (8 desktop / 40 phone) and `#/tasks` (8 phone, 0 desktop).
// Those dots DO separate when the row fits on one line, so removing them outright would change a
// rendering that is correct at desktop width — that is a taste call about the separator idiom, not
// a defect with one obvious fix.
//
// ⚠️ WHY THIS RAIL IS NOT A TREE-WIDE MATCHER. A line-window heuristic ("the previous line is also
// conditional") was tried and measured: 32 candidates, with safe sites mislabelled as risky —
// `TriggersListPage.tsx:183-185` are three consecutive conditionals that are perfectly safe because an
// UNCONDITIONAL name span renders earlier in the same parent, which a line window cannot see. Deciding
// this correctly needs JSX structure, and the honest detector is the DOM sweep above, which cannot run
// in jsdom. So this rail pins the two sites the sweep actually judged, and the sweep is the thing to
// re-run when a new meta line appears.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the agents meta line cannot strand its separator', () => {
  const src = read('pages/agents/AgentsListPage.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/function NativeRow\(/)
    expect(src).toMatch(/function DiscoveredRow\(/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('gates the dot on the model that it separates from', () => {
    expect(src, 'the model is optional, so the separator must be too').toMatch(
      /\{agent\.model \? '· ' : ''\}\{agent\.description\}/,
    )
  })

  it('no longer hard-codes the dot onto the description', () => {
    // The exact defect text, kept as a negative so a revert is named rather than merely un-asserted.
    expect(/>· \{agent\.description\}/.test(src), 'a literal prefix reappeared').toBe(false)
  })

  it('the sibling row still renders its description bare — the two variants must agree', () => {
    // `DiscoveredRow` never had a prefix. If someone "unifies" by ADDING one here, the same defect
    // arrives from the other direction: discovered agents have no model field at all.
    const discovered = src.slice(src.indexOf('function DiscoveredRow('))
    expect(/·/.test(discovered), 'DiscoveredRow has no model, so it can never earn a separator').toBe(false)
  })
})

describe('the knowledge summary cannot strand its separator', () => {
  const src = read('pages/knowledge/KnowledgeListPage.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/truncate/)
    expect(src).toMatch(/it\.summary \|\| it\.content/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('the summary renders bare — it always wraps to its own line', () => {
    expect(src, 'the summary must not carry a leading separator').toMatch(
      /\{\(it\.summary \|\| it\.content\) && <span className="truncate">\{it\.summary \|\| it\.content\}<\/span>\}/,
    )
  })

  it('the exact pre-fix shape does not come back', () => {
    // Kept as a negative so a revert is named rather than merely un-asserted.
    expect(/<span className="truncate">· \{it\.summary/.test(src), 'the dangling prefix reappeared').toBe(false)
  })

  it('the short file_size sibling KEEPS its separator — it shares the label line', () => {
    // The asymmetry is the point: `fmtBytes` is a few characters, so it sits beside the type label
    // and its dot genuinely separates. "Unifying" by stripping this one would lose a real separator.
    expect(src).toMatch(/<span>· \{fmtBytes\(it\.file_size\)\}<\/span>/)
  })
})

describe('the canonical form on #/tasks stays canonical', () => {
  // 98 of the 106 measured separators are safe because they follow text that always renders. This one
  // is safe because it is COMPUTED, and it is the form to copy when a meta line grows optional parts.
  const src = read('pages/tasks/TasksListPage.tsx')

  it('MetaLine still computes the separator rather than hard-coding it', () => {
    expect(src).toMatch(/\(lead\.length > 0 \|\| i > 0\) \? '· ' : ''/)
  })
})
