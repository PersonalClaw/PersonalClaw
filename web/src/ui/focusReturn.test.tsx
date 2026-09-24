import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { useFocusReturn } from './useFocusReturn'

// ── Closing a panel must not drop focus to <body> ────────────────────────────────
//
// A panel dismissed while focus sits inside it leaves `document.activeElement === document.body`,
// which throws a keyboard user back to the top of the page. `ui/Popover`, `useFocusTrap` and
// `DegradedChip` all restore focus to the trigger. `SidePanel` — the dock every list uses for its
// peek — bound Escape but restored nothing.
//
// Measured on the live DOM, opening the Projects peek by CLICKING a row, focusing its Close button
// and pressing Escape:
//
//     BEFORE   focus after Esc: BODY  isBody=true
//     AFTER    focus after Esc: DIV   isBody=false  "Personal default no workspace · 1 list"
//
// 🔑 Why a NEW hook rather than reusing `useFocusTrap`: the trap also captures Tab, and that is
// correct for a modal (which owns focus) and WRONG for a dock. `SidePanel` is a non-modal SIBLING of
// the list beside it — Tab must flow straight through. So `useFocusReturn` is the restore half only,
// and it keeps the one subtlety that makes this hard in a single place:
//
//   the previously-focused element is captured DURING RENDER, not in the effect. React applies a
//   child's `autoFocus` during the same commit, BEFORE effects run — so an effect-time capture
//   records the in-panel field as the "trigger" and then restores focus to a node being unmounted,
//   which drops focus to <body>. That is the exact bug the hook exists to prevent, so a copied
//   `useEffect` at each call site would reintroduce it.

const SRC = join(process.cwd(), 'src')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

// The hook must live in the component that MOUNTS WITH THE PANEL — the cycle-38 lesson. Calling it
// in a wrapper that survives the toggle captures on the WRAPPER's first render (when nothing is
// focused yet), so it restores nothing. Production is correct because callers mount SidePanel
// conditionally (`{peekId && <SidePanel …>}`); an earlier version of this test kept the hook mounted
// and reported a false failure against a working fix.
function Panel() {
  const ref = useFocusReturn<HTMLDivElement>()
  return (
    <div ref={ref} data-testid="panel">
      <button type="button" data-testid="close">close</button>
    </div>
  )
}

function Host({ show }: { show: boolean }) {
  return (
    <>
      <button type="button" data-testid="trigger">open</button>
      {show && <Panel />}
    </>
  )
}

describe('useFocusReturn restores the pre-open focus', () => {
  it('unmounting returns focus to whatever was focused before', () => {
    const { getByTestId, rerender } = render(<Host show={false} />)
    const trigger = getByTestId('trigger') as HTMLButtonElement
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    rerender(<Host show />)
    ;(getByTestId('close') as HTMLButtonElement).focus()
    expect(document.activeElement).toBe(getByTestId('close'))

    rerender(<Host show={false} />)
    // Without the hook this is document.body — the defect.
    expect(document.activeElement).toBe(trigger)
  })

  it('does not restore focus to a node that has been removed', () => {
    // Focusing a detached element is a no-op that silently leaves focus on <body>, so the hook has
    // to check `isConnected` rather than blindly calling .focus().
    function VanishPanel() {
      const ref = useFocusReturn<HTMLDivElement>()
      return <div ref={ref} data-testid="p2"><button type="button" data-testid="c2">x</button></div>
    }
    function Vanishing({ show, keepTrigger }: { show: boolean; keepTrigger: boolean }) {
      return (
        <>
          {keepTrigger && <button type="button" data-testid="t2">open</button>}
          {show && <VanishPanel />}
        </>
      )
    }
    const { getByTestId, rerender } = render(<Vanishing show={false} keepTrigger />)
    ;(getByTestId('t2') as HTMLButtonElement).focus()
    rerender(<Vanishing show keepTrigger />)
    ;(getByTestId('c2') as HTMLButtonElement).focus()
    // The trigger disappears while the panel is open — restoring to it must not throw.
    expect(() => rerender(<Vanishing show={false} keepTrigger={false} />)).not.toThrow()
  })
})

describe('SidePanel uses it, and the trap is NOT reused', () => {
  const src = read('ui/SidePanel.tsx')

  it('SidePanel calls useFocusReturn and attaches the ref to its docked root', () => {
    expect(src).toMatch(/import \{ useFocusReturn \} from '\.\/useFocusReturn'/)
    expect(src).toMatch(/const focusReturnRef = useFocusReturn<HTMLDivElement>\(\)/)
    expect(src).toMatch(/<motion\.div ref=\{focusReturnRef\}/)
  })

  it('SidePanel does NOT use useFocusTrap — a dock must not trap Tab', () => {
    // If someone "unifies" these later, Tab stops flowing from the list into the panel and back,
    // which is a regression dressed as consolidation. The trap belongs to modals only.
    expect(/useFocusTrap/.test(src), 'a dock is a non-modal sibling; trapping Tab would be a bug').toBe(false)
  })

  it('the capture happens during render, not inside the effect', () => {
    // The one subtlety worth pinning: an effect-time capture records the in-panel autoFocus target
    // and then "restores" to an unmounting node, dropping focus to <body>.
    const hook = read('ui/useFocusReturn.ts')
    const beforeEffect = hook.slice(0, hook.indexOf('useEffect('))
    expect(beforeEffect).toMatch(/prevActiveRef\.current = document\.activeElement/)
  })
})
