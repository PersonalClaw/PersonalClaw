import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NativeAgentDetail } from '../pages/agents/AgentDetail'
import type { SavedAgent } from '../lib/api'

// ── A scroll box Chrome puts in the tab order is named by its whole content ───────────────────────
//
// Two capped scroll boxes had no tab stop, no role and no name: the agent detail panel's system
// prompt (`max-h-72 overflow-y-auto`, **1127px of content hidden**) and the inbox proposal's
// provenance excerpt (`max-h-40 overflow-auto`, **1728px hidden**). axe flags both as
// `scrollable-region-focusable` (serious).
//
// 🪤 THE OBVIOUS READING OF THAT RULE IS WRONG IN CHROME, AND I NEARLY SHIPPED IT. "No focusable
// content, so a keyboard user cannot scroll it" is what the rule name suggests — but Chromium 151
// puts scrollers with no focusable children into the tab order by itself (verified on a minimal
// page: `button → scrollable div → button`, while a non-scrolling div is skipped). Both regions were
// already reachable and PageDown already scrolled them. Measured, not assumed — the first probe
// used `el.focus()`, which succeeds on a non-tabbable element and therefore reports success on the
// very defect under test. Tab traversal is the measurement.
//
// 🔑 WHAT IS ACTUALLY BROKEN IS THE ANNOUNCEMENT, AND IT IS WORSE THAN UNREACHABILITY. Focus lands
// on the box, and because a bare `div`/`pre` has no explicit name, Chrome computes one from its
// subtree. Read out of the accessibility tree at the moment focus arrives:
//
//   surface     before                                            after
//   #/agents    role="generic", name = 2,700+ chars of the        role="group", name="System prompt"
//               system prompt, announced as ONE name
//   #/inbox     role="generic", name = the entire provenance      role="group",
//               excerpt INCLUDING its `<untrusted_content        name="Why this was proposed"
//               source=…>` fence markers
//
// This is cycle 141's defect in a new shape: there, 5 artifact tiles were named by 438-695 characters
// of their own body. A `group` with an explicit `aria-label` does not take its name from content,
// which is the whole reason the canonical form is a trio and not just a tab stop.
//
// 🔑 THE FENCE TEXT IS THE SHARPER HALF. On `#/inbox` the computed name is attacker-influenced text —
// the excerpt is deliberately rendered as untrusted, fence markers and all, and it was being
// announced as the name of a UI control. Naming the region ends that.
//
// 🔑 CONVERGENCE, NOT INVENTION. The trio `tabIndex={0}` + `role="group"` + `aria-label` is already
// this repo's canonical form for a text scroll region, at four sites: `ui/content/ContentSurface`
// (×2, the preview panes), `settings/DiagnosticsPanel` ("Log output"), `settings/SecurityPanel`
// (the shell denylist) — and `pages/tasks/scrollRegionKeyboard.test.tsx` already rails the kanban
// columns to it. These two were the outliers. Both names are the surface's own words: the agent box
// sits inside `<Section label="System prompt">`, the excerpt inside a
// `<summary>Why this was proposed</summary>`.
//
// ⚠️ NOT VERIFIED: Firefox and WebKit. Only Chromium is installed here, and installing engines is not
// this cycle's business. The tab stop is what makes the region operable in browsers that do NOT
// auto-focus scrollers; that reasoning is stated, not measured.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const agent: SavedAgent = {
  name: 'personalclaw-loop', provider: 'claude', system_prompt: 'You are personalclaw-loop. '.repeat(40),
}

