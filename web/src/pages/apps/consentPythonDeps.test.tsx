// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import type { AppCatalogEntry, AppInstallResult, AppPythonDependency } from '../../lib/api'

const previewApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: () => Promise.reject(new Error('nothing is confirmed in this file')),
    updateApp: () => Promise.reject(new Error('nothing is confirmed in this file')),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: vi.fn() }))

// Imported after the mocks so the dialog binds them.
import { PermissionList, disclosureOf } from './installConsent'
import { InstallDialogHarness } from '../../test/installDialogHarness'

// ── Installing an app pip-installs packages the GATEWAY loads into its own process, and
//    consent never said so ──────────────────────────────────────────────────────────────
//
// Measured on a fresh `python:3.13-slim` container running a wheel built from this branch:
// of nine apps installed through the Store, FOUR declared `dependencies.pythonDependencies`
// and each `pip install`ed a package the gateway would load — `anthropic`, `openai`,
// `slack_sdk`, and a `Pillow>=10,<13` pin the installer evaluates against core's own. The
// consent dialog enumerated gateway permissions, app messaging, desktop capabilities,
// network reach and dashboard code, and said *"Installing fetches this app behind the
// security scanner"* — and nothing at all about a third-party package entering the
// interpreter that holds the owner's credentials, filesystem and network reach.
//
// `docs/security/limitations.md` §3 documents where they go and what they reach. That is not the
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
// 🔑 WHY IT LIVES INSIDE `PermissionList`. That is what puts it on the install dialog and the Store
// detail panel by construction — both render `AppDisclosureView`, the one pre-install disclosure —
// rather than by each caller remembering a prop, the mechanism that failed here before ("a comment
// asking four callers to remember is what failed here").

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
    // `apps/app_python.py` is the source of this framing, deliberately rather than a second
    // vocabulary invented here: the packages go to `<home>/app-python`, and the gateway
    // APPENDS that directory to its import path — so they can add code, never replace a
    // package it (or another app) already uses, and they run in-process once loaded.
    expect(t).toMatch(/into your PersonalClaw data folder \(app-python\)/)
    expect(t).toMatch(/loads those packages into its own process, after its own/)
    expect(t).toMatch(/never replace a package PersonalClaw or another app already uses/)
    expect(t).toMatch(/importable by PersonalClaw itself and by every other app you install/)
    // And the consequence a permission list cannot express: nothing above bounds it.
    expect(t).toMatch(/none of the permissions above bound it/)
    expect(t).toMatch(/pip install/)
    // 🔴 The two sentences this row used to say are no longer TRUE, so they must be gone:
    // packages no longer go into the gateway's own virtualenv, and "no per-app site-packages"
    // described that shared venv.
    expect(t).not.toMatch(/virtualenv/)
    expect(t).not.toMatch(/site-packages/)
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
    // whole point of the split. A row that said "pip install … the gateway loads those
    // packages" about `Pillow>=10,<13` would be false.
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

  it('reaches the install dialog — the screen with the button that acts on it', async () => {
    // The server's review of an app that pip-installs a package — what `POST /api/apps/preview`
    // reads from the staged manifest, so a registry listing discloses it exactly as a local card.
    previewApp.mockResolvedValue({
      ok: false, name: 'slack-channel', error: '', needs_consent: true,
      scan: { verdict: 'warning', tier: 'community', findings: [], signature: null },
      displayName: 'Slack Channel', version: '0.1.0', previous: null, consent: 's'.repeat(64),
      disclosure: {
        permissions: {}, crons: [], hasUI: false, uiComponents: '', hasBackend: false,
        onInstall: '', onUpdate: '', mcpServers: [],
    backendSandbox: '', providers: [], onEnable: '', onDisable: '', onUninstall: '',
    cliSetup: '', cliDoctor: '', sources: [], skills: [], runsAsYou: '',
        pythonDependencies: deps({ spec: 'slack-sdk>=3.27,<4', coreOwned: false }),
      },
    } satisfies AppInstallResult)
    render(<InstallDialogHarness target={{ source: '/apps/slack-channel', label: 'slack-channel' }} />)
    await waitFor(() => expect(screen.getByRole('dialog').textContent).toMatch(/Security scan:/))
    const dialog = screen.getByRole('dialog')
    // 🔴 PRESENCE IS NOT THE POINT — CO-LOCATION IS. #3540 was a correctly-rendered
    // `role="alert"` sitting BEHIND a modal's own backdrop, and jsdom implements no layout, so
    // nothing here can prove visibility. What a jsdom test CAN prove is the structural
    // precondition the browser drive then confirms: the disclosure is inside the dialog element,
    // not on the page behind it.
    const chip = [...dialog.querySelectorAll('code')].find((c) => c.textContent === 'slack-sdk>=3.27,<4')
    expect(chip, 'the specifier must be INSIDE the dialog, not on the page behind it').toBeTruthy()
    expect(text(dialog)).toMatch(/loads those packages into its own process, after its own/)
    // And above the button it informs.
    const confirm = [...dialog.querySelectorAll('button')].find((b) => /Install anyway/.test(b.textContent || ''))
    expect(confirm, 'the consentable branch must still offer the override').toBeTruthy()
    expect(chip!.compareDocumentPosition(confirm!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('disclosureOf is the one catalog authority, and it declines to guess', () => {
  const row = (over: Partial<AppCatalogEntry>) => over as AppCatalogEntry

  it('returns no disclosure at all when the manifest was never read', () => {
    // `consentKnown: false` is the registry-pointer case. The wire ships `pythonDependencies: []`
    // for BOTH that and "declares none", which is exactly why the flag is consulted and not the
    // array's emptiness — and why a pointer's Store panel renders no package row rather than an
    // empty one. (Its install dialog reads the packages from the server's review.)
    expect(disclosureOf(undefined)).toBeUndefined()
    expect(disclosureOf(row({ consentKnown: false, pythonDependencies: [] }))).toBeUndefined()
    expect(disclosureOf(row({ consentKnown: undefined, pythonDependencies: [] }))).toBeUndefined()
  })

  it('returns the list when it was read, and [] for an app declaring none', () => {
    expect(disclosureOf(row({ consentKnown: true, pythonDependencies: [] }))?.pythonDependencies).toEqual([])
    // An absent field on a read manifest is "declares none", not unknown.
    expect(disclosureOf(row({ consentKnown: true }))?.pythonDependencies).toEqual([])
    expect(disclosureOf(row({
      consentKnown: true, pythonDependencies: [{ spec: 'openai>=1.0', coreOwned: false }],
    }))?.pythonDependencies).toEqual([{ spec: 'openai>=1.0', coreOwned: false }])
  })
})

// ── The call-site rail ────────────────────────────────────────────────────────────────
//
// A disclosure that renders correctly and is never handed its data is not a disclosure, and
// `PermissionList` deliberately fails SILENT on an omitted `pythonDeps`. So a source rail is the
// only thing standing between that design and the defect returning one surface at a time. It is a
// CENSUS of every production file, not a list of known ones: a new surface that renders its own
// `PermissionList` is exactly the regression this exists to catch. `consentHostUiRendered.test.ts`
// counts the same renders for the host-page fact.

const SRC = join(process.cwd(), 'src')

/** Source with comments stripped, so the prose explaining the fix cannot satisfy — or trip —
 *  a count. A rail measures the program, not the explanation of it. */
const strip = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function productionFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return n === 'test' ? [] : productionFiles(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })
}

/** Every `<PermissionList …/>` in production code, by file. */
function permissionListRenders(): { rel: string; tag: string }[] {
  return productionFiles(SRC).flatMap((abs) =>
    [...strip(readFileSync(abs, 'utf8')).matchAll(/<PermissionList\b[\s\S]{0,300}?\/>/g)]
      .map((m) => ({ rel: abs.slice(SRC.length + 1), tag: m[0] })))
}

describe('every consent surface is handed the Python-dependency fact', () => {
  it('the comment stripper actually strips (or every count below is unfalsifiable)', () => {
    // The control this file's own rail needs: prove `strip()` removes both comment forms, so a
    // green below cannot be a green earned by a docstring.
    const raw = readFileSync(join(SRC, 'pages/apps/installConsent.tsx'), 'utf8')
    expect(strip(raw).length, 'stripping removed nothing — the regexes are wrong').toBeLessThan(raw.length)
    expect(strip(raw)).not.toMatch(/🔑/)
  })

  it('the one pre-install disclosure passes the review’s packages, and only the installed panel omits them', () => {
    const renders = permissionListRenders()
    const withFact = renders.filter((r) => /pythonDeps=\{disclosure\.pythonDependencies\}/.test(r.tag))
    const without = renders.filter((r) => !/pythonDeps=/.test(r.tag))
    // `AppDisclosureView` — what the install dialog AND the Store detail panel render.
    expect(withFact.map((r) => r.rel), 'the disclosure every pre-install surface renders').toEqual(['pages/apps/installConsent.tsx'])
    // The INSTALLED-app panel, whose wire (`AppSummary`) has no such field. By then the packages
    // are already installed and this is an inventory, not a consent surface — omitting the row
    // there is honest.
    expect(without.map((r) => r.rel), 'only the installed-app panel may omit it').toEqual(['pages/apps/AppsSection.tsx'])
    expect(renders, 'no third PermissionList anywhere — a new surface renders AppDisclosureView').toHaveLength(2)
  })

  it('no surface fabricates the answer it is about to disclose', () => {
    // The `hasUI: false` defect (#492's follow-up) in reverse: `storeUniverse` normalised a
    // catalog row with a hard-coded value and the panel then asserted it. A hard-coded
    // `pythonDependencies` would be worse, because the honest empty case renders NOTHING and so
    // a fabricated `[]` is indistinguishable from a correct one on screen.
    for (const rel of ['pages/apps/AppsSection.tsx', 'app/onboarding/EssentialsStep.tsx']) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), rel).not.toMatch(/pythonDependencies:\s*\[/)
    }
  })
})
