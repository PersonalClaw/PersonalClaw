import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Field as SettingsField } from './settingsUI'
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
