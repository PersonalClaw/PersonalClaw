// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import type { AppCatalogEntry, AppDisclosure, AppInstallResult, AppScanReport } from '../../lib/api'

// ── The install-consent dialog's states have to read correctly ───────────────────────────────────
//
// SH-3 landed this surface with no rail of its own, and it is the surface a user consents over a
// supply-chain decision on. Every Store install now opens it — a clean scan included — so each of
// its branches is driven here through the SHIPPED hook and dialog (`test/installDialogHarness`),
// with only the server's review (`POST /api/apps/preview`) stubbed.
//
// 🔑 WHAT AN EARLIER SWEEP FOUND CLEAN, so a later pass does not re-audit it: axe (wcag2a/aa, 21a/aa,
// 22aa) reported **0 serious or critical** on the dialog in all four branches × both themes; every
// control is ≥24px (2.5.8); `aria-modal="true"` is present.
//
// 🪤 AND ONE "FINDING" WAS THE INSTRUMENT. A probe first reported the 20px dialog title at 1.27:1 in
// light. The title is `rgb(31,31,31)` on a header painted `oklab(0.999994 … / 0.95)` — essentially
// white, so the real ratio is ~18:1. The probe's colour parser took the first three numbers out of
// `oklab(…)` and read them as RGB. **A contrast number is only as good as the colour-space parsing
// behind it.**
//
// ── What this rail pins ───────────────────────────────────────────────────────────────────────────
//
// 1. THE DISMISS VERB. On a terminal refusal the footer has exactly one button and nothing to cancel —
//    the server already refused. "Cancel" claims a pending action is being abandoned and invites the
//    reading that the app might otherwise still install. `Done` is the shipped verb for a dismiss-only
//    footer (this dialog's client-install branch, `chat/SessionSkillsReview`, `ChatPage`).
//
// 2. THE SENTENCE BOUNDARY. `app_manager` composes the client-install reason without terminal
//    punctuation ("'<name>' installs on your local machine, not this server") and this surface appends
//    "Run this in your terminal:" to it, which rendered as one run-on line.
//
// 🪤 DELIBERATELY NOT CHANGED, with the reason, so the next pass does not "finish" it:
//   • The refusal reason appears TWICE on an invalid-signature refusal — once in the lead sentence
//     from `terminalRefusalReason`, once in `SignatureRow`'s red detail line. The dialog is now the
//     only APP surface either appears on, but `terminalRefusalReason` also words the skills
//     marketplace's refusal (through `isBlockingResult`), which has no `SignatureRow` — so trimming
//     the lead sentence would take the reason off that surface entirely.
//   • "Security scan: clean" renders in green ABOVE "Invalid signature — install refused" in red.
//     Provenance and content are two different questions; showing only one invites "it scanned
//     clean" to be read as "it is from who it says". The blocking sentence leads the dialog, so the
//     green line cannot be read as the verdict on the install.

const previewApp = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    previewApp: (...a: unknown[]) => previewApp(...a),
    installApp: () => Promise.reject(new Error('this rail never confirms')),
    updateApp: () => Promise.reject(new Error('this rail never confirms')),
  },
}))
vi.mock('../../app/appSdk', () => ({ launchChat: vi.fn() }))

// Imported after the mocks so the dialog binds them.
import { disclosureOf } from './installConsent'
import { InstallDialogHarness } from '../../test/installDialogHarness'

const UNSIGNED = { state: 'unsigned', signer: '', reason: '' }

/** An app that gets nothing — each case adds only the grants it is about. */
const NOTHING: AppDisclosure = {
  permissions: {}, crons: [], pythonDependencies: [], hasUI: false, uiComponents: '',
  hasBackend: false, onInstall: '', onUpdate: '', mcpServers: [],
    backendSandbox: '', providers: [], onEnable: '', onDisable: '', onUninstall: '',
    cliSetup: '', cliDoctor: '', sources: [], skills: [], runsAsYou: '',
}

const scan = (over: Partial<AppScanReport> = {}): AppScanReport => ({
  verdict: 'warning', tier: 'community', findings: [], signature: null, ...over,
})

