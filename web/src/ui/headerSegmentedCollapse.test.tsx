import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { List, LayoutGrid, Columns3, GitFork } from 'lucide-react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Segmented } from './Segmented'

// ── Icon-only is NOT the last rung of the header ladder ─────────────────────
//
// `HeaderActions` degrades a control row FULL → TEXT → ICON → OVERFLOW. The trap: a
// 4-option `Segmented` is still ~142px at the ICON tier, and a phone header's whole
// inner content box is ~155px. Because a Segmented registers as `neverOverflow` (a
// mode-slider has no single-row `…`-menu form) the cluster kept it visible, capped the
// scroll rail below its width, and the surplus segments were simply CLIPPED.
//
// Measured at 390×844 on a live seeded gateway, before this was fixed — 6 surfaces, all
// capped to the identical 42px:
//
//     #/tasks        1 of 4 view buttons visible (Cards / Board / Graph gone;
//                    2 of the 3 sat underneath the `…` trigger itself)
//     #/prompts      System + Snippets gone
//     #/chat         permission-mode pill gone
//     #/workflows    Definitions tab gone
//
// 0 affected at 834px and 1280px, so it is a narrow-width defect, not a general one.
//
// Horizontal scroll is NOT the escape hatch: scrollbars are hidden app-wide by owner
// tenet (tokens.css, 2026-07-07), so a clipped segment reads as simply absent. And no cap
// value can rescue it — even with the title fully yielded the box is 39px short of
// strip + `…`.
//
// The fix is the rung that already existed but was never wired: `collapse="menu"` turns
// the strip into ONE pill that opens the options in a Popover, the same final rung
// `ArtifactCompare` and `LoopComposer` pass directly. Two halves, both asserted here:
//
//   1. `HeaderSegmented` must PASS `collapse="menu"` — without it no header Segmented
//      can ever reach that rung, which is what made the family possible.
//   2. The collapsed pill must honour `iconOnly` — labelled it is ~119px and STILL
//      overflowed the rail, rendering as a clipped "☰ Li…". Icon-only it is ~32-40px.
//
// Assertion 1 is a source scan on purpose: the wiring is a prop handed between two
// primitives, and jsdom has no layout, so the collapse threshold (a real width
// comparison) never fires in a unit test. Scanning the call is the honest way to pin it.

const OPTS = [
  { key: 'list', label: 'List view', icon: List },
  { key: 'cards', label: 'Cards view', icon: LayoutGrid },
  { key: 'board', label: 'Kanban board', icon: Columns3 },
  { key: 'dag', label: 'Dependency graph', icon: GitFork },
]

describe('HeaderSegmented reaches the collapsed rung', () => {
  it('passes collapse="menu" to Segmented', () => {
    const src = readFileSync(join(process.cwd(), 'src/ui/HeaderActions.tsx'), 'utf8')
    // The single <Segmented .../> render inside HeaderSegmented.
    const m = src.match(/return <Segmented[^/]*\/>/)
    expect(m, 'HeaderSegmented should render exactly one <Segmented/>').toBeTruthy()
    expect(
      m![0],
      'Without collapse="menu" a header Segmented has no rung below icon-only, so its ' +
        'surplus segments get clipped by the rail cap instead of collapsing to a pill.',
    ).toMatch(/collapse="menu"/)
  })
})

