import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { User } from 'lucide-react'
import { StepRow } from './StepStack'

// ── A completed onboarding step was mouse-only ────────────────────────────────────────────────────
//
// `StepRow` put `onClick` and `cursor: pointer` on a plain `motion.div` — no `tabIndex`, no `role`,
// no key handler. Four of the five rows are passed `onActivate`, so **once a step was done, a mouse
// user could go back to it and a keyboard user could not** (WCAG 2.1.1), on the FIRST screen of the
// product. Found by loading `#/onboarding` and reading the DOM: the whole page exposed exactly two
// buttons, Continue and Skip.
//
// 🪤 THE PREVIOUS AUDIT OF THIS FILE WALKED PAST IT. `stepProgressAnnounced.test.ts` records "The
// rows are `<div>`s (not focusable) and the active step's title is not in any focused control's
// accessible name" — as a REASON the live region was needed. It read the missing tab stop as a fact
// to design around, standing next to the `onClick` that made it a bug. A true observation, used to
// justify a fix for a different property, is a good way to look straight at a defect and not see it.
//
// 🪤 AND `aria-current="step"` WAS ON A ROLE-LESS DIV. That rail asserts the attribute is present and
// gated to the active row — both true — but never that it can reach anyone. `aria-current` is defined
// for an item within a SET, so a generic div is at best inconsistently exposed. The stack is a real
// `<ol>` now and the rows are real `<li>`s, which is what the visible design always was.
//
// 🔑 WHY A REAL `<button>` RATHER THAN `role="button"` + `tabIndex` + a key handler: hand-rolling the
// three of them is how they end up subtly different, which `ui/rawSoftOffContract.test.ts` already
// records for the soft-off family. A button brings focus, Enter and Space for free.

const SRC = join(process.cwd(), 'src')
const stepStack = () => readFileSync(join(SRC, 'app/onboarding/StepStack.tsx'), 'utf8')
const onboarding = () => readFileSync(join(SRC, 'app/Onboarding.tsx'), 'utf8')

describe('a revisitable step is reachable without a mouse', () => {
  it('🔴 a DONE row with onActivate renders a real button, named for where it goes', () => {
    const onActivate = () => {}
    render(
      <ol>
        <StepRow index={1} icon={User} title="Bring your setup over" state="done"
          doneSummary="Skipped" onActivate={onActivate} />
      </ol>,
    )
    // The name carries the DESTINATION, because "go back" appears nowhere on screen.
    const btn = screen.getByRole('button', { name: 'Go back to step 2: Bring your setup over' })
    expect(btn.tagName, 'a real button, not a div wearing a role').toBe('BUTTON')
    expect(btn.getAttribute('type'), 'type=button so it cannot submit a surrounding form').toBe('button')
  })

  it('the ACTIVE row is not a button — it is where you already are', () => {
    render(
      <ol>
        <StepRow index={0} icon={User} title="Your name" state="active" onActivate={() => {}}>
          <input aria-label="Your name" />
        </StepRow>
      </ol>,
    )
    // Its own children hold the controls; a header button here would add a second tab stop that
    // navigates nowhere.
    expect(screen.queryByRole('button', { name: /Go back to step/ })).toBeNull()
  })

  it('an UPCOMING row is not a button — there is nothing to go back to yet', () => {
    render(
      <ol>
        <StepRow index={3} icon={User} title="Try one" state="upcoming" onActivate={() => {}} />
      </ol>,
    )
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('a done row with NO onActivate is not a button either — the predicate is the affordance', () => {
    // The last step passes no `onActivate`; it must not gain a tab stop that does nothing.
    render(
      <ol>
        <StepRow index={4} icon={User} title="All set" state="done" />
      </ol>,
    )
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('🪤 the same predicate drives the element AND the cursor', () => {
    // The original bug was a `cursor: pointer` that promised an interaction the element could not
    // accept. One predicate now decides both, so they cannot drift apart again.
    const src = stepStack()
    expect(src).toMatch(/const revisitable = !active && done && !!onActivate/)
    expect(src, 'the element choice is derived from it').toMatch(/const Header = revisitable \? motion\.button : motion\.div/)
    expect(src, 'and so is the cursor').toMatch(/revisitable \? 'cursor-pointer' : 'cursor-default'/)
    expect(src, 'no bare onClick left on the row container').not.toMatch(/<motion\.li[\s\S]{0,400}?onClick=/)
  })
})

describe('the stepper is a set, so aria-current has something to be current within', () => {
  it('rows are list items', () => {
    render(<ol><StepRow index={0} icon={User} title="Your name" state="active" /></ol>)
    expect(screen.getByRole('listitem')).toBeTruthy()
  })

  it('the active row carries aria-current="step" on that list item', () => {
    render(<ol><StepRow index={0} icon={User} title="Your name" state="active" /></ol>)
    expect(screen.getByRole('listitem').getAttribute('aria-current')).toBe('step')
  })

  it('a non-active row carries no aria-current at all', () => {
    // An always-on value is as broken as a missing one: every row would claim to be current.
    render(<ol><StepRow index={1} icon={User} title="Bring your setup over" state="upcoming" /></ol>)
    expect(screen.getByRole('listitem').getAttribute('aria-current')).toBeNull()
  })

  it('the screen renders a real <ol>, and the live region stays OUTSIDE it', () => {
    // Only `<li>` may be an `<ol>` child. A `role="status"` paragraph in there is invalid content an
    // AT tree may drop — which would silently delete the announcement this screen relies on, trading
    // one a11y fix for the loss of another.
    const src = onboarding()
    expect(src, 'the stepper container is a list').toMatch(/<ol className="flex w-full list-none flex-col gap-2 p-0">/)
    const olAt = src.indexOf('<ol className="flex w-full list-none')
    const statusAt = src.indexOf('role="status" aria-live="polite"')
    expect(statusAt, 'the live region must exist').toBeGreaterThan(0)
    expect(statusAt, 'and must be declared before the list opens').toBeLessThan(olAt)
  })

  it('🪤 the live region no longer justifies itself with a claim that is now false', () => {
    // It used to read "The rows are not focusable" — true before this change, false after, and never
    // the actual reason: advancing a step does not move focus. A stale justification is how a
    // still-necessary region gets deleted by a later reader.
    const src = onboarding()
    expect(src, 'the false clause is gone').not.toMatch(/rows are not focusable and the step\s*\n?\s*\/?\/?\s*title is not in any focused control/)
    expect(src, 'and the real reason is stated').toMatch(/a step CHANGE is not a focus change/)
  })
})