/** The server's review of `demo-app` — what `POST /api/apps/preview` answers. */
function review(over: Partial<AppInstallResult> = {}, grants: Partial<AppDisclosure> = {}): AppInstallResult {
  return {
    ok: false, name: 'demo-app', error: '', needs_consent: true, scan: scan(),
    displayName: 'Demo App', version: '1.0.0', disclosure: { ...NOTHING, ...grants }, previous: null,
    consent: 'c'.repeat(64), ...over,
  }
}

/** The server's answer for a TERMINAL refusal: the review, with no consent to give. */
const refused = (s: AppScanReport, grants: Partial<AppDisclosure> = {}) =>
  review({ needs_consent: false, consent: undefined, error: 'install refused', scan: s }, grants)

/** Open the one install dialog on the server's review `r`, past its "Checking…" line. */
async function dialogFor(r: AppInstallResult): Promise<HTMLElement> {
  previewApp.mockResolvedValue(r)
  render(<InstallDialogHarness target={{ source: '/apps/demo-app', label: 'demo-app' }} />)
  await waitFor(() => expect(screen.getByRole('dialog').textContent).not.toMatch(/Nothing is installed yet/))
  return screen.getByRole('dialog')
}

/** The dialog renders into a portal; whitespace-normalised so a sentence split across JSX lines reads whole. */
const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s+/g, ' ')

const footerButtons = () =>
  screen.getAllByRole('button').map((b) => (b.textContent || '').trim()).filter(Boolean)

beforeEach(() => previewApp.mockReset())
afterEach(cleanup)

describe('the consent dialog offers an override only when one exists', () => {
  it('a consentable warning keeps Cancel, because there is a pending action to abandon', async () => {
    await dialogFor(review({ scan: scan({ signature: UNSIGNED }) }))
    const names = footerButtons()
    expect(names.some((n) => /Install anyway/.test(n)), 'the override is offered').toBe(true)
    expect(names).toContain('Cancel')
    expect(names).not.toContain('Done')
  })

  it('a clean scan still asks — Install, never "Install anyway"', async () => {
    await dialogFor(review({ scan: scan({ verdict: 'clean', signature: UNSIGNED }) }))
    const names = footerButtons()
    expect(names).toContain('Install')
    expect(names.some((n) => /anyway/.test(n)), 'nothing to override on a clean scan').toBe(false)
    expect(names).toContain('Cancel')
  })

  it('a dangerous verdict is dismiss-only, so its button says Done', async () => {
    await dialogFor(refused(scan({ verdict: 'dangerous' })))
    const names = footerButtons()
    expect(names.some((n) => /Install/.test(n)), 'no install of any kind on a terminal refusal').toBe(false)
    expect(names, 'nothing to cancel — the install was already refused').not.toContain('Cancel')
    expect(names).toContain('Done')
  })

  it('an invalid signature is dismiss-only too — a refusal by PROVENANCE, not content', async () => {
    await dialogFor(refused(scan({
      verdict: 'clean',
      signature: { state: 'invalid', signer: 'PersonalClaw Apps', reason: 'digest mismatch for server/provider.py' },
    })))
    const names = footerButtons()
    expect(names.some((n) => /Install/.test(n))).toBe(false)
    expect(names).not.toContain('Cancel')
    expect(names).toContain('Done')
    // The reason must still reach the user — and it reaches them TWICE, which is the redundancy
    // deliberately kept (see the header). Pinned as a count so it is a recorded fact: if a later
    // pass de-duplicates it, this number changes on purpose rather than silently, and if a refactor
    // drops BOTH copies the user loses the only explanation of the refusal.
    expect(screen.getAllByText(/digest mismatch for server\/provider\.py/), 'lead sentence + SignatureRow detail').toHaveLength(2)
  })
})

