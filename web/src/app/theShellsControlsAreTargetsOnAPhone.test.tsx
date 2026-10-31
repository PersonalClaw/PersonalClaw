// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'

// ── The shell's own controls are 44 px targets on a phone ─────────────────────────────────────────
//
// The UI quality matrix measured six shell controls under 44 px at 320, 390, 412 and 768 px, on 104
// of its 111 rows: Expand sidebar (32 by 36), Open terminal and Notifications (28), Theme (36), the
// model chip (Choose a model) and System status on the status line (24). Each now carries the
// compact widths' 44 px floor (`TOUCH_TARGET`) from its shell call site, and keeps its own size from
// 1024 px up. So does the needs-you badge in the same corner, which the matrix's rows do not show.
// The status line is 44 px tall at those widths, so the control it holds fits inside it.
//
// Four 44 px targets made the right corner 201 px wide at 390, so a started chat's header no longer
// fit between the corners and stacked below them (116 px, not 56). Below 768 px the corner folds
// Open terminal and Theme into one More, a 44 px target too, whose menu names both as the corner
// did, the terminal with its shortcut. From 768 px up the corner is as it was.
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

const { ShellCornerLeft, ShellCornerRight } = await import('../ui/ShellCorners')
const { StatusLine } = await import('./StatusLine')
const { DestinationTabs } = await import('./DestinationTabs')
const { BookOpen, Brain, FileText } = await import('lucide-react')
const { bindingLabel } = await import('./keymap')

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
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
    // The strip sits in TopBar's left slot, whose title rule gives every `[data-type]` in it
    // `min-width: 0` from a descendant selector, and the pill has a `data-type`. A plain
    // `min-w-11` loses to that: the matrix measured the pill 32 px wide in a 44 px floor. Only an
    // important width holds.
    expect(pill.getAttribute('data-type'), 'the pill is a [data-type] the title rule reaches').not.toBeNull()
    expect(pill.className.split(/\s+/), pill.className).toContain('max-lg:min-w-11!')
  })
})

/** A window `width` px wide, as `matchMedia` answers the corner's and the shell's width queries. */
function windowOf(width: number) {
  vi.stubGlobal('matchMedia', (q: string) => {
    const max = /max-width:\s*(\d+)px/.exec(q)
    return {
      matches: max ? width <= Number(max[1]) : false, media: q, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
      dispatchEvent: () => false,
    }
  })
}

describe('below 768 px the right corner folds the terminal and the theme into one More', () => {
  it('shows one 44 px More in their place', async () => {
    windowOf(390)
    render(<ShellCornerRight terminalOpen={false} onToggleTerminal={() => {}} navigate={() => {}} />)
    await screen.findByTitle(/running without a model/)
    expect(screen.queryByRole('button', { name: 'Open terminal' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Theme: / })).toBeNull()
    const more = screen.getByRole('button', { name: 'More' })
    expect(compactFloor(more), more.className).toBe(true)
    // Three 44 px targets with their gaps made 153 px, and the started chat's header still
    // stacked at 390. Edge to edge, with no padding, below 768 px (`max-md:`), they make 133.
    const cluster = more.closest('[data-shell-corner-cluster]')
    expect(cluster, 'the More sits in the corner cluster').not.toBeNull()
    expect(cluster!.className.split(/\s+/)).toEqual(expect.arrayContaining(['max-md:gap-0', 'max-md:px-0', 'max-md:py-0']))
  })

  it('its menu names both as the corner did, the terminal first and with its shortcut', async () => {
    windowOf(390)
    const toggle = vi.fn()
    render(<ShellCornerRight terminalOpen={false} onToggleTerminal={toggle} navigate={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'More' }))
    const terminal = await screen.findByRole('button', { name: 'Open terminal' })
    const theme = screen.getByRole('button', { name: /^Theme: .* — switch to / })
    expect(terminal.compareDocumentPosition(theme) & Node.DOCUMENT_POSITION_FOLLOWING, 'the terminal comes first').toBeTruthy()
    expect(within(terminal).getByText(bindingLabel('drawer'))).toBeTruthy()
    fireEvent.click(terminal)
    expect(toggle).toHaveBeenCalledTimes(1)
  })

  it("draws a vertical ⋮, so it is not taken for the page's own `…` beside it", () => {
    // At 390 px the corner's More sits next to a chat's More actions (`…`): two of one glyph that
    // open two different menus. Their names already tell them apart; the glyph does too now.
    windowOf(390)
    render(<ShellCornerRight terminalOpen={false} onToggleTerminal={() => {}} navigate={() => {}} />)
    const glyph = screen.getByRole('button', { name: 'More' }).querySelector('svg')
    const cls = glyph?.getAttribute('class') ?? ''
    expect(cls, cls).toMatch(/\blucide-(ellipsis-vertical|more-vertical)\b/)
    expect(cls, cls).not.toMatch(/\blucide-(ellipsis|more-horizontal)\b(?!-)/)
  })

  it('names the terminal Hide terminal while it is open', async () => {
    windowOf(412)
    render(<ShellCornerRight terminalOpen onToggleTerminal={() => {}} navigate={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'More' }))
    expect(await screen.findByRole('button', { name: 'Hide terminal' })).toBeTruthy()
  })

  it('keeps both in the corner from 768 px up', () => {
    windowOf(768)
    render(<ShellCornerRight terminalOpen={false} onToggleTerminal={() => {}} navigate={() => {}} />)
    expect(screen.getByRole('button', { name: 'Open terminal' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Theme: / })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull()
  })
})