describe('the collapsed Segmented pill', () => {
  /** Force the collapse branch: jsdom reports every width as 0, so `need > avail` is
   *  false and the strip stays expanded. Stubbing the probe's scrollWidth is what makes
   *  the threshold fire, mirroring a real narrow header. */
  function renderCollapsed(iconOnly: boolean) {
    // jsdom ships no ResizeObserver, and Segmented constructs one in a layout effect.
    vi.stubGlobal('ResizeObserver', class {
      observe() {} unobserve() {} disconnect() {}
    })
    const spy = vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(400)
    const r = render(
      <div style={{ width: 40 }}>
        <Segmented options={OPTS} value="list" onChange={() => {}} ariaLabel="View"
          collapse="menu" iconOnly={iconOnly} />
      </div>,
    )
    return { ...r, restore: () => { spy.mockRestore(); vi.unstubAllGlobals() } }
  }

  it('drops its label and chevron when the cluster is already icon-only', () => {
    const { container, restore } = renderCollapsed(true)
    try {
      // The live trigger is the one NOT inside the aria-hidden measurement probe.
      const trigger = [...container.querySelectorAll('button[aria-expanded]')]
        .find((b) => !b.closest('[aria-hidden="true"]'))
      expect(trigger, 'collapsed pill should render a trigger').toBeTruthy()
      // ~119px labelled vs ~40px bare: the label is the entire overflow.
      expect(trigger!.textContent?.trim(), 'icon-only pill must render no text').toBe('')
      // Losing the text means the name has to come from aria-label, or the control
      // becomes unnameable to AT.
      expect(trigger!.getAttribute('aria-label')).toBe('View')
      // A square target, not a text pill — this is what fits the rail.
      expect(trigger!.className).toMatch(/size-8|size-6/)
    } finally { restore() }
  })

  it('keeps its label when the cluster is NOT icon-only', () => {
    const { container, restore } = renderCollapsed(false)
    try {
      const trigger = [...container.querySelectorAll('button[aria-expanded]')]
        .find((b) => !b.closest('[aria-hidden="true"]'))
      // The counterpart direction. Dropping the label unconditionally would strip the
      // active value off every wider collapsed pill (e.g. ArtifactCompare's), which a
      // mobile-only assertion would never catch.
      expect(trigger!.textContent).toContain('List view')
      expect(trigger!.className).not.toMatch(/size-8 |size-6 /)
    } finally { restore() }
  })

  it('still offers every option, with full labels, once opened', () => {
    const { container, restore } = renderCollapsed(true)
    try {
      const trigger = [...container.querySelectorAll('button[aria-expanded]')]
        .find((b) => !b.closest('[aria-hidden="true"]'))!
      fireEvent.click(trigger)
      // Hiding the pill's own label is only acceptable because the menu carries the
      // full set — that is what makes this a collapse and not a removal.
      for (const o of OPTS) expect(screen.getByText(o.label)).toBeInTheDocument()
    } finally { restore() }
  })
})

// ── A tab may not be crushed below its own size ──────────────────────────────
//
// `size-8` / `px-m` set a tab's size but NOT its floor: as a flex child with the default
// `flex-shrink: 1`, a tab in a slot narrower than the strip is squeezed instead of overflowing.
// Measured at 390×844 on `#/skills`, where `ModeToggle` deliberately drops to `iconOnly` on mobile —
// the honest, documented intent — and the two icon tabs still came out **15.3 × 32** rather than
// 32 × 32, inside a 107px slot the strip needed ~74px for:
//
//     before   Skills view · 2 tabs · 15.3px wide  → under the 24px SC 2.5.8 minimum, a 15px glyph
//                                                    filling its box edge to edge
//     after    Skills view · 2 tabs · 32px wide    → 834px and 1440px unchanged (97.7 / 88.8)
//
// Overflow is the job of `collapse` ('scroll' / 'menu'), not of silently crushing every target.
// Blast radius measured across 11 surfaces at 390px, before vs after: `#/skills` UNDERSIZED → clean,
// and every other surface byte-for-byte the same verdict — `#/artifacts` and `#/inbox` still
// scrollable, `#/tasks` and `#/loops` still carrying their own (separately recorded) overflow
// defects, nothing newly clipped and no new page overflow.

describe('Segmented tabs do not shrink', () => {
  const src = readFileSync(join(process.cwd(), 'src', 'ui', 'Segmented.tsx'), 'utf8')

  it('the tab button declares shrink-0', () => {
    const tab = src.slice(src.indexOf('role="tab"'), src.indexOf('role="tab"') + 1200)
    expect(tab, 'a tab crushed to 15px is neither legible nor tappable').toMatch(/inline-flex shrink-0 items-center/)
  })

  it('renders it on every option, both densities', () => {
    for (const size of ['md', 'sm'] as const) {
      const { container, unmount } = render(
        <Segmented ariaLabel="t" size={size} value="a" onChange={() => {}}
          options={[{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }]} />,
      )
      const tabs = [...container.querySelectorAll('[role="tab"]')]
      expect(tabs.length).toBe(2)
      for (const t of tabs) expect(t.className, `size=${size}`).toMatch(/\bshrink-0\b/)
      unmount()
    }
  })
})