describe('the client-install branch reads as two sentences', () => {
  const CI = { shell: 'curl -fsSL https://example.invalid/install.sh | sh', postInstall: 'open -a "Demo"' }
  const directive = (error: string) =>
    review({ needs_consent: false, consent: undefined, needs_client_install: true, client_install: CI, error })

  it("closes the server's unpunctuated reason before appending the instruction", async () => {
    const dialog = await dialogFor(directive("'demo-app' installs on your local machine, not this server"))
    expect(text(dialog), 'the run-on this fixes').not.toMatch(/not this server Run this in your terminal/)
    expect(text(dialog)).toMatch(/not this server\. Run this in your terminal:/)
  })

  it('does not double-punctuate a reason that already ends in one', async () => {
    // The hard-coded fallback already ends in a period; the helper must be idempotent.
    const dialog = await dialogFor(directive(''))
    expect(text(dialog)).toMatch(/not this server\. Run this in your terminal:/)
    expect(text(dialog)).not.toMatch(/\.\. Run this/)
  })

  it('is dismiss-only, and offers no install of its own', async () => {
    await dialogFor(directive('x'))
    // This branch is the precedent the refusal branches were converged onto.
    expect(footerButtons()).toContain('Done')
    expect(footerButtons().some((n) => /^Install/.test(n)), 'the command runs on your machine, not from here').toBe(false)
  })
})

// ── One screen cannot say both that the app installs and that it cannot ──────────────────────────
//
// The unsigned-signature note ended "It still installs — the security scan above is what gates it."
// unconditionally, and on a `dangerous` verdict that sentence landed about five lines above "This
// app is blocked — dangerous content cannot be installed." Pinned in BOTH directions, because the
// sentence is CORRECT on a consentable warning: a test that only forbade it on a refusal would pass
// just as well if the note were deleted, and deleting it would drop the reassurance an unsigned
// community app is owed.
describe('the unsigned note agrees with the verdict beside it', () => {
  it('a consentable WARNING still says the missing signature does not stop the install', async () => {
    const dialog = await dialogFor(review({ scan: scan({ verdict: 'warning', signature: UNSIGNED }) }))
    expect(text(dialog), 'an unsigned community app is normal and the note says so').toMatch(/It still installs/)
    expect(text(dialog)).not.toMatch(/cannot be installed/)
  })

  it('a DANGEROUS refusal never claims the app installs', async () => {
    const dialog = await dialogFor(refused(scan({ verdict: 'dangerous', signature: UNSIGNED })))
    expect(text(dialog), 'the contradiction this fixes').not.toMatch(/It still installs/)
    // The refusal itself is untouched — the fix is to the copy, never to the gate.
    expect(text(dialog)).toMatch(/This app is blocked — dangerous content cannot be installed\./)
    // …and the unsignedness is still disclosed, reframed as not being the reason.
    expect(text(dialog)).toMatch(/No maintainer signature/)
    expect(text(dialog)).toMatch(/not why this install was refused/)
  })
})

// ── The grants are part of consent, on every path ────────────────────────────────────────────────
//
// The consent screen used to render the scan report alone on two of its paths, and on a clean scan
// it did not render at all. What it discloses now is the SERVER'S reading of the bytes about to be
// installed, on the same screen as the button that installs them.
describe('the dialog discloses the grants, not only the scan', () => {
  it('renders the enforced permissions and the scheduled jobs beside the findings', async () => {
    const dialog = await dialogFor(review({}, {
      permissions: { api: ['/api/knowledge'], cron: true, network: true },
      crons: [{ name: 'digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour', agent: 'researcher', message: 'summarise', scheduled: true }],
    }))
    expect(text(dialog)).toMatch(/Permissions the gateway enforces/)
    expect(text(dialog)).toMatch(/API: \/api\/knowledge/)
    expect(text(dialog)).toMatch(/Scheduled jobs/)
    expect(text(dialog)).toMatch(/digest/)
    // The override is still the same explicit click — disclosure was added, nothing was eased.
    expect(footerButtons().some((n) => /Install anyway/.test(n))).toBe(true)
  })

  it('says "none" for a manifest that really declared nothing', async () => {
    // The server read this manifest and it grants nothing — that is a fact, and it is said.
    const dialog = await dialogFor(review())
    expect(text(dialog)).toMatch(/granted no gateway capability/)
  })

  it('discloses the grants on a REFUSAL too — they are why the findings matter', async () => {
    const dialog = await dialogFor(refused(scan({ verdict: 'dangerous' }), { permissions: { agent: true } }))
    expect(text(dialog)).toMatch(/Run background agents/)
    expect(footerButtons().some((n) => /Install/.test(n)), 'still terminal').toBe(false)
  })
})

