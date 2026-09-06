import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ConsentModal } from './installConsent'
import type { GuardedResult } from '../../lib/useGuardedInstall'

// ── The install-consent modal's BLOCKED states have to read correctly ──────────────────────────────
//
// SH-3 landed this surface with no rail of its own, and it is the surface a user consents over a
// supply-chain decision on. It is also invisible to every route census the loop runs: the modal only
// exists after an install attempt comes back blocked, so `#/apps` in its default state never renders
// it. Driven for the first time via stubbed `POST /api/apps` responses
// (`.validation/ux/probes/install-consent-modal.mjs`), all four branches at both themes.
//
// 🔑 WHAT THE SWEEP FOUND CLEAN, so a later pass does not re-audit it: axe (wcag2a/aa, 21a/aa, 22aa)
// reported **0 serious or critical** on the dialog in all four branches × both themes; every control
// is ≥24px (2.5.8); `aria-modal="true"` is present; and nothing inside the dialog actually scrolls, so
// there is no unnamed/unreachable scroll region — the `curl … | sh` one-liner the guide documents
// fits without overflow. The tab strip that reveals the Store was checked too and is correct
// (`role="tablist"` + `aria-label` + `aria-selected` + roving `tabindex`).
//
// 🪤 AND ONE "FINDING" WAS MY OWN INSTRUMENT. The probe first reported the 20px dialog title at
// **1.27:1** in light. The title is `rgb(31,31,31)` on a header painted
// `oklab(0.999994 … / 0.95)` — essentially white, so the real ratio is ~18:1. The probe's colour
// parser took the first three numbers out of `oklab(…)` and read them as RGB. **A contrast number is
// only as good as the colour-space parsing behind it.**
//
// ── What this rail pins ───────────────────────────────────────────────────────────────────────────
//
// 1. THE DISMISS VERB. On a terminal refusal the footer has exactly one button and nothing to cancel —
//    the install was already refused server-side. "Cancel" claims a pending action is being abandoned
//    and invites the reading that the app might otherwise still install. Censused the app's modal
//    footers: `Done` ×3 (this file's client-install branch, `chat/SessionSkillsReview`, `ChatPage`),
//    `Close` ×1 (`code/CodeCockpitPage`, beside a "Try again"), and `Cancel` ×11 — every one of which
//    sits next to a committing button. So `Done` is the shipped verb for a dismiss-only footer and
//    this is convergence, not invention.
//
//    🪤 `AppsSection`'s install and update modals look like the same shape (`Cancel` + a conditional
//    `Install anyway`) but are NOT: their else-branch always renders a commit button — disabled, with
//    the refusal as its `disabledReason` — so those footers are never dismiss-only and "Cancel" is
//    correct there. Read both branches before calling two footers the same footer.
//
// 2. THE SENTENCE BOUNDARY. `app_manager` composes the client-install reason without terminal
//    punctuation ("'<name>' installs on your local machine, not this server") and this surface appends
//    "Run this in your terminal:" to it, which rendered as one run-on line. Only the hard-coded
//    fallback ends in a period, which is why the seam is invisible in review and appears only on the
//    real path.
//
// 🪤 DELIBERATELY NOT CHANGED, with the reason, so the next pass does not "finish" it:
//   • The refusal reason appears TWICE on an invalid-signature refusal — once in the lead sentence
//     from `terminalRefusalReason`, once in `SignatureRow`'s red detail line. Removing either is not a
//     local edit: `AppsSection`'s modals surface that same reason only as a button `disabledReason`
//     tooltip, so `SignatureRow`'s line is the ONLY place a user reads it there. Deciding where the
//     reason canonically lives spans three surfaces and a shared security-copy function — recorded,
//     not guessed.
//   • "Security scan: clean" renders in green ABOVE "Invalid signature — install refused" in red.
//     That ordering is deliberate per the component's own docstring: provenance and content are two
//     different questions, and showing only one invites "it scanned clean" to be read as "it is from
//     who it says". The blocking sentence already leads the modal, so the green line cannot be read as
//     the verdict on the install.

