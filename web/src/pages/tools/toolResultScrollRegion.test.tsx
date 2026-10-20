import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ToolOutput } from './ToolOutput'

// ── A tool result's scroll affordance has to be ON SCREEN (#2515) ─────────────────────────────────
//
// Measured in a real browser, `#/tools?open=workflow_list_defs` run from the docked side panel
// (419px wide), not reasoned from source:
//
//   box                                       clientW/scrollW   clientH/scrollH   verdict
//   result shell  `max-h-96 overflow-y-auto`      338 / 338        384 / 7051     6667px hidden, unnamed
//   JSON table    `overflow-x-auto …`             324 / 505       7007 / 7007     181px hidden sideways
//
// 🔑 THE TABLE'S OWN SCROLLBAR WAS 6645px BELOW THE VISIBLE WINDOW. `overflow-x-auto` made the hidden
// 181px *reachable* — `scrollLeft = 9999` returned 181, so nothing was lost — but the affordance lives
// at the bottom edge of the scrolling box, and that box is as tall as the result. It sat at y=7801
// while the visible window ended at y=1156, on a 900px viewport. `offsetHeight − clientHeight` was 2
// (just the borders), so there was no scrollbar taking layout either: overlay only, never in view.
// A cap on the scrollport is the fix — one visible 384px box owning both axes.
//
// 🪤 `el.focus()` WOULD HAVE REPORTED SUCCESS ON THE DEFECT. It focuses a non-tabbable element
// happily. `tabIndex >= 0` was the measurement, and it read false on both boxes with 0 focusable
// descendants — `scrollable-region-focusable`, with the computed name falling back to the entire
// result payload. The trio `tabIndex={0}` + `role="group"` + `aria-label` is this repo's canonical
// form (design/scrollRegionNamed.test.tsx), and its derived census scans `<pre>` tags only — both
// boxes here are `div`s, which is why a green rail never covered them.

describe("the JSON result table's scrollport is capped and named", () => {
  // An array of objects with many columns → the JsonTable branch, the one measured above.
  const rows = Array.from({ length: 40 }, (_, i) => ({
    id: `row-${i}`, name: `workflow-${i}`, description: 'a'.repeat(80), version: '1.0.0',
  }))
  const mount = () => render(<ToolOutput text={JSON.stringify(rows)} />)

  it('renders the table branch at all — the scan is not vacuous', () => {
    const { container } = mount()
    expect(container.querySelector('table'), 'array-of-objects sniffs to a table').toBeTruthy()
  })

  it('the scrolling box caps its own height, so its scrollbar stays in view', () => {
    const { container } = mount()
    const box = container.querySelector('table')!.closest('[role="group"]')!
    const cls = box.className
    expect(cls, 'an uncapped x-scrollport puts its affordance below the fold').toMatch(/max-h-96/)
    expect(cls, 'and it owns both axes, not just x').toMatch(/overflow-auto/)
  })

  it('carries the canonical trio: tab stop, group role, short explicit name', () => {
    const { container } = mount()
    const box = container.querySelector('table')!.closest('[role="group"]')!
    expect(box.getAttribute('tabindex'), 'operable without relying on Chrome auto-focusing scrollers').toBe('0')
    expect(box.getAttribute('role')).toBe('group')
    const label = box.getAttribute('aria-label')!
    expect(label, 'a name, not the payload').toBeTruthy()
    expect(label.length, `an assembled name must stay a name: ${label}`).toBeLessThan(40)
  })
})

describe('the inspector result shell is a named region', () => {
  // Source-pinned: the shell only exists after a live invoke, so a render test here would assert the
  // absence of the element rather than its attributes (same reasoning as the inbox excerpt rail).
  const code = readFileSync(join(process.cwd(), 'src/pages/tools/ToolInspector.tsx'), 'utf8')

  it('the capped output box carries the trio', () => {
    expect(code).toMatch(/<div tabIndex=\{0\} role="group" aria-label="Tool result" className="max-h-96 overflow-y-auto">/)
  })

  it('its name is the surface\'s own word, not the output', () => {
    expect(code, 'the result block it labels').toMatch(/\{result\.ok \? 'Success' : 'Error'\}/)
  })
})
