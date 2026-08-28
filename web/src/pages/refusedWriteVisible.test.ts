import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A refused write must not render like a click that never happened ─────────────────────────
//
// The swallowed-write family has been closed twice already: `settings/settingsWriteReported` swept the
// settings area, and `confirmedWriteReported` derived the confirm-gated population. This is the axis
// neither covers, and it is the one that makes the silence hard to notice —
//
//   **the SUCCESS path sets an explicit confirmation, and the failure path set nothing.**
//
// So the two outcomes were told apart only by the ABSENCE of something, which is indistinguishable
// from a click the app never received. Four sites, and each catch stated a STATE decision rather than
// a reason to stay quiet:
//
//   MemoryPanel   StudioDocEditor   `/* leave dirty */`    success flashes `Saved ✓`
//   AgentDetail   routing notes     `/* leave dirty */`    success flashes `Saved ✓`
//   Diagnostics   changeLevel       `/* leave prior */`    success MOVES the level pill
//   TasksWidget   complete          `/* leave in place */` success REMOVES the row
//
// Every one of those state decisions is correct and is kept — reverting a draft would destroy text the
// user is still editing, and moving a pill the backend refused would be a lie. The defect was that the
// state decision was the *whole* of the failure path.
//
// 🔑 THE REMEDY IS PER-SURFACE, DELIBERATELY, because this family's own rule is that the wrong remedy
// is a second defect. The two editors report INLINE beside their Save button, which is the form
// `MemoryPanel`'s own `AddLessonForm` already uses one screen away; the two surfaces with no inline
// slot use the toast, which is what `dashboard/PinnedTiles` and `dashboard/DashboardLive` already do.
// A uniform toast would have put a floating message on an editor that has a perfectly good place for
// it, and a uniform inline span would have had nowhere to render on a one-line widget row.
//
// 🪤 WHY THIS RAIL EXISTS AT ALL RATHER THAN JUST WIDENING THE SETTINGS SWEEP. That sweep found ZERO
// offenders while two sat in its own directory: its matcher was
// `await api.…\([^;]{0,200}?catch \{\s*\}`, and `[^;]` stops at the first semicolon, so it could only
// ever see a SINGLE-STATEMENT try body. That is backwards for this defect — the statements after the
// await *are* the success signals whose absence hides the failure, so a real offender is more likely
// to have several, not fewer. Widened there in the same change (0 → 2 on the unfixed tree, no false
// positives across 52 files). Its declared scope is `pages/settings`, so the two sites outside it are
// asserted here instead of quietly moving that scan's root.
//
// 🪤 AND THE TREE-WIDE GENERALISATION IS DELIBERATELY NOT DONE YET. The same widened matcher run over
// all 612 source files reports **10**: these four, two that an open change already fixes
// (`SkillInspector.deleteSkill`, `TaskDetail.deleteTaskComment`), and three with a genuinely stated
// best-effort reason (`KnowledgeListPage.createKnowledgeCollection`,
// `DesignStepPreview.updateULoop` ×2, `TerminalView.createTerminal`). Generalising now would either
// red on arrival or need an exemption set the other change cannot remove. Recorded in
// `.validation/ux/PRODUCT-POLISH.md` so it is written once, cleanly.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
/** Comments stripped: this file's own header quotes the `catch { /* leave dirty *\/ }` it removed, and
 *  a rail that reads its explanation as a program is how one of these goes false-green here. */
const code = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The two sites outside `settingsWriteReported`'s declared `pages/settings` scope. */
const OUTSIDE_THE_SETTINGS_SWEEP = [
  { file: 'pages/agents/AgentDetail.tsx', write: 'saveAgentMetadata', form: 'inline' as const },
  { file: 'pages/dashboard/widgets/TasksWidget.tsx', write: 'updateTask', form: 'toast' as const },
]