const scan = (over: Record<string, unknown> = {}) => ({
  verdict: 'warning', findings: [], signature: null, ...over,
}) as NonNullable<GuardedResult['scan']>

const guarded = (over: Partial<GuardedResult> = {}): GuardedResult => ({
  ok: false, needsConsent: false, scan: null, ...over,
})

const footerButtons = () =>
  screen.getAllByRole('button').map((b) => (b.textContent || '').trim()).filter(Boolean)

describe('the consent modal offers an override only when one exists', () => {
  it('a consentable warning keeps Cancel, because there is a pending action to abandon', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ needsConsent: true, scan: scan({ signature: { state: 'unsigned', signer: '', reason: '' } }) })} />)
    const names = footerButtons()
    expect(names.some((n) => /Install anyway/.test(n)), 'the override is offered').toBe(true)
    expect(names).toContain('Cancel')
    expect(names).not.toContain('Done')
  })

  it('a dangerous verdict is dismiss-only, so its button says Done', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ scan: scan({ verdict: 'dangerous' }) })} />)
    const names = footerButtons()
    expect(names.some((n) => /Install anyway/.test(n)), 'no override on a terminal refusal').toBe(false)
    expect(names, 'nothing to cancel — the install was already refused').not.toContain('Cancel')
    expect(names).toContain('Done')
  })

  it('an invalid signature is dismiss-only too — a refusal by PROVENANCE, not content', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ scan: scan({ verdict: 'clean', signature: { state: 'invalid', signer: 'PersonalClaw Apps', reason: 'digest mismatch for server/provider.py' } }) })} />)
    const names = footerButtons()
    expect(names.some((n) => /Install anyway/.test(n))).toBe(false)
    expect(names).not.toContain('Cancel')
    expect(names).toContain('Done')
    // The reason must still reach the user — and it reaches them TWICE, which is the redundancy this
    // cycle deliberately did not fix (see the header). Pinned as a count so it is a recorded fact: if
    // a later pass de-duplicates it, this number changes on purpose rather than silently, and if a
    // refactor drops BOTH copies the user loses the only explanation of the refusal.
    expect(screen.getAllByText(/digest mismatch for server\/provider\.py/), 'lead sentence + SignatureRow detail').toHaveLength(2)
  })
})

describe('the client-install branch reads as two sentences', () => {
  const CI = { shell: 'curl -fsSL https://example.invalid/install.sh | sh', postInstall: 'open -a "Demo"' }

  it("closes the server's unpunctuated reason before appending the instruction", () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ clientInstall: CI, error: "'demo-app' installs on your local machine, not this server" })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text, 'the run-on this fixes').not.toMatch(/not this server Run this in your terminal/)
    expect(text).toMatch(/not this server\. Run this in your terminal:/)
  })

  it('does not double-punctuate a reason that already ends in one', () => {
    // The hard-coded fallback already ends in a period; the helper must be idempotent.
    const { container } = render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ clientInstall: CI, error: '' })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/not this server\. Run this in your terminal:/)
    expect(text).not.toMatch(/\.\. Run this/)
  })

  it('is dismiss-only, and already said Done before this cycle', () => {
    render(<ConsentModal label="demo-app" busy={false} onConfirm={() => {}} onClose={() => {}}
      permissions={undefined} crons={undefined}
      result={guarded({ clientInstall: CI, error: 'x' })} />)
    // This branch is the precedent the refusal branches were converged onto.
    expect(footerButtons()).toContain('Done')
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
const UNSIGNED = { state: 'unsigned', signer: '', reason: '' }

describe('the unsigned note agrees with the verdict beside it', () => {
  const modalText = (verdict: string) => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={undefined} crons={undefined} onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: verdict === 'warning', scan: scan({ verdict, signature: UNSIGNED }) })} />)
    return (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
  }

  it('a consentable WARNING still says the missing signature does not stop the install', () => {
    const text = modalText('warning')
    expect(text, 'an unsigned community app is normal and the note says so').toMatch(/It still installs/)
    expect(text).not.toMatch(/cannot be installed/)
  })

  it('a DANGEROUS refusal never claims the app installs', () => {
    const text = modalText('dangerous')
    expect(text, 'the contradiction this fixes').not.toMatch(/It still installs/)
    // The refusal itself is untouched — the fix is to the copy, never to the gate.
    expect(text).toMatch(/This app is blocked — dangerous content cannot be installed\./)
    // …and the unsignedness is still disclosed, reframed as not being the reason.
    expect(text).toMatch(/No maintainer signature/)
    expect(text).toMatch(/not why this install was refused/)
  })
})

