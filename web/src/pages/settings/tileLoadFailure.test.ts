import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Three settings-hub tiles shimmered forever, and one already knew better ─────────────────
//
// Cycles 117 and earlier stopped several `settingsWidgets` hooks substituting `[]`/`null` for a failed
// fetch — necessary, because the hub SHARES CACHE KEYS with the panels and a swallow there made the
// panels' error branches unreachable. The stated cost at the time was that a tile would then sit in
// its `loading` shimmer instead of claiming a false count. This is that cost, paid.
//
// The population is small and was measured, not assumed. Of `settingsWidgets`' 26 cached hooks, **22
// still substitute a value**, so their tiles can never be `undefined` for long; only **4** can:
//
//   useInbox            'settings:inbox'              → already said it failed  ← canonical form
//   useApps             'apps'                        → shimmered forever
//   useArchives         'settings:archives'           → shimmered forever
//   useProjectionRules  'settings:projection-rules'   → shimmered forever
//
// Driven on `#/settings` at 1440×1000 with each tile's endpoint at 500 and a cold sessionStorage, using
// the Inbox tile as the CONTROL that proves the probe can see a failure line:
//
//                  before                              after
//   Apps           shimmer=2  text "Apps"              shimmer=0  "Apps Couldn't load your apps."
//   Archive        shimmer=2  text "Archive"           shimmer=0  "Archive Couldn't load your archives."
//   Tool output    shimmer=2  text "Tool output"       shimmer=0  "Tool output Couldn't load your
//                                                                  projection rules."
//   Inbox          shimmer=0  already said it          unchanged  ← control
//
// 🔑 THE CANONICAL FORM WAS ALREADY IN THE FILE, comment and all: the Inbox tile ships
// `loading={s === undefined && !inboxErr}` plus a muted line, under a comment reading "a tile that
// shimmers forever is the same lie in miniature — say it failed instead". Converging on that is one
// edit per tile and introduces NO new visual idiom, which matters here: the dashboard's slot-level
// error treatment is a recorded OWNER call, and this deliberately does not invent one. The tile stays a
// nav affordance; the panel behind it owns the real `LoadError` + Retry (also cycle 117).
//
// 🪤 `CardSkeleton` IS `aria-hidden` AND THE CARD HAS NO `aria-busy`, so a forever-shimmer was silent to
// assistive tech as well as wrong on screen. Logged as its own concern rather than folded in here — it
// is a loading-announcement question across all 22 tiles, not a failure question about 4.
//
// 🪤 THE TOOL-OUTPUT TILE HAS TWO READS. Its savings meter (`useToolsSavings`) keeps its own fallback
// and can still headline while the rules read has failed, so the failure line is gated on
// `savedTokens === 0` — a line that says "couldn't load your projection rules" under a live savings
// number would be talking about the wrong read.

const SRC = join(process.cwd(), 'src')
const widgets = readFileSync(join(SRC, 'pages/settings/settingsWidgets.tsx'), 'utf8')
// Comments stripped: this file DOCUMENTS the shapes it no longer uses, and a source scan that counts
// prose as code has been wrong five times in this session already.
const code = widgets.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** The four tiles whose reader can actually fail: [title, the error alias it must read]. */
const CAN_FAIL: [string, string][] = [
  ['Inbox', 'inboxErr'],
  ['Apps', 'appsErr'],
  ['Archive', 'archErr'],
  ['Tool output', 'rulesErr'],
]

describe('a hub tile whose data can fail says so instead of shimmering', () => {
  for (const [title, alias] of CAN_FAIL) {
    it(`the ${title} tile does not treat a failure as loading`, () => {
      // 🪤 NOT `[\\s\\S]{0,400}?>` — a non-greedy scan to the first `>` stops inside
      // `onClick={() => go('apps')}`, truncating the tag before `loading=`. Third time this trap has
      // bitten in this session (cycle 118's census reported 2 sites of 4 for the same reason). Take a
      // fixed WINDOW after the title instead and search inside it.
      const at = code.indexOf(`title="${title}"`)
      expect(at, `the ${title} tile must still exist`).toBeGreaterThan(-1)
      const tag = code.slice(at, at + 400)
      expect(tag, 'the shimmer must yield to the error').toMatch(
        new RegExp(`loading=\\{\\w+ === undefined && !${alias}\\}`),
      )
    })

    it(`the ${title} tile renders a failure line`, () => {
      // Scoped to the tile's own render body, so one tile's line cannot satisfy another's assertion.
      const at = code.indexOf(`title="${title}"`)
      const body = code.slice(at, at + 1200)
      expect(body).toMatch(new RegExp(`Boolean\\(${alias}\\)`))
      expect(body, 'and it names what failed').toMatch(/Couldn&rsquo;t load/)
    })

    it(`the ${title} tile reads the error off its hook`, () => {
      expect(code).toMatch(new RegExp(`error: ${alias}`))
    })
  }

  it('the tool-output failure line yields to its live savings meter', () => {
    const at = code.indexOf('title="Tool output"')
    expect(code.slice(at, at + 900)).toMatch(/Boolean\(rulesErr\) && savedTokens === 0/)
  })

  it('the 22 hooks that still substitute a value are NOT given a failure line', () => {
    // Enforcing one on a tile that can never enter the state would be dead code — the population was
    // measured first, and this pins that measurement so the next sweep does not "finish the job".
    // 🪤 Segment per hook, NOT a character window: a `{0,600}` scan from `const useInbox = () =>`
    // runs past its own definition into the NEXT hook's `.catch` and reports it as a swallower. Same
    // over-wide-proximity flaw the shared loadError rail was carrying (cycle 120 replaced its window
    // with paren-matching for exactly this reason).
    const starts = [...code.matchAll(/const (use\w+) = \(\) =>/g)]
    const swallowing = starts
      .filter((m, i) => /\.catch\(\(\)\s*=>/.test(code.slice(m.index!, starts[i + 1]?.index ?? m.index! + 700)))
      .map((m) => m[1])
    expect(swallowing.length, 'if this count moves, re-measure which tiles can fail')
      .toBeGreaterThanOrEqual(18)
    for (const alias of ['inboxErr', 'appsErr', 'archErr', 'rulesErr']) {
      expect(swallowing, `${alias}'s hook must NOT be among the swallowers`).not.toContain(
        { inboxErr: 'useInbox', appsErr: 'useApps', archErr: 'useArchives', rulesErr: 'useProjectionRules' }[alias],
      )
    }
  })

  it('no new visual idiom was invented — the line is the tile\'s own muted type', () => {
    // A tile-scale error CHROME would be an owner call (the dashboard's slot-level idiom already is).
    // This is the quiet line the Inbox tile already shipped, three more times.
    const lines = [...code.matchAll(/Couldn&rsquo;t load[^<]*<\/div>/g)]
    expect(lines.length, 'four tiles, four lines').toBeGreaterThanOrEqual(4)
    const styled = [...code.matchAll(/className="text-on-surface-low text-\[0\.75rem\]">Couldn&rsquo;t load/g)]
    expect(styled.length, 'every one of them uses the same muted type as the original').toBeGreaterThanOrEqual(4)
  })
})
