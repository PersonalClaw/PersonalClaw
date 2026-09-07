import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, act, cleanup, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Popover } from './Popover'

// ── A portaled flyout was a focus black hole ──────────────────────────────────────────────────────
//
// `Popover`'s `portal` prop was added to fix CLIPPING — a visual problem — and it silently changed
// the KEYBOARD contract. `createPortal(flyout, document.body)` appends the menu after the whole
// `#root` subtree, so the flyout stops being the trigger's DOM neighbour. Sequential focus order
// follows DOM order, so Tab from the trigger went to the next control ON THE PAGE and straight past
// the open menu: the flyout floated over the transcript while focus walked along behind it, and
// nothing dismissed on Tab either. To reach a row you had to Tab through every remaining tabbable in
// the document.
//
// Ten portaled call sites had it, including the composer's PERMISSION MODE pill — the control that
// governs what the agent may do without asking.
//
// 🪤 THE PROJECT HAD ALREADY DIAGNOSED THIS MECHANISM, AND ITS RAIL STILL COULD NOT SEE THESE.
// `lib/menuCursorAdoption.test.tsx` records the portaled HeaderModePill's "four mode options were
// unreachable in practice", and the fix shipped as `useMenuCursor`. But that rail scans for
// `role="menu"|"listbox"`, and these flyouts are deliberately ROLE-LESS (`ui/popupItemRoles.test.tsx`
// pins that a bare button is correct in a role-less popover). The scan key is narrower than the
// defect shape: THE DEFECT COMES FROM PORTALING, NOT FROM DECLARING A ROLE. So the rail was green
// while ten surfaces had the identical, already-named bug.
//
// 🔑 THE SECOND HALF OF THIS FILE IS THE MORE IMPORTANT HALF. The obvious fix — "autofocus the
// flyout whenever `portal` is set" — BREAKS the one portaled call site that was already correct.
// `ui/Segmented` renders a `role="listbox"` child adopting `useMenuCursor` with `initialIndex` = the
// SELECTED option. React runs child effects BEFORE parent effects, so an unconditional focus-in here
// lands last and drags focus from the selected option to the first one — which is verbatim the defect
// `useMenuCursor` says it measured ("one ArrowDown from a trigger showing 'Agent' focused 'Ask'").
// Hence the container-role stand-down, asserted below from both sides.

afterEach(() => cleanup())

// 🪤 "CLOSED" IS ASSERTED ON THE COMPONENT'S STATE, NOT ON THE FLYOUT LEAVING THE DOM.
// `AnimatePresence` keeps the exiting node mounted for its exit animation, so a
// `not.toBeInTheDocument()` check reds even for Escape, whose behaviour predates this change and
// certainly works. The trigger already receives `open` as its first argument, so reflecting it onto
// the button is a direct read of the state the assertion is actually about.
const isOpen = () => screen.getByText('Open').getAttribute('data-open')

/** A portaled popover whose flyout is role-less — the shape of 10 of the 11 portaled call sites. */
function RoleLess({ after = true }: { after?: boolean }) {
  return (
    <div>
      <Popover portal trigger={(o, toggle) => <button onClick={toggle} data-open={o}>Open</button>}>
        {(close) => (
          <div>
            <button onClick={close}>First row</button>
            <button onClick={close}>Second row</button>
          </div>
        )}
      </Popover>
      {after && <button>Next on page</button>}
    </div>
  )
}

/** The `ui/Segmented` shape: portaled, but the flyout declares a container role and owns its cursor. */
function RoleDeclaring() {
  return (
    <Popover portal trigger={(o, toggle) => <button onClick={toggle} data-open={o}>Open</button>}>
      {() => (
        <div role="listbox" aria-label="Options">
          <button role="option" aria-selected={false}>First option</button>
          {/* The cursor hook would focus this one — the SELECTED entry, not the first. */}
          <button role="option" aria-selected>Selected option</button>
        </div>
      )}
    </Popover>
  )
}

const open = () => act(() => { screen.getByText('Open').click() })

