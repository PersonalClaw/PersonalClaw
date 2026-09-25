/**
 * Every app-config control has a resolvable ACCESSIBLE NAME.
 *
 * Found by driving, not by reading. Apps → kebab → Configure against a real gateway in a
 * fresh container reported **16 of 16 fields across six installed apps** with no resolvable
 * name — DuckDuckGo 1/1, Design Critique 1/1, Git Repository 6/6, Notes 1/1, HTTP Webhook
 * Hooks 4/4, OpenAI-Compatible 3/3. `getByRole('spinbutton', {name: 'Request Timeout'})`
 * found zero; a screen reader announced "spin button, 20".
 *
 * The mechanism, and why "wrapped in a Field" was not enough: a control claims the Field's
 * published label through `FieldLabelCtx`, and
 *   - a raw `<input>` never reads that context at all (`design/rawFormControls.test.tsx`
 *     reproduces exactly that), and
 *   - `Select`/`TextInput` deliberately stand down from it when a `name` is passed
 *     (`claimsFieldLabel = !!labelId && !name && !ariaLabel`), which this call site always
 *     does — so swapping in a primitive would NOT have fixed it either.
 * Two of the four branches already had it right (the boolean toggle's `aria-label`, and
 * `JsonField`'s `ariaLabel`), which is why the miss was invisible: the file was applying
 * its own rule to the two rarest field types and skipping the two commonest.
 *
 * WHY THIS RAIL AND NOT THE EXISTING ONE. `design/rawFormControls.test.tsx` records in its
 * own header that a tree-wide source scan is the wrong detector here ("a 4-line window
 * missed a valid aria-label three attribute-lines down"), and that the per-surface DOM probe
 * is the detector — but its probe only clicked the create affordances on `#/projects`,
 * `#/files` and `#/tools`. Apps → Configure is two clicks deeper and sits in the blind spot
 * that header names. This test closes it for the shared component rather than for one route,
 * because `AppConfigFields` has three consumers (the Apps Configure modal, Settings › Apps,
 * and `settings/ProviderConfigForm`), so a per-route probe would have to find all three.
 *
 * It asserts the NAME, never the attribute: `aria-label` is one of at least four ways to
 * acquire one, and pinning the attribute would fail a future migration to a primitive that
 * is more correct rather than less.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AppConfigFields, type SchemaProp } from './appConfigForm'

afterEach(() => cleanup())

/** The four branches `AppConfigFields` renders, each with a label distinct from its key so a
 *  test passing on the key alone cannot masquerade as a pass on the label. */
const PROPS: Record<string, SchemaProp> = {
  endpoint: { type: 'string', 'x-meta': { label: 'Base URL' } },
  timeout_secs: { type: 'integer', minimum: 1, maximum: 120, 'x-meta': { label: 'Request Timeout' } },
  api_key: { type: 'string', 'x-meta': { label: 'API Key', sensitive: true } },
  region: { type: 'string', enum: ['us', 'eu'], 'x-meta': { label: 'Region' } },
  verbose: { type: 'boolean', 'x-meta': { label: 'Verbose logging' } },
  headers: { type: 'object', 'x-meta': { label: 'Extra Headers' } },
}

/** Resolve a control's accessible name the way assistive tech does: an `aria-labelledby`
 *  reference, else `aria-label`, else an associated `<label>`. Returns null for an unnamed
 *  control, and the string `'(dangling id)'` for an `aria-labelledby` that resolves to
 *  nothing — which must never read as a pass, since it is worse than no attribute. */
function accessibleName(el: Element): string | null {
  const by = el.getAttribute('aria-labelledby')
  if (by) {
    const parts = by.split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim() ?? '')
    const joined = parts.filter(Boolean).join(' ')
    return joined || '(dangling id)'
  }
  const own = el.getAttribute('aria-label')
  if (own) return own
  const id = el.getAttribute('id')
  const lab = id ? document.querySelector(`label[for="${CSS.escape(id)}"]`) : null
  if (lab) return lab.textContent?.trim() ?? null
  return el.closest('label')?.textContent?.trim() ?? null
}

const controls = () =>
  Array.from(
    document.querySelectorAll<HTMLElement>(
      'input, select, textarea, button[aria-pressed], button[role="switch"]',
    ),
  )

describe('every app-config control is named', () => {
  it('names all six field kinds, and leaves none unnamed', () => {
    render(<AppConfigFields appName="acme" props={PROPS} cur={{}} set={() => {}} />)
    const found = controls()
    // The vacuity floor: this suite is meaningless if the render produced no controls, and a
    // zero-length "every" assertion passes. Six declared properties, six controls.
    expect(found.length, 'AppConfigFields rendered no controls, so the sweep measured nothing')
      .toBe(6)
    const unnamed = found.filter((el) => !accessibleName(el)).map((el) => el.id || el.tagName)
    expect(unnamed, 'controls with no accessible name').toEqual([])
    const dangling = found.filter((el) => accessibleName(el) === '(dangling id)').map((el) => el.id)
    expect(dangling, 'controls whose aria-labelledby resolves to nothing').toEqual([])
  })

  it('each control answers to the label a user can see, by role', () => {
    render(<AppConfigFields appName="acme" props={PROPS} cur={{}} set={() => {}} />)
    // This is the assertion the driven probe made and that read zero before the fix. It is
    // role-scoped on purpose: the accessibility tree is the census (a `getBoundingClientRect`
    // sweep over-counts responsive variants, and an innerText sweep misses icon-only controls).
    expect(screen.getByRole('textbox', { name: 'Base URL' })).toBeTruthy()
    expect(screen.getByRole('spinbutton', { name: 'Request Timeout' })).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Region' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Verbose logging' })).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Extra Headers' })).toBeTruthy()
    // A `password` input has no ARIA role, so it cannot be reached by role at all — which is
    // exactly why the sweep above walks the DOM and this one does not cover it.
    const secret = document.getElementById('app-cfg-acme-api_key')!
    expect(accessibleName(secret)).toBe('API Key')
  })

  it("a required field's name carries the same marker its visible label does", () => {
    // The ` *` marker is appended to `label` once and must reach BOTH, or a screen-reader user
    // and a sighted user disagree about which fields are mandatory.
    render(
      <AppConfigFields appName="acme" props={PROPS} cur={{}} set={() => {}} required={['endpoint']} />,
    )
    expect(screen.getByText('Base URL *')).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Base URL *' })).toBeTruthy()
  })

  it('a field with no declared label falls back to its key, and is still named', () => {
    render(<AppConfigFields appName="w2" props={{ note: { type: 'string' } }} cur={{}} set={() => {}} />)
    expect(screen.getByRole('textbox', { name: 'note' })).toBeTruthy()
  })
})
