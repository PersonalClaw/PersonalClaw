import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── If a popup DECLARES role=menu / role=listbox, it has to implement one ──────────────────────
//
// Cycle 136 fixed the two context menus. This is the census that closes the family, and it started
// by correcting the question: the ledger asked "which of the ~30 `MenuRow` call sites need this?"
// but the promise lives on the CONTAINER, not the row. Measured both ways:
//
//   31 non-test MenuRow call sites → only 2 sit inside a keyboard-navigable container
//   13 containers declaring menu/listbox/tablist → 5 are popups, and 3 had implemented NOTHING
//
// So the sweep the ledger imagined was 10× larger than the defect. What the three had in common,
// driven with the keyboard only (parent tree, dark, 1440×900 unless noted):
//
//   popup                              focus on open   ArrowDown   item tabindex   axe
//   ui/ProjectPicker      #/chat       the trigger     nothing     6× default 0    **1 serious**
//   HeaderModePill        #/chat       the trigger     nothing     4× default 0    0
//   CollapsedSegmented    #/knowledge  the trigger     nothing     5× "0"          0
//                         @430px
//
// 🔴 THE ONE AXE FINDING IS A ROLE DIFFERENCE WORTH KNOWING: `aria-input-field-name` (serious) on
// ProjectPicker's listbox. A `role="listbox"` is an ARIA **input field**, so unlike `role="menu"` it
// MUST carry a name — the nameless menu of cycle 136 was not reportable, this nameless listbox is.
//
// 🪤 EACH POPUP'S TAB BEHAVIOUR DEPENDED ON WHETHER IT WAS PORTALED, not on anything it declared.
// The two INLINE popups (ProjectPicker, CollapsedSegmented) let Tab walk into their first option
// while staying open; the PORTALED one (HeaderModePill) sits last in the document, so Tab skipped
// straight past it to the next header control — its four mode options were unreachable in practice.
//
// 🪤 AND ONE CLAIM I HAD TO KILL: I predicted the mode pill "cannot hold focus", because its wrapper
// arms a 120ms close on blur and the menu is portaled OUTSIDE that wrapper. Driven, the menu stayed
// open (measured at +200ms and +600ms) — **React portals bubble events through the React tree**, so
// the wrapper's own `onFocus` fires for the portaled option and cancels the close. Measure the
// framework's behaviour before shipping a claim about it.
//
// The hover path is why `autoFocus: false` exists: the mode pill opens on mouseenter, and stealing
// focus because a pointer crossed a control would be worse than the defect being fixed. The cursor
// still arms, so the first Arrow key pulls focus in.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
/** 🪤 Comments stripped FIRST, always. `ui/Popover.tsx` documents the words `role="menu"` in
 *  MenuRow's prose, so a raw grep counts it as a sixth popup — the same false positive that has
 *  cost this session three separate censuses. */