describe('a portaled Popover hands focus to its flyout', () => {
  it('moves focus INTO the flyout on open, instead of leaving it on the trigger', () => {
    render(<RoleLess />)
    open()
    // The defect: focus stayed on the trigger while the flyout sat at the end of <body>, so the
    // next Tab went to "Next on page" rather than into the menu.
    expect(document.activeElement, 'focus must enter the flyout').toBe(screen.getByText('First row'))
  })

  it('🪤 lands on the FIRST TABBABLE, skipping a tabindex="-1" decoration', () => {
    // `[tabindex="-1"]` is programmatically focusable but deliberately outside the tab sequence, so
    // it is not where keyboard entry belongs. A naive `[tabindex]` selector would stop here.
    render(
      <Popover portal trigger={(_o, toggle) => <button onClick={toggle}>Open</button>}>
        {() => (
          <div>
            <div tabIndex={-1} data-testid="deco">not a stop</div>
            <button>Real row</button>
          </div>
        )}
      </Popover>,
    )
    open()
    expect(document.activeElement).toBe(screen.getByText('Real row'))
    expect(document.activeElement).not.toBe(screen.getByTestId('deco'))
  })

  it('Tab closes the flyout and returns focus to the trigger', () => {
    render(<RoleLess />)
    open()
    fireEvent.keyDown(screen.getByText('First row'), { key: 'Tab' })
    expect(isOpen(), 'the flyout must close').toBe('false')
    expect(document.activeElement, 'focus returns to the trigger').toBe(screen.getByText('Open'))
  })

  it('🔑 Tab is NOT preventDefault-ed — the browser continues from the trigger', () => {
    // This is the whole trick, and it is `menuCursorKeydown`'s Tab branch reasoning: rather than
    // SIMULATING the next stop, hand focus back to the trigger and let the browser's own Tab carry
    // on from there. Preventing the default would strand focus on the trigger instead, so the press
    // would look like it did nothing.
    render(<RoleLess />)
    open()
    const ev = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    act(() => { screen.getByText('First row').dispatchEvent(ev) })
    expect(ev.defaultPrevented, 'Tab must stay cancelable so the browser moves focus on').toBe(false)
  })

  it('Escape still closes and restores the trigger — the pre-existing behaviour is intact', () => {
    render(<RoleLess />)
    open()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(isOpen()).toBe('false')
    expect(document.activeElement).toBe(screen.getByText('Open'))
  })
})

describe('🔑 it stands down for a popup that owns its own keyboard', () => {
  it('does NOT move focus into a role="listbox" flyout', () => {
    // If this reds, `ui/Segmented`'s selected-option cursor has been overridden and its menu now
    // opens on the wrong row.
    render(<RoleDeclaring />)
    open()
    expect(document.activeElement, 'the cursor hook owns entry here').not.toBe(screen.getByText('First option'))
  })

  it('does NOT consume Tab inside a role-declaring flyout', () => {
    // `menuCursorKeydown` already routes Tab for these; two mechanisms dismissing one press is how
    // a double-dismiss bug starts.
    render(<RoleDeclaring />)
    open()
    fireEvent.keyDown(screen.getByText('First option'), { key: 'Tab' })
    expect(isOpen(), 'the flyout must be left alone').toBe('true')
  })
})

describe('inline mode is untouched', () => {
  it('does not move focus, because the flyout is already the trigger’s next sibling', () => {
    // The 3 non-portaled call sites work by DOM adjacency and must not change: autofocusing them
    // would take focus off the trigger for a menu the user can already Tab into.
    render(
      <Popover trigger={(_o, toggle) => <button onClick={toggle}>Open</button>}>
        {() => <button>Inline row</button>}
      </Popover>,
    )
    open()
    expect(document.activeElement).not.toBe(screen.getByText('Inline row'))
  })

  it('does not consume Tab either', () => {
    render(
      <Popover trigger={(_o, toggle) => <button onClick={toggle}>Open</button>}>
        {() => <button>Inline row</button>}
      </Popover>,
    )
    open()
    fireEvent.keyDown(screen.getByText('Inline row'), { key: 'Tab' })
    expect(screen.queryByText('Inline row')).toBeInTheDocument()
  })
})

describe('the fix is structural, and its reach is pinned', () => {
  const SRC = join(process.cwd(), 'src')
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('focuses with preventScroll — without it, opening the menu closes it', () => {
    // 🪤 NOT a micro-optimisation. The portaled flyout is `position: fixed`, and focusing into it
    // can scroll an ancestor. `Popover` closes on ANY scroll in portal mode (capture-phase), so a
    // scrolling focus call would shut the menu in the same frame it opened.
    const code = strip(readFileSync(join(SRC, 'ui', 'Popover.tsx'), 'utf8'))
    const effect = code.match(/if \(!open \|\| !portal \|\| ownsItsKeyboard\(\)\) return[\s\S]{0,200}/)?.[0] ?? ''
    expect(effect, 'found the focus-in effect').not.toBe('')
    expect(effect).toMatch(/preventScroll: true/)
  })

  it('VACUITY: the portaled call sites the fix serves are still there', () => {
    // Comments are stripped FIRST: `ui/FilterMenu.tsx` writes "🔴 PORTAL for the same reason…" in
    // prose, and `ui/Segmented.tsx` writes "🔴 PORTAL, or this menu is INVISIBLE" — a naive scan
    // counts those and reports a reach the code does not have.
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })
    let portaled = 0
    let callSites = 0
    for (const f of walk(SRC)) {
      const code = strip(readFileSync(f, 'utf8'))
      const popovers = [...code.matchAll(/<Popover\b/g)].length
      if (!popovers) continue
      callSites += popovers
      portaled += [...code.matchAll(/\bportal\b(?!\s*[:?])/g)].length
    }
    // Measured at the time of writing: 11 portaled of 14 call sites across 9 files. 10 of the 11
    // were the defect; the 11th is `ui/Segmented`, which the stand-down above protects.
    expect(portaled, 'portaled Popover call sites').toBeGreaterThanOrEqual(10)
    expect(callSites, 'Popover call sites overall').toBeGreaterThanOrEqual(13)
    // If portaling ever stops being the majority of call sites, the guard's `portal` condition is
    // worth revisiting rather than inheriting on trust.
    expect(portaled).toBeGreaterThan(callSites / 2)
  })
})
