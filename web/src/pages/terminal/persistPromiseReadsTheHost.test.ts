import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The persistence promise reads the HOST, not the flag it just wrote ──────────────────────────
//
// `#545`. The backend gates correctly — `_persist_enabled` is `config flag AND tmux_available()`
// — but published only the flag, so `TerminalPage` derived its hint from `dashboard.terminal.persist`
// alone and rendered "Sessions are tmux-backed, so they survive a restart." on a host with no tmux,
// where `_persist_enabled` is False and every session dies with the gateway. The control reported
// the value it had written rather than the capability.
//
// `persist_available` (the host fact) rides on `GET /api/terminal/sessions` — asserted on the
// backend by `tests/test_terminal_persist_capability.py`. This is the CLIENT half: the page must
// actually read it and branch the promise on it. Adoption, not availability — a published field
// with no reader is the same defect wearing a new field name.
//
// 🔑 WHAT MOVED, AND WHY THIS RAIL MOVED WITH IT. The sentences used to be spelled INSIDE this
// page, so this file asserted on its literals. They now live in `lib/persistClaim`, the single
// owner of the fact AND every sentence derived from it, because branching only the hint here left
// the control ENABLED and LIT on a tmux-less host and left Settings › Agent stating the same
// requirement in its own words. So the assertions below are in two halves — the page must go
// through the owner and hand it the host fact, and the owner must be the thing that branches —
// which is strictly more than pinning strings in one file could say. `persistClaim.test.ts` owns
// the three-state contract itself; `persistClaimHasOneOwner.test.ts` forbids a third surface from
// spelling the claim inline again.
//
// 🪤 ABSENT IS NOT `false` AT THE WIRE, AND IS `false` AT THE CLAIM. An older backend does not send
// the key; treating that as "no tmux" invents a limitation, and treating it as `true` re-asserts
// the lie. The reconciliation is `persistAvailableFrom` — one named owner of that decision, which
// is why the page must not re-derive it with its own `=== false` comparison.

const PAGE = 'src/pages/terminal/TerminalPage.tsx'
const OWNER = 'src/lib/persistClaim.ts'

const read = (p: string): string => readFileSync(join(process.cwd(), p), 'utf8')
/** Only rendered code can lie to a user; this file's own comments quote the claim they explain. */
const strip = (raw: string) =>
  raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

describe('the terminal persistence hint reads the host capability', () => {
  it('reads the published host fact off the sessions response, through its owner', () => {
    const src = strip(read(PAGE))
    expect(src, 'the published field must be consumed').toMatch(
      /setPersistAvailable\(\s*persistAvailableFrom\(\s*r\s*\)\s*\)/,
    )
    expect(src, 'and the absent-means-unavailable rule must not be re-derived here')
      .toMatch(/from '\.\.\/\.\.\/lib\/persistClaim'/)
  })

  it('states nothing itself — every string comes from the owner', () => {
    const src = strip(read(PAGE))
    expect(src, 'the page must hand the host fact and the flag to the owner').toMatch(
      /persistToggleCopy\(\s*persistAvailable\s*,/,
    )
    // The literals are gone from the page; a re-inlined sentence reds here AND in the census.
    expect(src, 'a survival sentence must not be spelled at the call site')
      .not.toMatch(/they survive a restart|not installed on this host/)
  })

  it('renders the owner’s verdict, including the parts that used to be ignored', () => {
    const src = strip(read(PAGE))
    // Branching only the HINT is what left the control clickable and `aria-pressed` true for an
    // inert setting. All four fields must reach the control.
    for (const field of ['persistCopy.label', 'persistCopy.hint', 'persistCopy.disabled', 'persistCopy.active']) {
      expect(src, `${field} must reach the header control`).toContain(field)
    }
    // `active` must come from the owner, never from the raw flag beside it.
    expect(src, 'the lit state may not be read off the config flag').not.toMatch(/active=\{persist\}/)
  })

  it('the owner branches the claim on a POSITIVE capability, and distinguishes both OFF cases', () => {
    const owner = read(OWNER)
    const claim = 'Sessions are tmux-backed, so they survive a restart.'
    const at = owner.lastIndexOf(claim)
    expect(at, 'the true-case sentence must still exist').toBeGreaterThan(-1)
    // The promise is reachable only after the unavailable branch has returned.
    expect(
      owner.slice(0, at),
      'the survival claim must sit behind the capability check',
    ).toMatch(/if\s*\(!available\)/)
    expect(owner, 'nothing installed').toMatch(/This needs tmux, which is not installed on this host/)
    expect(owner, 'installed but not enabled').toMatch(/Enabling keeps them alive with tmux/)
  })
})
