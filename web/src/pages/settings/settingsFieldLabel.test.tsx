import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Field as SettingsField, Row as SettingsRow, NumberRow } from './settingsUI'
import { Field as FormField, TextInput, TextArea } from '../../ui/forms'

// ── The settings `Field` owned a label and published nothing ───────────────────
//
// A form-family control gets its accessible name by claiming the surrounding Field's label via
// `aria-labelledby`. `ui/forms`' Field publishes that id through `FieldLabelCtx`; `settingsUI`'s
// Field — a bordered settings row, a legitimate SECOND layout — did not. So it owned the visible
// label and gave its controls nothing to claim.
//
// Same defect class as the ToolsPage local Field (the previous change in this stack), in a second
// place, and found by MEASURING rather than reading: a DOM sweep of nine routes reported exactly one
// with unnamed controls —
//
//     #/settings/account  →  6 inputs with no accessible name,
//                            including "New password" and "Confirm password"
//
// Fixed at the source: `settingsUI.Field` now publishes its label id via the newly-exported
// `FieldLabelProvider`, so every consumer of that Field is fixed at once instead of each call site
// having to remember an `ariaLabel`. Four settings panels wrap form controls in it; three already
// passed `ariaLabel` and were safe, AccountPanel passed none on any of its six.
//
// A SECOND, DEEPER BUG surfaced while verifying: the primitive's own precedence ignored an explicit
// `ariaLabel` whenever a labelId existed (`claimsFieldLabel = !!labelId && !name`). Its comment had
// always promised the opposite — "a multi-control Field member … an explicit ariaLabel provides the
// name" — so a caller could not override. Measured consequence: the two password inputs share one
// "Set a password" Field and BOTH announced "Set a password". `ariaLabel` is the caller saying "this
// control is not the Field", which only the caller can know, so it now wins.

/** The accessible name a control actually resolves. `byId` avoids a CSS selector because React's
 *  `useId()` emits `:r0:` and a colon is not valid in one. */
function accessibleName(el: Element, root: HTMLElement): string | null {
  const by = el.getAttribute('aria-labelledby')
  if (by) return root.ownerDocument.body.querySelector(`[id="${CSS.escape(by)}"]`)?.textContent?.trim() ?? '(dangling id)'
  return el.getAttribute('aria-label')
}

describe('settingsUI Field publishes its label to the controls inside', () => {
  it('a TextInput claims the settings Field label', () => {
    const { container } = render(
      <SettingsField label="Sign-in username" hint="The name you'll type at the sign-in form.">
        <TextInput value="" onChange={() => {}} placeholder="you" />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement))
      .toBe('Sign-in username')
  })

  it('a TextArea claims it too', () => {
    const { container } = render(
      <SettingsField label="Custom instructions">
        <TextArea value="" onChange={() => {}} rows={3} />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('textarea')!, container as HTMLElement))
      .toBe('Custom instructions')
  })

  it('the published id resolves to a real element', () => {
    // A dangling id is worse than none: AT reports no name while the markup looks correct.
    const { container } = render(
      <SettingsField label="Assistant name"><TextInput value="" onChange={() => {}} /></SettingsField>,
    )
    const by = container.querySelector('input')!.getAttribute('aria-labelledby')
    expect(by).toBeTruthy()
    expect(container.querySelector(`[id="${CSS.escape(by!)}"]`)?.textContent).toBe('Assistant name')
  })

  it('the hint is NOT what gets claimed', () => {
    // Only the label carries the id — claiming the hint would announce a paragraph as the name.
    const { container } = render(
      <SettingsField label="Username" hint="A short handle stamped onto things you create.">
        <TextInput value="" onChange={() => {}} />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('Username')
  })
})

describe('an explicit ariaLabel wins over the Field label', () => {
  it('names each member of a multi-control Field distinctly', () => {
    // THE measured defect: two password inputs in one "Set a password" Field both announced
    // "Set a password" and were indistinguishable to a screen reader.
    const { container } = render(
      <SettingsField label="Set a password">
        <TextInput type="password" value="" onChange={() => {}} ariaLabel="New password" />
        <TextInput type="password" value="" onChange={() => {}} ariaLabel="Confirm password" />
      </SettingsField>,
    )
    const [a, b] = [...container.querySelectorAll('input')]
    expect(accessibleName(a, container as HTMLElement)).toBe('New password')
    expect(accessibleName(b, container as HTMLElement)).toBe('Confirm password')
  })

  it('holds for the ui/forms Field as well — one rule, both layouts', () => {
    const { container } = render(
      <FormField label="Set a password">
        <TextInput type="password" value="" onChange={() => {}} ariaLabel="New password" />
      </FormField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement))
      .toBe('New password')
  })

  it('and for a TextArea', () => {
    const { container } = render(
      <SettingsField label="Notes">
        <TextArea value="" onChange={() => {}} rows={2} ariaLabel="Release notes" />
      </SettingsField>,
    )
    expect(accessibleName(container.querySelector('textarea')!, container as HTMLElement))
      .toBe('Release notes')
  })

  it('without an ariaLabel the Field label is still claimed — the default is unchanged', () => {
    // The regression guard: making ariaLabel win must not break the common single-control case.
    const { container } = render(
      <SettingsField label="Your name"><TextInput value="" onChange={() => {}} /></SettingsField>,
    )
    expect(accessibleName(container.querySelector('input')!, container as HTMLElement)).toBe('Your name')
  })
})

