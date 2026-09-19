import { describe, expect, it } from 'vitest'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// ── ONE OWNER FOR "SESSIONS SURVIVE A RESTART" ────────────────────────────────────────────────
//
// Issue 545: the terminal header promised "Sessions are tmux-backed, so they survive a restart"
// on a host with no tmux. The backend was never wrong — `_persist_enabled()` ANDs the config flag
// with a live tmux probe and returns False without it — but the frontend had its OWN derivation
// of the same claim: `Boolean(cfg.dashboard.terminal.persist)`, the flag it had just written.
//
// Publishing `persist_available` fixed the headline and left the SHAPE intact: the sentences were
// still spelled at the call site, in two places. Settings › Agent's "Durable worker sessions"
// hint had the softer version of the same fault — "Requires the tmux binary; without it this has
// no effect", naming the requirement without ever consulting it, 20 lines from a page that knew.
//
// 🔑 SO THE CLAIM ITSELF IS OWNED. `lib/persistClaim.ts` holds the fact (`persist_available`,
// published by `GET /api/terminal/sessions` from the identical call the gate makes) AND every
// sentence about it, behind functions that take the capability as an argument. This rail is what
// stops a THIRD surface from spelling the promise inline again.
//
// Derived, not allowlisted: it walks the tree and matches on the claim's vocabulary. Tests are
// excluded as a CATEGORY (a rail has to be able to quote the thing it forbids), never
// file-by-file — an exemption list is how this class of rule rots into a registry of offenders.

const SRC = join(process.cwd(), 'src')
const OWNER = join(SRC, 'lib', 'persistClaim.ts')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.(ts|tsx)$/.test(n) && !/\.(test|doc)\.(ts|tsx)$/.test(n) ? [p] : []
  })

/** A sentence that PROMISES tmux-backed survival: the binary named alongside outliving a restart.
 *  BOTH halves are required, so a passing mention of tmux (a shell command, a provider name) is
 *  not a claim — measured on the tree at the time of writing, the pair matched exactly the two
 *  surfaces this fix is about and nothing else. */
const CLAIM = /tmux/i
const SURVIVAL = /surviv|outliv|persistent session|restart/i

/** Comments may DISCUSS the claim (this fix leaves several explaining it); only rendered text can
 *  lie to a user, so comments come off before anything is matched. */
const strip = (raw: string) => raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function claimants(): string[] {
  const out: string[] = []
  for (const f of walk(SRC)) {
    const code = strip(readFileSync(f, 'utf8'))
    if (CLAIM.test(code) && SURVIVAL.test(code)) out.push(f)
  }
  return out
}

describe('the tmux-survival claim has exactly one owner', () => {
  it('is not vacuous — the owner itself is found by the same scan', () => {
    // Guard the scan's ROOT before its result: run from the repo root instead of `web/` and
    // `src/` resolves to the Python package, where the walk finds no `.ts` at all and every
    // assertion below passes for the wrong reason.
    expect(existsSync(OWNER), `the scan root is wrong — no ${OWNER}`).toBe(true)
    const files = claimants()
    expect(files.length, 'the scan must find something, or it proves nothing').toBeGreaterThan(0)
    expect(files, 'the owner module states the claim, so it must match').toContain(OWNER)
    // And the owner really is reading the published capability, not a config flag.
    const owner = readFileSync(OWNER, 'utf8')
    expect(owner).toMatch(/persist_available/)
    // Comments must be stripped first — the owner's own doc quotes the flag it replaced.
    expect(strip(owner), 'the owner must not derive the capability from the config flag')
      .not.toMatch(/dashboard\??\.\s*terminal|terminal\??\.\s*persist\b/)
  })

  it('no other source states the claim — every surface goes through the owner', () => {
    const strays = claimants()
      .filter((f) => f !== OWNER)
      .map((f) => relative(process.cwd(), f))
    expect(strays, [
      'A tmux-survival promise may only be spelled in lib/persistClaim.ts, whose functions',
      'take the host capability as an argument. Import persistToggleCopy/durableWorkersHint',
      '(or add a function there) instead of writing the sentence at the call site — that is',
      'exactly how issue 545 shipped a label promising what the host could not do.',
    ].join(' ')).toEqual([])
  })

  it('both persistence surfaces consult the owner', () => {
    // Named, because these are the two the audit found — a THIRD one is caught by the scan
    // above, not by extending this list.
    for (const p of ['pages/terminal/TerminalPage.tsx', 'pages/settings/AgentDefaultsPanel.tsx']) {
      const src = readFileSync(join(SRC, p), 'utf8')
      expect(src, `${p} must read the capability from lib/persistClaim`)
        .toMatch(/from '.*lib\/persistClaim'/)
    }
  })
})
