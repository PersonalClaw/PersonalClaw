// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

// ── The shell's own controls are 44 px targets on a phone ─────────────────────────────────────────
//
// The UI quality matrix measured six shell controls under 44 px at 320, 390, 412 and 768 px, on 104
// of its 111 rows: Expand sidebar (32 by 36), Open terminal and Notifications (28), Theme (36), the
// model chip (Choose a model) and System status on the status line (24). Each now carries the
// compact widths' 44 px floor (`TOUCH_TARGET`) from its shell call site, and keeps its own size from
// 1024 px up. So does the needs-you badge in the same corner, which the matrix's rows do not show.
// The status line is 44 px tall at those widths, so the control it holds fits inside it.
//
// The corners grew, so a destination's tab strip (Library, Automations) folds into its one pill
// at 320 and 390 px where it used to fit. The pill is the shell's too, and carries the floor.
//
// jsdom has no layout, so the classes are the honest thing to pin: they are what set the box. The
// matrix (`e2e/surfaces.spec.ts`) measures the box itself.

vi.mock('../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      notifications: vi.fn(async () => ({ notifications: [] })),
      degraded: vi.fn(async () => ({ surfaces: [{ surface: 'search_ranking', label: 'Semantic search', available: false, floor: 'Keyword ranking', backlog: 0 }] })),
      onboarding: vi.fn(async () => ({ has_model_provider: true })),
      system: vi.fn(() => new Promise(() => {})),
    },
  }
})
vi.mock('../lib/useNeedsYou', () => ({ useNeedsYou: () => ({ rows: [{ id: 'one' }], data: {}, error: null }) }))
vi.mock('../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
// The content width control reads the appearance store, which this render has no provider for, and
// the compact widths drop it from the corner anyway.
vi.mock('../ui/WidthPill', () => ({ WidthPill: () => null }))

const { ShellCornerLeft, ShellCornerRight } = await import('../ui/ShellCorners')
const { StatusLine } = await import('./StatusLine')
const { DestinationTabs } = await import('./DestinationTabs')
const { BookOpen, Brain, FileText } = await import('lucide-react')

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

/** Whether `el`'s classes give it a 44 px box below the large breakpoint, in both dimensions. */
const compactFloor = (el: Element) => {
  const cls = el.className.toString().split(/\s+/)
  return cls.includes('max-lg:min-h-11') && cls.includes('max-lg:min-w-11')
}

describe("the shell's controls on a phone", () => {
  it('the sidebar tab carries the floor', () => {
    render(<ShellCornerLeft collapsed onToggle={() => {}} />)
    const tab = screen.getByRole('button', { name: 'Expand sidebar' })
    expect(compactFloor(tab), tab.className).toBe(true)
  })

  it("so does every control in the right corner", async () => {
    render(<ShellCornerRight terminalOpen={false} onToggleTerminal={() => {}} navigate={() => {}} />)
    const chip = await screen.findByTitle(/running without a model/)
    const controls = [
      screen.getByRole('button', { name: 'Open terminal' }),
      screen.getByRole('button', { name: /waiting on you/ }),
      screen.getByRole('button', { name: /^Notifications/ }),
      screen.getByRole('button', { name: /^Theme: / }),
      chip,
    ]
    for (const control of controls) {
      const name = control.getAttribute('aria-label') ?? control.textContent
      expect(compactFloor(control), `${name}: ${control.className}`).toBe(true)
    }
  })

  it('so does System status, on a status line tall enough to hold it', () => {
    render(<StatusLine />)
    const status = screen.getByRole('button', { name: /^System status/ })
    expect(compactFloor(status), status.className).toBe(true)
    const line = screen.getByRole('contentinfo', { name: 'Status' })
    expect(line.className.split(/\s+/)).toContain('max-lg:h-11')
  })

  it("a destination's folded tab strip is a pill that carries the floor", async () => {
    // jsdom has no layout: the strip wants 400 px and its row offers none, so it folds.
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(400)
    const tabs = [
      { id: 'knowledge', label: 'Knowledge', icon: BookOpen, address: 'knowledge' },
      { id: 'memory', label: 'Memory', icon: Brain, address: 'memory' },
      { id: 'files', label: 'Files', icon: FileText, address: 'files' },
    ]
    render(<DestinationTabs name="Library" tabs={tabs} tab="files" navigate={() => {}} fallback={null}>{null}</DestinationTabs>)
    const pill = await screen.findByRole('button', { name: 'Library' })
    expect(compactFloor(pill), pill.className).toBe(true)
  })
})