describe('AccountPanel names all six of its controls', () => {
  it('passes an explicit ariaLabel on both password inputs', () => {
    // Read from cwd like the other design tests: `import.meta.url` is not a file: URL under vitest.
    const src = readFileSync(join(process.cwd(), 'src/pages/settings/AccountPanel.tsx'), 'utf8')
    expect(src).toMatch(/ariaLabel="New password"/)
    expect(src).toMatch(/ariaLabel="Confirm password"/)
  })
})

// ── 2026-08-19: the label was claimed, the HINT was still sighted-only ────────────────────────────
//
// The contract above gives a control its NAME. Its description was a separate, unfixed half: measured
// on `#/settings/account`, all six inputs were correctly named and **not one had `aria-describedby`**.
// So sentences the layout renders right beside the control existed only for sighted users — including a
// CONSTRAINT ("At least 12 characters — length matters more than symbols") and a consequence ("Leave it
// empty to keep records unattributed"). A screen-reader user heard "Username, edit text" and none of
// the rule they were expected to follow.
//
// 196 call sites pass a `hint` (Field ×99, settingsUI's Row ×69, NumberRow ×28) and **none of them
// changed**: the id is published by the three layouts and claimed by the same controls that already
// claim the label — the exact mechanism, and the exact zero-call-site-change property, the label half
// was built for.
//
// 🪤 axe CANNOT SEE THIS. An unassociated `<p>` beside an input is valid HTML with no rule to violate;
// all 19 newly-visible settings surfaces reported 0 blocking findings at dark, light and phone. The
// only way to find it is to ask each control what describes it.

describe('a Field publishes its hint as the control DESCRIPTION', () => {
  const describedText = (el: Element, root: HTMLElement) => {
    const id = el.getAttribute('aria-describedby')
    if (!id) return null
    const target = root.querySelector(`#${CSS.escape(id)}`)
    return target ? (target.textContent || '').trim() : 'DANGLING'
  }

  it('the settings Field describes its control with the hint', () => {
    const { container } = render(
      <SettingsField label="Username" hint="A short handle stamped onto things you create.">
        <TextInput value="" onChange={() => {}} />
      </SettingsField>,
    )
    const input = container.querySelector('input')!
    expect(describedText(input, container as HTMLElement))
      .toBe('A short handle stamped onto things you create.')
    // The NAME is still the label — the neighbouring contract must not shift.
    expect(accessibleName(input, container as HTMLElement)).toBe('Username')
  })

  it('the ui/forms Field does it too — one rule, both layouts', () => {
    const { container } = render(
      <FormField label="Brief" hint="Shared as context with every agent.">
        <TextArea value="" onChange={() => {}} rows={3} />
      </FormField>,
    )
    expect(describedText(container.querySelector('textarea')!, container as HTMLElement))
      .toBe('Shared as context with every agent.')
  })

  it('a Row describes a control that names ITSELF', () => {
    // A `Row` publishes no label id on purpose (its control carries its own `aria-label`, and ux-690
    // recorded the divided-row layout as a distinction). The description is independent of that.
    const { container } = render(
      <SettingsRow label="Idle timeout" hint="Auto-close an idle session after this long.">
        <TextInput value="" onChange={() => {}} ariaLabel="Idle timeout" />
      </SettingsRow>,
    )
    const input = container.querySelector('input')!
    expect(describedText(input, container as HTMLElement))
      .toBe('Auto-close an idle session after this long.')
    expect(accessibleName(input, container as HTMLElement)).toBe('Idle timeout')
  })

  it('NumberRow inherits it by COMPOSITION, not by luck', () => {
    // It renders a settings `Field`, so the 28 hinted NumberRows are covered by construction. If it
    // ever stops composing Field, this fails rather than silently losing 28 descriptions.
    const { container } = render(
      <NumberRow label="Turns kept" hint="How many recent turns you can rewind to."
        cfg={{ turns: 5 }} field="turns" min={1} max={9} patch={() => {}} />,
    )
    expect(describedText(container.querySelector('input')!, container as HTMLElement))
      .toBe('How many recent turns you can rewind to.')
  })

  it('an UNHINTED field sets no aria-describedby at all', () => {
    // 🔑 The half that matters as much as the fix: `aria-describedby` pointing at a missing element is
    // worse than absent, because assistive tech resolves it to nothing while the attribute claims a
    // description exists. The id is published only when a hint is rendered.
    for (const el of [
      render(<SettingsField label="Your name"><TextInput value="" onChange={() => {}} /></SettingsField>),
      render(<FormField label="Your name"><TextInput value="" onChange={() => {}} /></FormField>),
      render(<SettingsRow label="Your name"><TextInput value="" onChange={() => {}} ariaLabel="Your name" /></SettingsRow>),
    ]) {
      const input = el.container.querySelector('input')!
      expect(input.getAttribute('aria-describedby'), 'no hint means no description attribute').toBeNull()
    }
  })
})