describe('a refused write is visible, not merely non-confirmed', () => {
  it('reads its subjects (a rail over nothing asserts nothing)', () => {
    for (const { file, write } of OUTSIDE_THE_SETTINGS_SWEEP) {
      expect(code(file).length, `${file} did not read`).toBeGreaterThan(1_000)
      expect(code(file), `${file} no longer calls api.${write}`).toContain(`api.${write}(`)
    }
  })

  it.each(OUTSIDE_THE_SETTINGS_SWEEP)('$file does not swallow api.$write', ({ file }) => {
    // The exact defect shape, on comment-stripped source so restoring it with a nicer comment fails.
    const empty = [...code(file).matchAll(/catch\s*(?:\([^)]*\))?\s*\{\s*\}/g)]
    expect(
      empty.length,
      `an empty catch in ${file} makes a refused write identical to a click that never landed`,
    ).toBe(0)
  })

  // 🪤 COUNTED **PER COMPONENT**, and both cheaper anchors failed a mutation first. A per-FILE
  // `toMatch` escaped, because `AgentDetail` carries two of these spans and deleting one left the other
  // matching. A per-file COUNT then borrowed its number from code this change never touched:
  // `MemoryPanel` already had one in `AddLessonForm` (3 `setErr` handlers in total) and `AgentDetail`
  // four, so "expect 1" was wrong about a file that legitimately has two. Slicing to the component
  // that owns the write is the only anchor that measures THIS fix — the same "bound the slice to the
  // construct" lesson a RiskBadge rail in this repo learned the hard way.
  /** The component body, brace-matched from its `function X(` to its closing top-level `}`. */
  const componentBody = (rel: string, name: string) => {
    const src = code(rel)
    const at = src.indexOf(`function ${name}(`)
    expect(at, `${rel} no longer defines ${name}`).toBeGreaterThan(-1)
    const end = src.indexOf('\n}', at)
    expect(end, `${name}'s body did not terminate`).toBeGreaterThan(at)
    return src.slice(at, end)
  }

  /** Each editor that flashes a success confirmation, and must report its failure in the same row. */
  const INLINE_REPORTERS: [string, string][] = [
    ['pages/settings/MemoryPanel.tsx', 'StudioDocEditor'],
    ['pages/agents/AgentDetail.tsx', 'RoutingNotesEditor'],
    ['pages/agents/AgentDetail.tsx', 'RoutingStatusView'],
  ]

  it.each(INLINE_REPORTERS)('%s › %s reports its refused write inline', (rel, name) => {
    const body = componentBody(rel, name)
    expect(
      [...body.matchAll(/\{err && <span role="alert"[^>]*>\{err\}<\/span>\}/g)].length,
      `${name} must report beside the control that was pressed — a toast would put the answer ` +
        'somewhere other than where the user is looking',
    ).toBe(1)
    expect(
      [...body.matchAll(/catch \(e\) \{ setErr\(e instanceof Error \? e\.message : '/g)].length,
      `${name}: the slot needs a handler filling it from the server's own sentence`,
    ).toBe(1)
    expect(body, `${name} must not swallow it instead`).not.toMatch(/catch\s*\{\s*\}/)
  })

  it('the two save editors still flash their success confirmation', () => {
    // The reason the failure had to be local: success is local. If this goes, revisit the remedy.
    for (const name of ['StudioDocEditor', 'RoutingNotesEditor']) {
      const rel = name === 'StudioDocEditor' ? 'pages/settings/MemoryPanel.tsx' : 'pages/agents/AgentDetail.tsx'
      expect(componentBody(rel, name), `${name} must still flash Saved ✓`).toMatch(/Saved ✓/)
    }
  })

  it('the level pill stays gated on the response, and reports through the .catch form', () => {
    const src = code('pages/settings/DiagnosticsPanel.tsx')
    // Gated: the pill must never claim a level the backend refused.
    expect(src).toMatch(/\.then\(\(r\) => setLevel\(r\.level\)\)/)
    expect(src).toMatch(/\.catch\(reportActionFailure\(`set the log level to \$\{l\}`\)\)/)
    expect(src, 'setLevel must not run outside that success handler').not.toMatch(
      /await api\.setLogLevel\(l\);\s*setLevel/,
    )
  })

  it('the dashboard tick names WHICH task, and gates both of its success signals', () => {
    const src = code('pages/dashboard/widgets/TasksWidget.tsx')
    expect(src, 'the row threads its own title in — the widget is a list').toMatch(
      /complete\(t\.id,\s*t\.title\)/,
    )
    expect(src).toMatch(/reportingWrite\(`complete [^`]*\$\{title\}/)
    // Both consequences of success are gated: the row removal AND the dashboard refresh.
    expect(src).toMatch(/if \(!ok\) return\s*\n\s*setDone\(/)
    expect(src, 'refreshAll must sit after the gate, not before it').toMatch(
      /setDone\([\s\S]{0,80}?refreshAll\(\)/,
    )
  })
})
