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

  it('a row the flow has NOT unlocked is not a button — the caller withholds the handler', () => {
    // Reachability is the caller's judgement, because only it knows the run's high-water mark. A row
    // it has not unlocked arrives with no `onActivate` and must not gain a tab stop that does
    // nothing.
    render(
      <ol>
        <StepRow index={3} icon={User} title="Try one" state="upcoming" />
      </ol>,
    )
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('🔴 an UPCOMING row the flow HAS unlocked is a button — this is the trap that was closed', () => {
    // The old predicate was `!active && done && !!onActivate`, so a row could only be revisited once
    // it was FINISHED. Measured consequence: a run that reached step 5 and went back to step 1
    // re-derived steps 2-5 as `upcoming`, which removed every header button — the page was left with
    // Continue and Skip, and the only way forward was to walk all four steps again. A step the run
    // has already stood on, or jumped over on a resume, is unfinished AND reachable, and both of
    // those rows are `upcoming`.
    render(
      <ol>
        <StepRow index={3} icon={User} title="Try one" state="upcoming" onActivate={() => {}} />
      </ol>,
    )
    expect(screen.getByRole('button', { name: 'Go back to step 4: Try one' }).tagName).toBe('BUTTON')
  })

  it('a SKIPPED row is revisitable too — walking past a step is not finishing with it', () => {
    render(
      <ol>
        <StepRow index={1} icon={User} title="Bring your setup over" state="skipped"
          doneSummary="Skipped" onActivate={() => {}} />
      </ol>,
    )
    expect(screen.getByRole('button', { name: 'Go back to step 2: Bring your setup over' })).toBeTruthy()
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
    expect(src).toMatch(/const revisitable = !active && !!onActivate/)
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
    // TWO justifications have died here, both by being true when written. "The rows are not
    // focusable" went false when a completed row's header became a real button. "A step CHANGE is not
    // a focus change" went false when the new step's heading started taking focus — and that one had
    // been promoted to "the real reason" by this very test, which is how a rail can pin a claim into
    // place after the code has moved out from under it.
    //
    // The reason that survives is about what the region SAYS rather than how focus behaves: the
    // heading focus lands on is named for the step ("Bring your setup over"), never for its position,
    // so this region is the only place "Step 2 of 5" is spoken. That does not become false when focus
    // handling changes again.
    const src = onboarding()
    expect(src, 'the first dead clause is gone').not.toMatch(/rows are not focusable and the step\s*\n?\s*\/?\/?\s*title is not in any focused control/)
    // 🪤 The second clause SURVIVES, quoted and immediately called false — the graveyard is the
    // useful part, and deleting it is how the same wrong reason gets rediscovered. What must not
    // exist is the clause doing WORK, i.e. followed by the "so without this…" that made it a reason.
    // A bare `not.toMatch` on the sentence would red on its own epitaph, which is why this one is
    // anchored on the comma.
    expect(src, 'the second dead clause is not carrying any argument').not.toMatch(/a step CHANGE is not a focus change, so/)
    expect(src, 'and it is recorded as dead rather than deleted').toMatch(/"a step CHANGE is not a focus change" — false/)
    expect(src, 'and a reason that outlives the focus behaviour is stated')
      .toMatch(/named for the step, not for its\s*\n?\s*\/?\/?\s*position/)
  })
})

describe('advancing a step moves focus to the new step instead of dropping it', () => {
  // 🔴 THE DEFECT: the control that had focus — the name field or its Continue arrow — lives in the
  // step BODY, which unmounts as the step collapses. Focus fell to `<body>`, so a keyboard user's
  // next Tab restarted from the top of the document and a screen-reader user had a position with no
  // way to find it (WCAG 2.4.3). Measured in the browser rail too
  // (`web/e2e/onboardingGeometry.spec.ts`), which is what proves the heading is also scrolled into
  // view; this is the fast half — jsdom can observe focus and semantics, just not layout.

  it('the ACTIVE step title is a real heading, and a programmatic focus target', () => {
    render(<ol><StepRow index={1} icon={User} title="Bring your setup over" state="active" /></ol>)
    const h = screen.getByRole('heading', { name: 'Bring your setup over' })
    expect(h.tagName).toBe('H2')
    // -1, not 0: the heading is a destination, not a control. A tab stop here would add a stop that
    // does nothing between the row and its own fields.
    expect(h.getAttribute('tabindex')).toBe('-1')
  })

  it('a NON-active step title is not a heading — one heading at a time, on the step you are on', () => {
    // Five headings would need the APG `<h2><button>` accordion shape (a done row's header IS a
    // button, and `<h2>` inside `<button>` is invalid content), whose accessible name is then the
    // whole row — title, step number and subtitle. The set semantics live on the `<ol>`/`<li>` +
    // `aria-current` instead.
    render(
      <ol>
        <StepRow index={0} icon={User} title="Your name" state="done" doneSummary="Keyur" onActivate={() => {}} />
        <StepRow index={3} icon={User} title="Try one" state="upcoming" />
      </ol>,
    )
    expect(screen.queryByRole('heading')).toBeNull()
  })

  it('🔴 becoming active takes focus', () => {
    const { rerender } = render(<ol><StepRow index={1} icon={User} title="Bring your setup over" state="upcoming" /></ol>)
    expect(document.activeElement, 'nothing focused while the step is still upcoming').toBe(document.body)
    rerender(<ol><StepRow index={1} icon={User} title="Bring your setup over" state="active" /></ol>)
    expect(
      document.activeElement,
      'the step became active and focus stayed on <body> — a keyboard user has to Tab from the top of\n' +
        'the document to find the step they were just moved to',
    ).toBe(screen.getByRole('heading', { name: 'Bring your setup over' }))
  })

  it('🪤 mounting ALREADY active does NOT take focus', () => {
    // Step 1 is active from the first render and its name field carries `autoFocus`. An
    // unconditional focus would race that and land the user on a heading instead of the field they
    // have to type in — so the effect compares against the PREVIOUS state, not the current one.
    render(
      <ol>
        <StepRow index={0} icon={User} title="Your name" state="active">
          <input autoFocus aria-label="Your name" />
        </StepRow>
      </ol>,
    )
    expect(document.activeElement, 'the step body keeps the focus it asked for').toBe(screen.getByLabelText('Your name'))
  })

  it('the focus ring is drawn INSIDE the row, or it is not drawn at all', () => {
    // The `<li>` is `overflow-hidden` and the global `:focus-visible` rule draws outward, so an
    // outline on a child that fills the row is clipped away entirely — the trap the header's own
    // comment records. A heading focus with no visible ring moves the user silently.
    expect(stepStack()).toMatch(/<h2 ref=\{heading\} tabIndex=\{-1\}[^>]*focus-visible:-outline-offset-2/)
  })
})
