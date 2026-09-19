import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The persistence promise reads the HOST, not the flag it just wrote ──────────────────────────
//
// `#545`. The backend gates correctly — `_persist_enabled` is `config flag AND _tmux_available()`
// — but published only the flag, so `TerminalPage` derived its hint from `dashboard.terminal.persist`
// alone and rendered "Sessions are tmux-backed, so they survive a restart." on a host with no tmux,
// where `_persist_enabled` is False and every session dies with the gateway. The control reported
// the value it had written rather than the capability.
//
// `persist_available` (the host fact) now rides on `GET /api/terminal/sessions` — asserted on the
// backend by `tests/test_terminal_persist_capability.py`. This is the CLIENT half: the page must
// actually read it and branch the promise on it. Adoption, not availability — a published field
// with no reader is the same defect wearing a new field name.
//
// 🪤 `undefined` IS NOT `false`. An older backend does not send the key, and defaulting that to "no
// tmux" would invent a limitation, exactly as defaulting it to `true` re-asserts the lie. Only a
// positive `=== false` may downgrade the claim, which is why the check below is on that comparison
// and not on truthiness.

const PAGE = 'src/pages/terminal/TerminalPage.tsx'

function page(): string {
  return readFileSync(join(process.cwd(), PAGE), 'utf8')
}

describe('the terminal persistence hint reads the host capability', () => {
  it('reads persist_available off the sessions response', () => {
    expect(page(), 'the published host fact must be consumed').toMatch(
      /setPersistAvailable\(\s*r\.persist_available\s*\)/,
    )
  })

  it('branches the survival claim on it, with `=== false` so absent makes no claim', () => {
    const src = page()
    const at = src.indexOf('persistAvailable === false')
    expect(at, 'the downgrade must be gated on a POSITIVE false').toBeGreaterThan(-1)
    // The branch has to reach the tmux sentence, not merely exist somewhere in the file.
    const branch = src.slice(at, at + 500)
    expect(branch, 'the no-tmux branch must say tmux is missing').toMatch(/tmux is not installed/)
  })

  it('no longer claims restart survival unconditionally', () => {
    const src = page()
    const claim = 'Sessions are tmux-backed, so they survive a restart.'
    // 🪤 `lastIndexOf`, NOT `indexOf`. The file's own explanatory comment QUOTES this sentence
    // (that is what the fix is about), and the quote sits ABOVE the guard — so `indexOf` finds the
    // comment, the look-back is empty, and the rail fails on a correctly fixed file. The rendered
    // occurrence is the last one. A marker that is a substring of its own explanation needs the
    // position pinned deliberately.
    const at = src.lastIndexOf(claim)
    expect(at, 'the true-case sentence should still exist').toBeGreaterThan(-1)
    expect(
      src.slice(0, at),
      'the survival claim must be reached only under the capability check',
    ).toMatch(/persistAvailable === false/)
  })

  it('still distinguishes the two OFF cases — nothing installed vs not enabled', () => {
    const src = page()
    expect(src).toMatch(/This needs tmux, which is not installed on this host/)
    expect(src).toMatch(/Enabling keeps them alive with tmux/)
  })
})