const codeOf = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** Every non-test file under web/src. */
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('the declaration-implies-implementation census', () => {
  /** file → the popup roles it declares, comments stripped. */
  const declaring = new Map<string, string[]>()
  for (const abs of walk(SRC)) {
    const rel = abs.slice(SRC.length + 1)
    const roles = [...codeOf(rel).matchAll(/role="(menu|listbox)"/g)].map((m) => m[1])
    if (roles.length) declaring.set(rel, [...new Set(roles)])
  }
  /** The COMBOBOX popups: focus stays in the composer's editor and a virtual cursor moves, which is
   *  the right pattern for a typeahead (APG combobox) and the opposite of this hook's. They own
   *  their own arrow handling; what they still lack is `aria-activedescendant`, i.e. this family's
   *  defect in its third variant — deferred, not forgotten, because wiring it means reaching into
   *  the CodeMirror contenteditable that holds focus, not adding an attribute. */
  //
  //  🔑 `app/CommandPalette.tsx` joined this family in cycle 181, and it is the variant that CLOSED the
  //  gap named above: focus stays in its search field and `aria-activedescendant` now tracks the active
  //  option (measured: `opt-0` → `opt-1` on ArrowDown, with the option's `aria-selected` following and
  //  focus never leaving the input). Before it, the palette declared no listbox, no options and no
  //  activedescendant at all — 22 commands with a purely visual highlight. The two composer menus keep
  //  their deferral, for the reason stated: their focus lives in a CodeMirror contenteditable.
  const COMBOBOX = ['ui/composer/SlashMenu.tsx', 'ui/composer/MentionMenu.tsx', 'app/CommandPalette.tsx']
  const focusMoving = [...declaring.keys()].filter((rel) => !COMBOBOX.includes(rel))

  it('finds the popups — the scan is not vacuous', () => {
    expect(declaring.size, 'containers declaring menu/listbox').toBeGreaterThanOrEqual(5)
  })

  it('a combobox popup is EXEMPT from the hook but not from the pattern', () => {
    // The exemption above is a classification, not a pass: keeping focus in the field only works if
    // something else tells assistive tech which option is active. So a file in COMBOBOX owes
    // `aria-activedescendant` + `role="option"` — unless it is one of the two whose focus lives in a
    // CodeMirror contenteditable, which is the deferral the comment above states and dates.
    const DEFERRED = ['ui/composer/SlashMenu.tsx', 'ui/composer/MentionMenu.tsx']
    for (const rel of COMBOBOX) {
      if (DEFERRED.includes(rel)) {
        // The deferral must stay HONEST: if one of these grows an activedescendant, it graduates and
        // this list should shrink rather than keep excusing it.
        expect(codeOf(rel), `${rel} now wires activedescendant — remove it from DEFERRED`)
          .not.toMatch(/aria-activedescendant/)
        continue
      }
      const code = codeOf(rel)
      expect(code, `${rel}: a combobox owes aria-activedescendant`).toMatch(/aria-activedescendant|ariaActiveDescendant/)
      expect(code, `${rel}: its popup children must be options`).toMatch(/role="option"/)
      expect(code, `${rel}: an active option must say so`).toMatch(/aria-selected/)
    }
  })

  it('every declaring file wires the shared cursor', () => {
    // THE RATCHET. A new popup that says role=menu/listbox and does not import the hook is exactly
    // the defect this cycle measured three times; it fails here instead of shipping.
    const missing = focusMoving.filter((rel) => !read(rel).includes('useMenuCursor'))
    expect(missing, `these declare a keyboard-navigable role but implement no cursor:\n${missing.join('\n')}`).toEqual([])
  })

  it('the five adopters are the ones we expect', () => {
    expect(focusMoving.sort()).toEqual([
      'pages/files/browse/FileTree.tsx',
      'ui/HeaderActions.tsx',
      'ui/ProjectPicker.tsx',
      'ui/Segmented.tsx',
      'ui/motion/ContextMenu.tsx',
    ])
  })

  it('all five route arrows through the one reducer, not five spellings', () => {
    for (const rel of focusMoving) {
      expect(codeOf(rel), `${rel} should call menuCursorKeydown`).toMatch(/menuCursorKeydown\(/)
    }
    // No second spelling — except `ui/Segmented.tsx`, which holds TWO widgets: the expanded
    // `role="tablist"` strip keeps its own ←/→/↑/↓ handler (a tablist is horizontal-first and is not
    // a popup), while the collapsed listbox uses the reducer. Asserted by slicing, so "Segmented is
    // exempt" cannot quietly grow into "Segmented's popup hand-rolls its arrows too".
    for (const rel of focusMoving.filter((r) => r !== 'ui/Segmented.tsx')) {
      expect(codeOf(rel), `${rel} must not hand-roll ArrowDown`).not.toMatch(/key === 'ArrowDown'/)
    }
    const seg = codeOf('ui/Segmented.tsx')
    const at = seg.indexOf('function CollapsedOptions')
    expect(at, 'the collapsed option list moved — re-anchor this slice').toBeGreaterThan(0)
    expect(seg.slice(at), 'the popup half must use the reducer only').not.toMatch(/key === 'Arrow/)
    expect(seg.slice(0, at), "the tablist half keeps its own horizontal handler").toMatch(/ArrowRight' \|\| e\.key === 'ArrowDown'/)
  })

  it('a listbox carries a name; a menu need not — the role difference axe reported', () => {
    // ProjectPicker's was the serious violation. Segmented's listbox already passed `ariaLabel`.
    expect(codeOf('ui/ProjectPicker.tsx')).toMatch(/role="listbox"[\s\S]{0,120}aria-label="Project"/)
    expect(codeOf('ui/Segmented.tsx')).toMatch(/role="listbox" aria-orientation="vertical" aria-label=\{ariaLabel\}/)
  })

  it('the pick-one popups open on their SELECTED option, not the top', () => {
    // APG: focus starts on the checked/selected item so the user hears what is set. The action
    // menus (ContextMenu, FileTree) correctly keep the default 0 — nothing there is "current".
    for (const rel of ['ui/ProjectPicker.tsx', 'ui/HeaderActions.tsx', 'ui/Segmented.tsx']) {
      expect(read(rel), `${rel} should seed the cursor from the value`).toMatch(/initialIndex:/)
    }
    for (const rel of ['ui/motion/ContextMenu.tsx', 'pages/files/browse/FileTree.tsx']) {
      expect(read(rel), `${rel} is an action menu — no initialIndex`).not.toMatch(/initialIndex:/)
    }
  })

  it('the typeahead menus keep focus in the composer — a distinction, not a gap', () => {
    // They must NOT adopt focus movement: a slash/mention menu that stole focus from the editor
    // would break typing. Asserted from both sides so a future pass cannot "finish the sweep".
    for (const rel of COMBOBOX) {
      expect(codeOf(rel), `${rel} owns its own cursor`).toMatch(/key === 'ArrowDown'/)
      expect(codeOf(rel), `${rel} must not move focus onto an option`).not.toMatch(/useMenuCursor/)
    }
  })

  it('a header popup portals, or the page body paints over it', () => {
    // 🔴 Measured on `#/knowledge` at 430px with the collapsed view menu OPEN:
    // `document.elementFromPoint` at the listbox's own centre returned the page's search INPUT —
    // `topmostIsInside: false`. An `absolute z-30` flyout inside the header loses to the page body,
    // which paints over the header row, so the options were keyboard-operable and INVISIBLE. With
    // `portal` the flyout is `fixed z-50` and the topmost element there is its own label span.
    expect(codeOf('ui/Segmented.tsx'), 'the collapsed pill must portal its Popover').toMatch(/placement="bottom"[\s\S]{0,80}portal/)
    // ui/HeaderActions' mode pill solves the same problem with its own createPortal — the comment
    // that explains WHY is the reason this one was findable.
    expect(codeOf('ui/HeaderActions.tsx')).toMatch(/createPortal\(/)
  })

  it('only the hover-opening popup declines autoFocus', () => {
    expect(read('ui/HeaderActions.tsx'), 'the mode pill opens on mouseenter').toMatch(/autoFocus: false/)
    const others = ['ui/ProjectPicker.tsx', 'ui/Segmented.tsx', 'ui/motion/ContextMenu.tsx', 'pages/files/browse/FileTree.tsx']
    for (const rel of others) expect(read(rel), `${rel} opens by intent — focus should follow`).not.toMatch(/autoFocus: false/)
  })
})

describe('HeaderModePill: the portaled menu a keyboard user could not reach', () => {
  beforeEach(() => vi.resetModules())

  const renderPill = async () => {
    const { HeaderModePill } = await import('../ui/HeaderActions')
    const onChange = vi.fn()
    const view = render(
      <HeaderModePill ariaLabel="Task mode" value="plan" onChange={onChange}
        options={[{ key: 'agent', label: 'Agent' }, { key: 'ask', label: 'Ask' }, { key: 'plan', label: 'Plan' }]} />,
    )
    return { view, onChange }
  }

  it('opening on hover does NOT move focus — the pointer must not be hijacked', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    expect(document.activeElement, 'focus stays on the trigger until a key is pressed').toBe(trigger)
  })

  it('the first ArrowDown pulls focus in WITHOUT skipping the checked option', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    // value="plan" is the third option: the FIRST arrow only enters the list (lands on Plan), and
    // the second moves. Without that rule one ArrowDown skipped the selected option entirely.
    act(() => { fireEvent.keyDown(window, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Plan' }))
    act(() => { fireEvent.keyDown(window, { key: 'ArrowUp' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Ask' }))
    act(() => { fireEvent.keyDown(window, { key: 'Home' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Agent' }))
  })

  it('Home from OUTSIDE the list jumps to the first option — only arrows just-enter', async () => {
    // The distinction the first version got wrong: it treated Home as "enter the list", so a user
    // asking for the top of the menu got the option that was already selected.
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Home' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Agent' }))
  })

  it('is one tab stop, not four', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    // Cursor on the checked option ("plan", index 2) before anything is pressed.
    expect(screen.getAllByRole('menuitemradio').map((r) => r.getAttribute('tabindex'))).toEqual(['-1', '-1', '0'])
  })

  it('Tab dismisses instead of walking into the header behind it', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Tab' }) })
    await waitFor(() => expect(screen.queryAllByRole('menuitemradio')).toHaveLength(0))
    expect(document.activeElement).toBe(trigger)
  })

  it('Escape closes it and the reopen latch does not stick', async () => {
    // 🪤 The regression this test exists for: `restoreFocus()` puts focus back on the trigger, and
    // the trigger's wrapper opens on `onFocus` — so the first version of this change made Escape
    // close and instantly RE-OPEN the menu (measured live: "Escape closed: true → false").
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }) })
    await waitFor(() => expect(screen.queryAllByRole('menuitemradio')).toHaveLength(0))
    expect(document.activeElement).toBe(trigger)
    // …and hovering afterwards still works: the latch is one turn long, not a mode.
    await new Promise((r) => setTimeout(r, 5))
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
  })

  it('choosing with the keyboard commits the value once', async () => {
    const { onChange } = await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Home' }) })
    const cursor = document.activeElement as HTMLElement
    // The browser's activation click on the focused row (jsdom does not synthesize it).
    act(() => { fireEvent.keyDown(cursor, { key: 'Enter' }); fireEvent.click(cursor) })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('agent')
  })
})

describe('ProjectPicker: a listbox that promised arrows', () => {
  beforeEach(() => vi.resetModules())

  const renderPicker = async (value = '') => {
    vi.doMock('../lib/api', async (orig) => {
      const real = await orig<typeof import('./api')>()
      return { ...real, api: { ...real.api, projects: async () => ([
        { id: 'p1', name: 'Personal', status: 'active' },
        { id: 'p2', name: 'Repeatable', status: 'active' },
      ]) } }
    })
    const { ProjectPicker } = await import('../ui/ProjectPicker')
    const onChange = vi.fn()
    render(<ProjectPicker value={value} onChange={onChange} emptyLabel="No project" />)
    const trigger = screen.getByRole('button', { name: /^Project/ })
    trigger.focus()
    act(() => { fireEvent.click(trigger) })
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1))
    return { trigger, onChange }
  }

  it('names its listbox — the axe serious finding', async () => {
    await renderPicker()
    expect(screen.getByRole('listbox', { name: 'Project' })).toBeTruthy()
  })

  it('moves focus onto the selected option when it opens', async () => {
    await renderPicker('p2')
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('option', { name: /Repeatable/ })))
  })

  it('arrows walk the options and the list is one tab stop', async () => {
    await renderPicker()
    const opts = screen.getAllByRole('option')
    await waitFor(() => expect(document.activeElement).toBe(opts[0]))
    expect(opts.map((o) => o.getAttribute('tabindex'))).toEqual(['0', '-1', '-1'])
    act(() => { fireEvent.keyDown(window, { key: 'End' }) })
    expect(document.activeElement).toBe(screen.getAllByRole('option')[2])
    expect(screen.getAllByRole('option').map((o) => o.getAttribute('tabindex'))).toEqual(['-1', '-1', '0'])
  })

  it('Escape closes it and hands focus back to the trigger', async () => {
    const { trigger } = await renderPicker()
    await waitFor(() => expect(document.activeElement).not.toBe(trigger))
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }) })
    await waitFor(() => expect(screen.queryAllByRole('option')).toHaveLength(0))
    expect(document.activeElement).toBe(trigger)
  })
})
