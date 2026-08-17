import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The tool name has to be visible on a phone, not merely recoverable ─────────────────────────
//
// `grid grid-cols-2` carried no breakpoint, and each cell holds a wrench icon, the name, an approval
// shield, a risk badge and sometimes a "Disabled" pill. Measured at 390px across 99 real tools:
//
//   visible width of the name span   0–83px, MEDIAN 65px
//   worst cases                      `artifact_delete` painted at 0px (needing 117)
//                                    `artifact_save` at 1px (needing 101)
//   at 768px and 1440px              101/101px — every name renders in full
//
// So the name was not truncated, it was ERASED, and only at phone width.
//
// 🪤 A `title` WOULD HAVE BEEN THE WRONG FIX, and that is the lesson this rail carries. The
// truncation census that found these 81 clipped elements classifies before it edits, because a tooltip
// needs a pointer and this defect exists ONLY on touch. A name you can hover is no use to someone who
// cannot hover; it has to be VISIBLE. The census's "identifier-ish → add a title" rule is a default,
// not a law — check whether the reader on that surface has a pointer at all.
//
// The fix is the app's own idiom: one column below `sm:`, as nine other grids already do
// (SecurityPanel, ArtifactGrid, ArtifactCompare, ScheduleForm, AgentForm, AuditPanel,
// PresetEmptyState, DesignCockpitPage ×2).

const SRC = readFileSync(join(process.cwd(), 'src/pages/tools/ToolsPage.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

describe('the tool grid gives a name room on a phone', () => {
  it('is one column below sm, two above', () => {
    expect(CODE).toMatch(/<div className="grid grid-cols-1 gap-s sm:grid-cols-2">/)
  })

  it('no unconditional two-column grid remains on this page', () => {
    // The defect shape, pinned: `grid-cols-2` with NO breakpoint qualifier in front of it.
    // 🪤 The lookbehind is load-bearing. `\bgrid-cols-2\b` also matches inside `sm:grid-cols-2`,
    // because `:` is a non-word character — my first draft of this assertion failed on the fixed
    // source for exactly that reason.
    expect(CODE, 'a bare grid-cols-2 would collapse the name again')
      .not.toMatch(/(?<![:-])\bgrid-cols-2\b/)
    // …and the responsive one, which carries the prefix, is present.
    expect(CODE).toMatch(/sm:grid-cols-2/)
  })

  it('the cell still holds everything that made it tight — the vacuity floor', () => {
    // If the badges ever leave the row, the reasoning above stops applying and the two-column layout
    // might be fine again. These are what crowd the name, so they are what this rail watches.
    expect(CODE, 'the wrench').toMatch(/<Wrench size=\{16\}/)
    expect(CODE, 'the approval shield').toMatch(/t\.requires_approval && <ShieldAlert/)
    expect(CODE, 'the risk badge').toMatch(/<RiskBadge risk=\{t\.risk_level\} \/>/)
    expect(CODE, 'and the disabled pill').toMatch(/off && <span[^>]*>Disabled<\/span>/)
  })

  it('the name is still the truncating identifier it was', () => {
    // The fix gives it room; it does not stop it truncating when a name is genuinely enormous.
    //
    // 🔑 RE-POINTED, NOT RELAXED (cycle 622). This pinned the prop string up to its closing `>`, so
    // adding the `title` that a truncating identifier needs broke a match whose INTENT — still a
    // truncating monospace identifier — was untouched. Second time this exact shape has bitten in this
    // session (see `mutedLinkTargets`), so it now asserts each prop independently AND the recovery
    // title, which is strictly more than the literal ever checked.
    const span = /<span className="truncate font-mono text-on-surface text-\[0\.8125rem\]"[^>]*>\{t\.name\}<\/span>/.exec(CODE)?.[0] ?? ''
    expect(span, 'the tool name is still a truncating mono span').toBeTruthy()
    expect(span, 'and it hands over its full value').toContain('title={t.name}')
  })

  it('the servers list below is unaffected — it was never a grid', () => {
    // Named so a future reader does not "fix" it too: those rows are full-width flex already.
    expect(CODE).toMatch(/<div className="flex flex-col gap-2">/)
  })
})
