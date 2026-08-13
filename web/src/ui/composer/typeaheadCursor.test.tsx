import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// The menu fetches its command list once per session and caches it at module scope, so the mock has
// to be in place before the module is imported — hence the dynamic import per test.
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, slashCommands: async () => ([
    { name: '/help', description: 'List available commands' },
    { name: '/clear', description: 'Start a fresh thread' },
    { name: '/model', description: 'Switch the model' },
  ]) } }
})

// ── The third variant of the cursor family: a listbox nobody was told about ─────────────────────
//
// Cycles 136-138 gave the app's five row/pick-one popups a real cursor (focus). The composer's two
// typeahead menus were deliberately excluded there, because focus must STAY in the editor — moving it
// would break typing. That left them with a cursor and no channel to announce it. Measured on `#/chat`
// with the slash menu open (11 options) and again with the mention menu (12):
//
//                                        BEFORE                      AFTER
//   option ids                           none                        `<prefix>-opt-<i>`
//   editor aria-activedescendant         **none**                    the cursor's option id
//   ↓ from option 0 to 1                 aria-selected moved         aria-selected AND the
//                                        (on a node nobody focused)  activedescendant move together
//   editor aria-controls / haspopup      none / none                 the list id / "listbox"
//   MentionMenu listbox name             **none** → axe SERIOUS      "Prompts, files and knowledge"
//   axe on the open menu                 slash 0 · mention 1         **0 · 0**
//
// 🔑 THE POINT: `aria-selected` on an element the user is not focused on announces nothing. The only
// channel that reaches a screen reader while focus stays in a text editor is `aria-activedescendant`
// ON THE FOCUSED ELEMENT — which is why the slash menu's perfectly correct `role="option"` +
// `aria-selected` (shipped by cycle 130's popup-roles work) still left the cursor silent, and why axe
// reported zero: every attribute it can check was already right.
//
// 🪤 NOT `role="combobox"` + `aria-expanded`. `aria-expanded` is not an allowed attribute on `textbox`
// (axe: aria-allowed-attr), and re-roling a multi-line message editor as a combobox claims more than
// this fixes. `aria-activedescendant` IS supported on textbox, and `aria-controls` / `aria-haspopup`
// are global — measured afterwards: axe clean on the whole page.
//
// 🪤 THE ATTRIBUTE MUST GO WHEN THE MENU DOES. A dangling `aria-activedescendant` names a node that no
// longer exists. Driven: Escape → all three attributes removed, `document.getElementById` of the last
// value returns null, 0 listboxes, and typing still works.
//
// 🪤 TWO REACT TRAPS THIS HIT ON THE WAY, both caught by running it rather than reading it:
//   1. The report effect first landed BELOW each menu's `if (!open …) return null`, so the hook count
//      changed between renders — React rejects that outright.
//   2. Reporting the cursor from an effect whose deps include the parent's inline callback is an
//      infinite loop; the host's setter returns `prev` unchanged when nothing moved, so React bails
//      out of the re-render. Verified live: no "Maximum update depth" console error.

// jsdom does not implement scrollIntoView, which the cursor effect calls to keep the active row in
// view. A no-op keeps the harness honest about what it is standing in for.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

const COMPOSER = join(process.cwd(), 'src', 'ui', 'composer')
const read = (f: string) => readFileSync(join(COMPOSER, f), 'utf8')
const codeOf = (f: string) => read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('SlashMenu publishes the cursor its editor has to announce', () => {
  const anchor = { current: document.createElement('div') } as React.RefObject<HTMLElement>
  let SlashMenu: typeof import('./SlashMenu')['SlashMenu']
  beforeEach(async () => { ({ SlashMenu } = await import('./SlashMenu')) })

  it('gives the listbox and every option an id from the shared prefix', async () => {
    render(<SlashMenu query="" anchorRef={anchor} open idPrefix="cmp-slash" onActiveIndex={vi.fn()}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    const list = await screen.findByRole('listbox', { name: 'Slash commands' })
    expect(list.id).toBe('cmp-slash-list')
    const opts = screen.getAllByRole('option')
    expect(opts.length).toBeGreaterThan(0)
    expect(opts[0].id).toBe('cmp-slash-opt-0')
    // Every id must resolve — that is what aria-activedescendant depends on.
    for (const [i, o] of opts.entries()) expect(document.getElementById(`cmp-slash-opt-${i}`)).toBe(o)
  })

  it('reports the cursor index while open, and null when it closes', async () => {
    const onActiveIndex = vi.fn()
    const { rerender } = render(<SlashMenu query="" anchorRef={anchor} open idPrefix="p" onActiveIndex={onActiveIndex}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    await screen.findByRole('listbox')
    expect(onActiveIndex).toHaveBeenCalledWith(0)
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(onActiveIndex).toHaveBeenLastCalledWith(1)
    onActiveIndex.mockClear()
    rerender(<SlashMenu query="" anchorRef={anchor} open={false} idPrefix="p" onActiveIndex={onActiveIndex}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    expect(onActiveIndex, 'closing must clear the editor attribute').toHaveBeenLastCalledWith(null)
  })

  it('keeps aria-selected on the cursor option — the visual half is unchanged', async () => {
    render(<SlashMenu query="" anchorRef={anchor} open idPrefix="q" onActiveIndex={vi.fn()}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    await screen.findByRole('listbox')
    const opts = screen.getAllByRole('option')
    expect(opts.filter((o) => o.getAttribute('aria-selected') === 'true')).toHaveLength(1)
  })
})

describe('both typeahead menus, and the editor that speaks for them', () => {
  it('the report effect sits ABOVE the early return in both', () => {
    for (const f of ['SlashMenu.tsx', 'MentionMenu.tsx']) {
      const code = codeOf(f)
      const effect = code.indexOf('onActiveIndex(open &&')
      const early = code.search(/if \(!open \|\| !anchorRef\.current/)
      expect(effect, `${f}: the effect must exist`).toBeGreaterThan(0)
      expect(early, `${f}: the early return must exist`).toBeGreaterThan(0)
      expect(effect, `${f}: a hook after an early return changes the hook count`).toBeLessThan(early)
    }
  })

  it('the mention listbox is named — it was the one axe could see', () => {
    expect(codeOf('MentionMenu.tsx')).toMatch(/aria-label=\{leading \? 'Prompts, files and knowledge' : 'Files and knowledge'\}/)
  })

  it('the editor points at the cursor and stops a textbox short of aria-expanded', () => {
    const code = codeOf('MarkdownInput.tsx')
    expect(code).toMatch(/'aria-activedescendant': `\$\{comboId\}-\$\{activeOption\.list\}-opt-\$\{activeOption\.index\}`/)
    expect(code).toMatch(/'aria-controls'/)
    expect(code).toMatch(/'aria-haspopup': 'listbox'/)
    expect(code, "aria-expanded is not allowed on role=textbox").not.toMatch(/'aria-expanded'/)
  })

  it('the attributes are compartment-swapped, not set on a rebuild', () => {
    // The extensions build once (the file says so about the placeholder); a static
    // `contentAttributes.of` could never follow the cursor.
    const code = codeOf('MarkdownInput.tsx')
    expect(code).toMatch(/comboComp\.current\.reconfigure\(EditorView\.contentAttributes\.of\(attrs\)\)/)
    expect(code).toMatch(/comboComp\.current\.of\(\[\]\)/)
  })

  it('the host dedupes the report, which is what stops the render loop', () => {
    const code = codeOf('MarkdownInput.tsx')
    expect(code).toMatch(/if \(prev\?\.list === list && prev\.index === index\) return prev/)
  })
})