// ── A crontab line is not a disclosure ──────────────────────────────────────────────────────────
describe('the scheduled-job row reads as a schedule', () => {
  const rowFor = (cron: Record<string, unknown>) =>
    dialogFor(review({}, { permissions: { cron: true }, crons: [{ scheduled: true, ...cron } as never] }))

  it('shows the words and keeps the exact expression as the row title', async () => {
    const dialog = await rowFor({ name: 'digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour' })
    const cadence = dialog.querySelector('[title="cron: 23 * * * *"]')
    expect(cadence, 'the raw expression is kept, not discarded').toBeTruthy()
    expect(cadence!.textContent).toBe('At 23 minutes past the hour')
    expect(dialog.textContent || '').not.toMatch(/23 \* \* \* \*/)
  })

  it('falls back to the expression when the server could not word it', async () => {
    // Honest last resort: an undescribable expression is more informative than a vague
    // "on a schedule", and repeating it in a tooltip would be noise.
    const dialog = await rowFor({ name: 'odd', cron_expr: '@reboot', cadence: '' })
    expect(dialog.textContent || '').toMatch(/@reboot/)
    expect(dialog.querySelector('[title^="cron:"]')).toBeNull()
  })

  it('still words the every-seconds form itself', async () => {
    const dialog = await rowFor({ name: 'poll', every: 3600 })
    expect(dialog.textContent || '').toMatch(/every hour/)
  })

  it('a job the app has no permission to schedule is disclosed as one that will not run', async () => {
    // `scheduled` is the server's reading (`app_crons.schedules`, the predicate the trigger store
    // registers by): declared without the `cron` grant, the job is inert, and "installing turns on
    // a scheduled job" would be a false sentence about it.
    const dialog = await dialogFor(review({}, {
      permissions: {}, crons: [{ name: 'digest', every: 3600, scheduled: false }],
    }))
    expect(text(dialog)).not.toMatch(/Installing turns on/)
    // "This job", not "One more": with nothing else scheduled there is no "more".
    expect(text(dialog)).toMatch(/This job is declared but will not run: the app does not have the Scheduled jobs permission/)
    expect(text(dialog)).toMatch(/off · every hour/)
  })

  it('a mixed list says how many MORE will not run beside the ones that will', async () => {
    const dialog = await dialogFor(review({}, {
      permissions: { cron: true },
      crons: [{ name: 'digest', every: 3600, scheduled: true }, { name: '', every: 0, scheduled: false }],
    }))
    expect(text(dialog)).toMatch(/Installing turns on a scheduled job/)
    expect(text(dialog)).toMatch(/One more is declared but will not run/)
  })
})

// ── disclosureOf: "declared none" and "not known yet" are different answers ──────────────────────
//
// The install dialog no longer needs this distinction — it discloses the server's reading of the
// bytes, which exists for a registry pointer too. The STORE DETAIL PANEL still does: it shows what a
// catalog row says an app gets before any review, and `CatalogEntry.to_dict` is `asdict`, so EVERY
// row ships `permissions: {}` — for a declared-none manifest AND for a registry pointer whose
// manifest is not read until install. `{}` is truthy, so a guard on the object alone asserted
// "granted no gateway capability" about an app nobody had read (issue 614, the opposite direction).
// `consentKnown` is the one authority; `disclosureOf` is the one place it is consulted.
describe('disclosureOf separates declared-none from not-known-yet', () => {
  const row = (over: Partial<AppCatalogEntry>) => over as AppCatalogEntry

  it('a scanned manifest that declared nothing is KNOWN and empty', () => {
    const d = disclosureOf(row({ consentKnown: true, permissions: {} }))
    expect(d?.permissions).toEqual({})
    expect(d?.crons).toEqual([])
    expect(d?.pythonDependencies, 'an absent field on a read manifest is "declares none"').toEqual([])
  })

  it('a scanned manifest carries its grants through unchanged', () => {
    const perms = { storage: true }
    expect(disclosureOf(row({ consentKnown: true, permissions: perms }))?.permissions).toBe(perms)
  })

  it('a registry pointer is NOT known, even though its permissions are {} on the wire', () => {
    expect(disclosureOf(row({ consentKnown: false, permissions: {}, pythonDependencies: [] }))).toBeUndefined()
  })

  it('a row from before the flag existed, and no row at all, are both not-known', () => {
    expect(disclosureOf(row({ permissions: {} }))).toBeUndefined()
    expect(disclosureOf(undefined)).toBeUndefined()
  })
})
