import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ConsentModal, PermissionList, consentPythonDeps } from './installConsent'
import type { GuardedResult } from '../../lib/useGuardedInstall'
import type { AppPythonDependency } from '../../lib/api'

// ── Installing an app pip-installs into the venv the GATEWAY runs out of, and consent
//    never said so ─────────────────────────────────────────────────────────────────────
//
// Measured on a fresh `python:3.13-slim` container running a wheel built from this branch:
// of nine apps installed through the Store, FOUR declared `dependencies.pythonDependencies`
// and each `pip install`ed into the gateway's own virtualenv — `anthropic`, `openai`,
// `slack_sdk`, and a `Pillow>=10,<13` pin the installer evaluates against core's own. The
// consent dialog enumerated gateway permissions, app messaging, desktop capabilities,
// network reach and dashboard code, and said *"Installing fetches this app behind the
// security scanner"* — and nothing at all about a third-party package entering the
// interpreter that holds the owner's credentials, filesystem and network reach.
//
// `docs/security/limitations.md` §3 documents the shared-venv behaviour. That is not the
// same as disclosing it: a user consenting in a modal does not read the threat model, and
// documenting a behaviour elsewhere does not discharge the duty of the surface where
// consent is actually given. So this is the dialog completing its own stated purpose.
//
// 🔑 WHY A BORDERED ADVISORY ROW AND NOT A BULLET. The bullets are headed "Permissions the
// gateway enforces". A module that is importable in-process has no chokepoint to enforce
// at, so a bullet would read as a capability the platform polices — the one thing that is
// false about it. `network` (EI-12 D2) and dashboard code (#492) are in the same position
// and already resolved it the same way, with their own row stated either way. This row
// joins them, and deliberately does NOT borrow their "advisory only" phrase: their
// declaration is unenforced, whereas these packages really do install.
//
// 🔑 WHY IT LIVES INSIDE `PermissionList`. That is what puts it on all four `ConsentModal`
// call sites, the Store detail panel and the onboarding card by construction rather than by
// four callers remembering a prop — the mechanism `installConsent.tsx`'s own header records
// as the thing that failed before ("a comment asking four callers to remember is what
// failed here").

const blocked = { needsConsent: true, ok: false } as unknown as GuardedResult

const deps = (...d: AppPythonDependency[]) => d

/** `Modal` renders through a portal, so the RTL container is empty — read the document. */
const text = (el: HTMLElement) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ')

