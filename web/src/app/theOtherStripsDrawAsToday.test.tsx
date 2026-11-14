// @vitest-environment jsdom
import { describe, expect, it, vi, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen, cleanup } from '@testing-library/react'
import { BookOpen, Brain, Folder } from 'lucide-react'
import { DestinationTabs } from './DestinationTabs'
import { TOUCH_TARGET } from '../ui/Toggle'

// ── A destination strip keeps its words on a phone only where the page asks ───────────────────
//
// The Library's strip keeps its words on a phone, its tabs 44 px targets, and outlines its current
// tab under forced colours (`DestinationTabs`' `keepLabels` and `markCurrent`). The Capabilities and
// Automations strips are the same component and pass neither, so they draw as before: icons alone
// at 768 px and below, no outline, and none of their goldens or trees move.

const SRC = join(import.meta.dirname, '..')
const TABS = [
  { id: 'library', label: 'Library', icon: BookOpen, address: 'library' },
  { id: 'memory', label: 'Memory', icon: Brain, address: 'memory' },
  { id: 'files', label: 'Files', icon: Folder, address: 'files' },
]

/** Pretend the window is *width* px wide for every `max-width` query. */
function windowOf(width: number) {
  vi.stubGlobal('matchMedia', (query: string) => {
    const max = Number(/max-width:\s*(\d+)px/.exec(query)?.[1] ?? Infinity)
    return { matches: width <= max, media: query, addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, onchange: null, dispatchEvent: () => false }
  })
}
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const strip = (props: { keepLabels?: boolean; markCurrent?: boolean }) => render(
  <DestinationTabs name="Library" tabs={TABS} tab="library" navigate={() => {}} fallback={null} {...props}><p>page</p></DestinationTabs>,
)

describe('the destination strips', () => {
  it('🔑 Capabilities and Automations pass neither opt-in', () => {
    for (const rel of ['pages/capabilities/CapabilitiesSection.tsx', 'pages/automations/AutomationsSection.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, rel).toContain('<DestinationTabs')
      expect(src, rel).not.toMatch(/keepLabels|markCurrent/)
    }
  })

  it('🔑 without the opt-ins a strip draws icons alone at 768 px and no outline', () => {
    windowOf(768)
    strip({})
    for (const tab of screen.getAllByRole('radio')) {
      expect(tab.textContent?.trim(), 'no words on a phone').toBe('')
      expect([...tab.classList].some((c) => c.startsWith('forced-colors:') || c.includes('min-h-11'))).toBe(false)
    }
  })

  it('🔑 with keepLabels a phone strip keeps its words, each tab a 44 px target', () => {
    windowOf(390)
    strip({ keepLabels: true, markCurrent: true })
    const tabs = screen.getAllByRole('radio')
    expect(tabs.map((t) => t.textContent?.trim())).toEqual(['Library', 'Memory', 'Files'])
    for (const t of tabs) for (const c of TOUCH_TARGET.split(' ')) expect(t.classList.contains(c)).toBe(true)
    expect([...screen.getByRole('radio', { checked: true }).classList].some((c) => c === 'forced-colors:outline-[Highlight]')).toBe(true)
  })

  it('below 360 px even a kept strip draws its icons alone', () => {
    windowOf(320)
    strip({ keepLabels: true })
    for (const tab of screen.getAllByRole('radio')) expect(tab.textContent?.trim()).toBe('')
  })
})
