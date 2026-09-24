import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Three buttons called "Save", and one field with no Save at all ─────────────────────────────────
//
// Measured on `#/settings/account`, reading every control's computed name:
//
//   buttons: ["Save", "Save", "Save", "Restart", "Save password", "Offer password sign-in",
//             "Require a 2FA code"]
//   duplicate name groups: [["Save", 3]]
//
// Three fields — Your name, Username, Assistant name — each committed by a button whose entire
// accessible name is "Save" (WCAG 4.1.2). And the fourth identity field had the opposite problem:
//
//   typed "newowner" into Sign-in username → left the panel → came back → the field read ""
//   buttons inside that Field: []
//
// **An editable box on a security surface that silently discards what you type.** The reason is a
// server contract, not an oversight: the only writer of that name is `POST /api/auth/password`, and it
// sets both in one call (`creds.set_password(username, password)`). So a username-only save does not
// exist — but the panel was presenting one.
//
// 🔑 THE FIX IS TO MAKE THE COMMIT PATH VISIBLE, not to invent an endpoint. The username moves into the
// form whose button actually commits it (three controls in one `Field`, the case `ui/forms` carves an
// explicit `ariaLabel` out for), the Field label and hint say both are saved together, and the button's
// `disabledReason` answers the exact question a user in that state has: *why can't I save just the
// username?* → "Enter the password too — the username is saved with it".
//
// 🪤 THIS IS THE ux-712 FAMILY AT A SITE ITS CENSUS CANNOT SEE. That census keys on a `<Button>` inside
// `.map((item) => …)` whose handler references the item — a per-row action. These three Saves are
// hand-written siblings in one component, so nothing in the derived scan matches them. **A census keyed
// on repetition-by-iteration misses repetition-by-authorship.**

const PANEL = join(process.cwd(), 'src', 'pages', 'settings', 'AccountPanel.tsx')
const read = () => readFileSync(PANEL, 'utf8')
const stripped = () => read().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('every Save on the account panel says what it saves', () => {
  it('the three identity buttons carry distinct names', () => {
    const src = stripped()
    for (const name of ['Save: Your name', 'Save: Username', 'Save: Assistant name']) {
      expect(src, `${name} must be the accessible name of one button`).toContain(name)
    }
    // Distinctness is the point — a shared name would satisfy "has a name" and fix nothing.
    const names = [...src.matchAll(/aria-?[Ll]abel="(Save: [^"]+)"/g)].map((m) => m[1])
    expect(names.length, 'all three named').toBe(3)
    expect(new Set(names).size, 'and no two share a name').toBe(3)
  })

  it('the visible word is still "Save" — a name fix, not a relabel', () => {
    const src = stripped()
    // Each button renders `{saved ? 'Saved' : 'Save'}`; three of them.
    expect((src.match(/\? 'Saved' : 'Save'\}/g) || []).length, 'the three visible labels are untouched').toBe(3)
  })

  it('camelCase on ui/Button, dashed on a raw button — and never the other way round', () => {
    // 🪤 `ui/Button` declares camelCase props and spreads no rest, so `aria-label` on IT compiles and
    // reaches nothing; on a raw `<button>` the camelCase spelling is what React drops. Both spellings
    // are present in this file for that reason, each on the right element.
    const src = stripped()
    expect(src, 'the kit button takes ariaLabel').toMatch(/<Button[^>]*ariaLabel="Save: Username"/)
    expect(src, 'and no dashed aria-label may appear on a <Button')
      .not.toMatch(/<Button[^>]*aria-label=/)
  })
})

describe('the sign-in username is committed by a button that exists', () => {
  it('it lives in the credential form, not a field of its own', () => {
    const src = stripped()
    expect(src, 'the standalone Field must be gone').not.toMatch(/<Field label="Sign-in username"/)
    expect(src, 'and the input sits in the credential Field with its own name')
      .toMatch(/<TextInput value=\{userDraft\} onChange=\{setUserDraft\} placeholder="you" ariaLabel="Sign-in username" \/>/)
  })

  it('the label and hint state that both are saved together', () => {
    const src = stripped()
    expect(src, 'the label names both halves').toMatch(/Set a sign-in username and password/)
    expect(src, 'and the change case too').toMatch(/Change the sign-in username or password/)
    expect(src, 'the hint says why a username alone cannot go').toMatch(/saved together, in one step/)
  })

  it('the button answers "why can\'t I save just the username?"', () => {
    const src = stripped()
    expect(src, 'the dirty-username branch must exist').toMatch(/userDirty \? 'Enter the password too — the username is saved with it'/)
    expect(src, 'and it is computed from the server value, not a guess')
      .toMatch(/const userDirty = userDraft\.trim\(\) !== \(state\.username \|\| ''\)/)
    expect(src, 'the button names what it commits').toMatch(/'Saved' : 'Save sign-in'/)
    expect(src, 'the old password-only label is gone').not.toMatch(/'Saved' : 'Save password'/)
  })

  it('nothing here invents a username-only writer', () => {
    // The server sets both in one call. If a future cycle adds a lone save it must add the endpoint
    // too — this assertion is what makes that a deliberate act instead of a plausible-looking patch.
    const src = stripped()
    expect(src, 'still exactly one credential writer').toMatch(/api\.setLoginPassword\(userDraft\.trim\(\), pwDraft\)/)
    expect((src.match(/setLoginPassword\(/g) || []).length, 'and only one call site').toBe(1)
  })
})