describe('the install-consent surface discloses the packages it will pip-install', () => {
  it('names the manifest specifier VERBATIM, not a generic warning', () => {
    render(<PermissionList perms={{}} pythonDeps={deps({ spec: 'anthropic>=0.20', coreOwned: false })} />)
    // The specifier IS the disclosure. A user deciding about `anthropic>=0.20` has to see
    // that string; "this app installs some Python packages" is a category, not a fact they
    // can weigh. Asserted as an exact substring so a future summarisation reds this.
    expect(text(document.body)).toContain('anthropic>=0.20')
    // …and in monospace, because it is a literal a reader may need to compare character by
    // character against what their environment already has.
    const chip = [...document.querySelectorAll('code')].find((c) => c.textContent === 'anthropic>=0.20')
    expect(chip, 'the specifier must render as a <code> literal').toBeTruthy()
  })

  it('says WHERE they land, in the installer\'s own words', () => {
    render(<PermissionList perms={{}} pythonDeps={deps({ spec: 'openai>=1.30', coreOwned: false })} />)
    const t = text(document.body)
    // `app_manager._reject_core_dependency_conflicts`'s docstring is the source of this
    // framing, deliberately rather than a second vocabulary invented here: "the deps land
    // in the **shared** venv the gateway is running out of … under a live process that has
    // already imported those modules".
    expect(t).toMatch(/shared virtualenv the gateway is running out of/)
    expect(t).toMatch(/a live process that has already imported those modules/)
    expect(t).toMatch(/no per-app site-packages/)
    // And the consequence a permission list cannot express: nothing above bounds it.
    expect(t).toMatch(/none of the permissions above bound it/)
    expect(t).toMatch(/pip install/)
  })

  it('an app that declares none grows NO section — absence renders nothing', () => {
    // Five of the nine measured installs declared no dependency at all. An empty
    // "Python packages: none" box would alarm without informing, and there is no claim in
    // the silence: unlike `hostUi`, this row makes a statement about something that will
    // HAPPEN, so not happening is correctly said by not being on screen.
    const { unmount } = render(<PermissionList perms={{ storage: true }} pythonDeps={[]} />)
    expect(text(document.body)).not.toMatch(/Python packages/i)
    // Positive control: the surrounding component DID render, so the absence above is
    // "declares none" and not "PermissionList failed". Without this the assertion passes
    // against a component that rendered nothing at all.
    expect(text(document.body)).toMatch(/Permissions the gateway enforces/)
    expect(text(document.body)).toMatch(/Network access:/)
    unmount()
    // An omitted prop (the installed-app panel's wire does not carry the field) is the same
    // silence, for the same reason.
    render(<PermissionList perms={{ storage: true }} />)
    expect(text(document.body)).not.toMatch(/Python packages/i)
    expect(text(document.body)).toMatch(/Network access:/)
  })

  it('a CORE-OWNED pin reads as "a version you already have", not as new code', () => {
    // The distinction comes from `app_manager._core_requirement_pins` — the SAME set
    // `_reject_core_dependency_conflicts` gates on — never a list kept in the frontend.
    // `Pillow>=10,<13` cannot move anything: the guard admits it only while the installed
    // version already satisfies it, and refuses the install otherwise.
    render(<PermissionList perms={{}} pythonDeps={deps({ spec: 'Pillow>=10,<13', coreOwned: true })} />)
    const t = text(document.body)
    expect(t).toContain('Pillow>=10,<13')
    expect(t).toMatch(/none new/)
    expect(t).toMatch(/every package it declares is one PersonalClaw already ships/)
    expect(t).toMatch(/a package PersonalClaw itself depends on/)
    expect(t).toMatch(/the install is refused rather than changing it/)
    // 🔴 The loud sentence must NOT fire for a pin that installs nothing — that is the
    // whole point of the split. A row that said "pip install … into the shared venv the
    // gateway is running out of" about `Pillow>=10,<13` would be false.
    expect(t).not.toMatch(/pip install/)
  })

  it('splits a MIXED manifest, and keeps the loud half loud', () => {
    // `diarization-onnx`, measured: three packages core does not own plus `numpy`, which it
    // does. Both statements are true about the same install and they are different
    // statements, so they render as two.
    render(<PermissionList perms={{}} pythonDeps={deps(
      { spec: 'onnxruntime>=1.16', coreOwned: false },
      { spec: 'sherpa-onnx>=1.10', coreOwned: false },
      { spec: 'soundfile>=0.12', coreOwned: false },
      { spec: 'numpy>=1.24', coreOwned: true },
    )} />)
    const t = text(document.body)
    expect(t).toMatch(/Python packages added to this gateway: onnxruntime>=1\.16, sherpa-onnx>=1\.10, soundfile>=0\.12/)
    expect(t).toMatch(/pip install/)
    expect(t).toMatch(/It also pins numpy>=1\.24/)
    // Singular grammar for one core-owned pin, plural for more — a disclosure that reads
    // like a template reads like it was not written for the user in front of it.
    expect(t).toMatch(/That pin is checked/)
    expect(t).not.toMatch(/Those pins are checked/)
    // `numpy` must NOT appear in the "added" list: it is not added.
    expect(t).not.toMatch(/added to this gateway:[^.]*numpy/)
  })

  it('pluralises the core-owned sentence when there is more than one pin', () => {
    render(<PermissionList perms={{}} pythonDeps={deps(
      { spec: 'numpy>=1.24', coreOwned: true },
      { spec: 'Pillow>=10,<13', coreOwned: true },
    )} />)
    const t = text(document.body)
    expect(t).toMatch(/packages PersonalClaw itself depends on/)
    expect(t).toMatch(/Those pins are checked/)
    expect(t).not.toMatch(/That pin is checked/)
  })

  it('reaches the ConsentModal — the screen with the button that acts on it', () => {
    render(<ConsentModal label="slack-channel" result={blocked} busy={false}
      permissions={{}} hostUi={{ page: false, components: false }}
      pythonDeps={deps({ spec: 'slack-sdk>=3.27,<4', coreOwned: false })}
      crons={undefined} onConfirm={() => {}} onClose={() => {}} />)
    const dialog = screen.getByRole('dialog')
    // 🔴 PRESENCE IS NOT THE POINT — CO-LOCATION IS. #3540 was a correctly-rendered
    // `role="alert"` in this very dialog sitting BEHIND the modal's own backdrop, and jsdom
    // implements no layout, so nothing here can prove visibility. What a jsdom test CAN
    // prove is the structural precondition the browser drive then confirms: the disclosure
    // is inside the dialog element, not on the page behind it. Occlusion itself was checked
    // in a real browser with `elementFromPoint` (see the PR's evidence).
    const chip = [...dialog.querySelectorAll('code')].find((c) => c.textContent === 'slack-sdk>=3.27,<4')
    expect(chip, 'the specifier must be INSIDE the dialog, not on the page behind it').toBeTruthy()
    expect(text(dialog)).toMatch(/shared virtualenv the gateway is running out of/)
    // And above the button it informs.
    const confirm = [...dialog.querySelectorAll('button')].find((b) => /Install anyway/.test(b.textContent || ''))
    expect(confirm, 'the consentable branch must still offer the override').toBeTruthy()
    expect(chip!.compareDocumentPosition(confirm!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('a registry POINTER says the packages are unknown, never that there are none', () => {
    // Same failure `consentPermissions` exists to prevent, one field along: a pointer's
    // manifest is not fetched until install, so `pythonDependencies: []` there means "not
    // read", not "declares none". `consentPythonDeps` returns `undefined`, and the modal's
    // unknown branch says so in words.
    render(<ConsentModal label="remote-thing" result={blocked} busy={false}
      permissions={undefined} hostUi={undefined} pythonDeps={undefined}
      crons={undefined} onConfirm={() => {}} onClose={() => {}} />)
    const t = text(screen.getByRole('dialog'))
    expect(t).toMatch(/could not read this app's declared permissions/)
    expect(t).toMatch(/any Python packages it adds to this gateway's environment are unknown too/)
  })
})

describe('consentPythonDeps is the one authority, and it declines to guess', () => {
  it('returns undefined when the manifest was never read', () => {
    expect(consentPythonDeps(undefined)).toBeUndefined()
    // `consentKnown: false` is the pointer case. The wire ships `pythonDependencies: []`
    // for BOTH that and "declares none", which is exactly why the flag is consulted and not
    // the array's emptiness.
    expect(consentPythonDeps({ consentKnown: false, pythonDependencies: [] })).toBeUndefined()
    expect(consentPythonDeps({ consentKnown: undefined, pythonDependencies: [] })).toBeUndefined()
  })

  it('returns the list when it was read, and [] for an app declaring none', () => {
    expect(consentPythonDeps({ consentKnown: true, pythonDependencies: [] })).toEqual([])
    // An absent field on a read manifest is "declares none", not unknown.
    expect(consentPythonDeps({ consentKnown: true })).toEqual([])
    expect(consentPythonDeps({
      consentKnown: true, pythonDependencies: [{ spec: 'openai>=1.0', coreOwned: false }],
    })).toEqual([{ spec: 'openai>=1.0', coreOwned: false }])
  })
})

// ── The call-site rail ────────────────────────────────────────────────────────────────
//
// A disclosure that renders correctly and is never handed its data is not a disclosure, and
// `PermissionList` deliberately fails SILENT on an omitted `pythonDeps`. So a source rail is
// the only thing standing between that design and the defect returning one surface at a
// time. COUNTED PER CALL SITE, not as bare membership: an "is it passed anywhere?" rail
// stays green with three of four sites missing. This mirrors `consentHostUiRendered.test.ts`
// and `consentDisclosesAbsent.test.ts`, which count the same four modals.

const SITES = [
  join('src/pages/apps', 'AppsSection.tsx'),
  join('src/app/onboarding', 'EssentialsStep.tsx'),
]

/** Source with comments stripped, so the prose explaining the fix cannot satisfy — or trip —
 *  a count. A rail measures the program, not the explanation of it. */
const code = (rel: string) =>
  readFileSync(join(process.cwd(), rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('every consent surface is handed the Python-dependency fact', () => {
  it('the comment stripper actually strips (or every count below is unfalsifiable)', () => {
    // The control this file's own rail needs: prove `code()` removes both comment forms,
    // so a green below cannot be a green earned by a docstring.
    const stripped = code(SITES[0])
    expect(stripped.length, 'stripping removed nothing — the regexes are wrong').toBeLessThan(
      readFileSync(join(process.cwd(), SITES[0]), 'utf8').length,
    )
    expect(stripped).not.toMatch(/🔑/)
  })

  it('every ConsentModal render passes pythonDeps', () => {
    let seen = 0
    for (const rel of SITES) {
      for (const m of code(rel).matchAll(/<ConsentModal\b[\s\S]{0,900}?\/>/g)) {
        seen += 1
        expect(m[0], `${rel}: a ConsentModal renders without the python-dependency fact`)
          .toMatch(/pythonDeps=\{consentPythonDeps\(/)
      }
    }
    // The same four the #492 and issue-614 rails count — they are the same modals:
    // `AppsSection`'s StoreView and SourcesPanel, `StoreDetailPanel`'s inline onConfirm, and
    // onboarding's essential-apps step. A fifth that forgets must fail this.
    expect(seen).toBe(4)
  })

  it('the two PRE-INSTALL PermissionList sites pass it too', () => {
    // Three `PermissionList` renders exist across these files. Two are pre-install (the
    // Store detail panel and the onboarding card) and carry a catalog entry, so they pass
    // it. The third is the INSTALLED-app panel, whose wire (`AppSummary`) has no such field
    // — it omits the prop and renders no row, which is honest: by then the packages are
    // already installed and this is a consent surface, not an inventory.
    let withFact = 0, without = 0
    for (const rel of SITES) {
      for (const m of code(rel).matchAll(/<PermissionList\b[\s\S]{0,300}?\/>/g)) {
        if (/pythonDeps=\{consentPythonDeps\(/.test(m[0])) withFact += 1
        else without += 1
      }
    }
    expect(withFact, 'both pre-install permission lists must disclose the packages').toBe(2)
    expect(without, 'only the installed-app panel may omit it').toBe(1)
  })

  it('no call site fabricates the answer it is about to disclose', () => {
    // The `hasUI: false` defect (#492's follow-up) in reverse: `storeUniverse` normalised a
    // catalog row with a hard-coded value and the panel then asserted it. A hard-coded
    // `pythonDependencies` would be worse, because the honest empty case renders NOTHING
    // and so a fabricated `[]` is indistinguishable from a correct one on screen.
    const src = code('src/pages/apps/AppsSection.tsx')
    expect(src).not.toMatch(/pythonDependencies:\s*\[/)
  })
})