describe('the agent system-prompt box is a named region', () => {
  const mount = () => render(
    <NativeAgentDetail agent={agent} isDefault={false} onSaved={vi.fn()} onDeleted={vi.fn()}
      onSetDefault={vi.fn()} editing={false} onEditingChange={vi.fn()} />,
  )

  it('renders the capped scroll box at all — the scan is not vacuous', () => {
    const { container } = mount()
    expect(container.querySelector('.max-h-72.overflow-y-auto'), 'the prompt scroll box').toBeTruthy()
  })

  it('carries the canonical trio: tab stop, group role, explicit name', () => {
    const { container } = mount()
    const box = container.querySelector('.max-h-72.overflow-y-auto')!
    expect(box.getAttribute('tabindex'), 'operable without relying on Chrome auto-focusing scrollers').toBe('0')
    expect(box.getAttribute('role'), 'announced as a labelled container').toBe('group')
    expect(box.getAttribute('aria-label'), 'borrowed from its own Section label').toBe('System prompt')
  })

  it('the explicit name is what stops the content becoming the name', () => {
    // The defect: with no `aria-label`, the computed name was 2,700+ characters of prompt. A short
    // name is the fix — so assert it is short, not merely present.
    const { container } = mount()
    const label = container.querySelector('.max-h-72.overflow-y-auto')!.getAttribute('aria-label')!
    expect(label.length, `an assembled name must stay a name: ${label.slice(0, 60)}`).toBeLessThan(40)
    expect(label).not.toMatch(/You are personalclaw-loop/)
  })
})

describe('the inbox provenance excerpt is a named region', () => {
  // Source-pinned rather than rendered: the excerpt comes from a fetched `detail.source_excerpt`, so
  // a render test would assert the absence of the element rather than its attributes.
  const code = read('pages/inbox/InboxDetail.tsx')

  it('the untrusted excerpt scroll box carries the same trio', () => {
    expect(code).toMatch(/<pre tabIndex=\{0\} role="group" aria-label="Why this was proposed" className="mt-1\.5 max-h-40 overflow-auto/)
  })

  it('its name is the disclosure summary, not the excerpt', () => {
    // 🪤 If the label ever goes, the computed name reverts to the excerpt — which carries
    // `<untrusted_content source=…>` fence text. A control named by untrusted content is the shape
    // this assertion exists to prevent.
    expect(code, 'the summary that owns the word').toMatch(/<summary[^>]*>Why this was proposed<\/summary>/)
    expect(code, 'and the excerpt stays fenced-looking text, not a name')
      .toMatch(/whitespace-pre-wrap[\s\S]{0,120}\{detail\.source_excerpt\}/)
  })
})

describe('the canonical form has one shape across the family', () => {
  const SITES: [string, RegExp][] = [
    // `#/learning` joined after a whole-inventory sweep: this rail's two examples were CAPPED boxes
    // (`max-h-72`, `max-h-40`) embedded in a page, so a census of those shapes could not see a page's
    // PRIMARY body scroller. Learning's is the one that qualifies, because its panels are read-only —
    // measured by Tab traversal (not `el.focus()`, which lies): tab stop 28, 222px of hidden content,
    // and **1066 characters** announced as the region's name, starting "Capture, last 7 days 7
    // silentSun—silentMon—…". Labelled for what it HOLDS rather than echoing the page's own h1.
    // Sweeping all 49 surfaces afterwards: 0 unnamed scrollable tab stops remain.
    ['pages/learning/LearningPage.tsx', /tabIndex=\{0\} role="group" aria-label="Capture and proposals"/],
    ['ui/content/ContentSurface.tsx', /tabIndex=\{0\} role="group" aria-label=/],
    ['pages/settings/DiagnosticsPanel.tsx', /tabIndex=\{0\} role="group" aria-label="Log output"/],
    ['pages/settings/SecurityPanel.tsx', /tabIndex=\{0\} role="group" aria-label=/],
  ]
  for (const [rel, re] of SITES) {
    it(`${rel} still uses the trio this change converged onto`, () => {
      expect(read(rel), 'the canonical form moved — reconcile, do not fork it').toMatch(re)
    })
  }

  it('the kanban rail that established the rule is intact', () => {
    const rail = read('pages/tasks/scrollRegionKeyboard.test.tsx')
    expect(rail).toMatch(/column scroll region must own a tab stop/)
    expect(rail, 'and it asserts the same trio').toMatch(/toBe\('group'\)/)
  })
})