// ── The grants are part of consent, on every path ────────────────────────────────────────────────
//
// The modal used to render the scan report alone, so two of its four callers (the Store card's
// Install and Manage Sources → Install) asked for consent while disclosing nothing about what the
// app is PERMITTED to do or what it will RUN unattended. The bullets now live inside the modal, on
// the same screen as "Install anyway".
describe('the consent modal discloses the grants, not only the scan', () => {
  it('renders the enforced permissions and the scheduled jobs beside the findings', () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={{ api: ['/api/knowledge'], cron: true, network: true }}
      crons={[{ name: 'digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour', agent: 'researcher', message: 'summarise' }]}
      onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'warning' }) })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/Permissions the gateway enforces/)
    expect(text).toMatch(/API: \/api\/knowledge/)
    expect(text).toMatch(/Scheduled jobs/)
    expect(text).toMatch(/digest/)
    // The override is still the same explicit click — disclosure was added, nothing was eased.
    expect(footerButtons().some((n) => /Install anyway/.test(n))).toBe(true)
  })

  it('says the grants are unknown rather than rendering silence', () => {
    // A bare source URL the catalog has not indexed genuinely has no manifest yet. An empty
    // permission list there would read as "this app is granted nothing", which is a claim.
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={undefined} crons={undefined} onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'warning' }) })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/could not read this app's declared permissions/)
    expect(text, 'an unknown grant list must not be dressed as an empty one')
      .not.toMatch(/granted no gateway capability/)
  })

  it('discloses the grants on a REFUSAL too — they are why the findings matter', () => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={{ agent: true }} crons={undefined} onConfirm={() => {}} onClose={() => {}}
      result={guarded({ scan: scan({ verdict: 'dangerous' }) })} />)
    const text = (container.ownerDocument.body.textContent || '').replace(/\s+/g, ' ')
    expect(text).toMatch(/Run background agents/)
    expect(footerButtons().some((n) => /Install anyway/.test(n)), 'still terminal').toBe(false)
  })
})

// ── A crontab line is not a disclosure ──────────────────────────────────────────────────────────
describe('the scheduled-job row reads as a schedule', () => {
  // The modal renders into a portal, so the assertions read the document body, not `container`.
  const row = (cron: Record<string, unknown>) => {
    const { container } = render(<ConsentModal label="demo-app" busy={false}
      permissions={{ cron: true }} crons={[cron as never]}
      onConfirm={() => {}} onClose={() => {}}
      result={guarded({ needsConsent: true, scan: scan({ verdict: 'warning' }) })} />)
    return container.ownerDocument.body
  }

  it('shows the words and keeps the exact expression as the row title', () => {
    const c = row({ name: 'digest', cron_expr: '23 * * * *', cadence: 'At 23 minutes past the hour' })
    const cadence = c.querySelector('[title="cron: 23 * * * *"]')
    expect(cadence, 'the raw expression is kept, not discarded').toBeTruthy()
    expect(cadence!.textContent).toBe('At 23 minutes past the hour')
    expect((c.textContent || '')).not.toMatch(/23 \* \* \* \*/)
  })

  it('falls back to the expression when the server could not word it', () => {
    // Honest last resort: an undescribable expression is more informative than a vague
    // "on a schedule", and repeating it in a tooltip would be noise.
    const c = row({ name: 'odd', cron_expr: '@reboot', cadence: '' })
    expect((c.textContent || '')).toMatch(/@reboot/)
    expect(c.querySelector('[title^="cron:"]')).toBeNull()
  })

  it('still words the every-seconds form itself', () => {
    const c = row({ name: 'poll', every: 3600 })
    expect((c.textContent || '')).toMatch(/every hour/)
  })
})
