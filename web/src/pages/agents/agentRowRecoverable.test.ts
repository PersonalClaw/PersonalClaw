import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── An agent's name is an identifier, and its clipped tail was the distinguishing part ────────────
//
// Measured at 390px on a populated home, `#/agents` had NINE clipped-and-unrecoverable elements across
// its two row variants:
//
//   names         207px of 225-261px   1.1-1.3x   `personalclaw-code-planner`, `-goal-planner`, …
//   descriptions  270px of 378-587px   1.4-2.2x   "Built-in investigative planner for the Code …"
//
// 🔑 THE NAMES ARE THE INTERESTING HALF. They are identifiers whose DISTINGUISHING part is the tail —
// `personalclaw-code-planner` vs `personalclaw-goal-planner` differ only after the 207px cut, so a
// phone user could not tell two rows apart. That is exactly the case the app's `title` idiom exists
// for (19 sites), rather than a prose-readability question.
//
// 🔑 THE DESCRIPTIONS SIT IN THE BAND ALREADY SETTLED. 1.4-2.2x is the same range as the shipped
// task-title fix (1.6-2.1x), so `title` is the consistent answer. The distinction that matters is with
// the **16.8x** skill-description case from the previous cycle: there `title` is a mitigation and the
// real answer is a layout decision, which is FILED as an owner call rather than guessed at here.
//
// 🔑 BOTH ROW VARIANTS, because the census flagged the names in `DiscoveredRow` and the descriptions in
// `AgentRow` — fixing one would have left the surface half-done in a way the census still reports. This
// file already insists the two variants agree (see its dangling-separator note).
//
// 🪤 THE DESCRIPTION'S TITLE IS THE DESCRIPTION ALONE. `AgentRow` renders `{agent.model ? '· ' : ''}`
// before it, a separator the ROW adds; putting it in the tooltip would hand the user punctuation as
// content.
//
// Nothing re-layouts — `title` is an attribute — so the captures are pixel-identical, which is the
// expected result rather than a missing screenshot.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const strip = (s: string) => s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')

describe('an agent row hands over the whole of what it clips', () => {
  const PAGE = strip(read('pages/agents/AgentsListPage.tsx'))

  it("the agent row's name and description both carry a title", () => {
    expect(PAGE).toMatch(/className="truncate text-on-surface text-\[0\.9375rem\] font-mono" style=\{fvs\(500\)\} title=\{agent\.name\}>\{agent\.name\}<\/span>/)
    expect(PAGE).toMatch(/<span className="truncate" title=\{agent\.description\}>\{agent\.model \? '· ' : ''\}\{agent\.description\}<\/span>/)
  })

  it('the discovered row variant carries them too', () => {
    expect(PAGE).toMatch(/className="block truncate text-on-surface text-\[0\.9375rem\]" style=\{fvs\(500\)\} title=\{agent\.name\}>\{agent\.name\}<\/span>/)
    expect(PAGE).toMatch(/className="mt-0\.5 truncate text-on-surface-low text-\[0\.8125rem\]" title=\{agent\.description\}>\{agent\.description\}<\/p>/)
  })

  it('all four are present — neither variant is half-done', () => {
    expect((PAGE.match(/title=\{agent\.name\}/g) || []).length, 'names').toBe(2)
    expect((PAGE.match(/title=\{agent\.description\}/g) || []).length, 'descriptions').toBe(2)
  })

  it('the description title excludes the row-added separator', () => {
    // The `·` belongs to the row, not to the agent. A tooltip reading "· Built-in worker…" would be
    // handing over punctuation as content.
    expect(PAGE, 'the separator stays in the rendered text only').toMatch(/title=\{agent\.description\}>\{agent\.model \? '· ' : ''\}/)
    expect(PAGE, 'and never inside the title').not.toMatch(/title=\{`?\$?\{?agent\.model \? '· '/)
  })

  it('all four still truncate — the fix is recovery, not re-layout', () => {
    // If one stops truncating, its `title` becomes noise and this rail should be revisited.
    expect((PAGE.match(/truncate/g) || []).length, 'truncating elements in this file').toBeGreaterThanOrEqual(4)
  })

  it('the row still names itself for assistive tech — the half that already worked', () => {
    // `ListRow label=` carries the agent name to AT regardless of the visual clip; the `title` is the
    // SIGHTED user's recovery. If the label goes, the finding changes shape.
    expect((PAGE.match(/<ListRow[^>]*label=\{agent\.name\}/g) || []).length, 'rows naming themselves').toBe(2)
  })
})
