import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Thirteen switches and ten inputs showing settings nobody saved ──────────────────────────
//
// `AgentDefaultsPanel` already carries the ruling, comment and all: **a settings panel must not present
// FABRICATED values as saved state.** Its siblings that read the SAME endpoint had not been converged.
// Censused `pages/settings` for readers that substitute a value on rejection — **55** of them — and
// narrowed to the family where the substitution decides what a CONTROL claims: panels whose
// `api.personalclawConfig()` read is the panel.
//
// Driven at 1440×900 with `/api/config/personalclaw` at 500 and a cold sessionStorage, measured against
// the PARENT worktree (`grep -c 'personalclawConfig().catch'` = 1 there, 0 here — the only way to know
// which tree a dev server is serving):
//
//                            before                              after
//   #/settings/chat          **10 switches + 6 inputs**, silent   0 · 0 · "Couldn't load your settings" + Retry
//   #/settings/durability    **2 switches + 3 inputs**, silent    0 · 0 · same
//   #/settings/packs         **1 switch + 1 input**, silent       0 · 0 · same
//   #/settings/agent         already correct                      unchanged  ← control
//
// 🔑 EVERY ONE OF THOSE CONTROLS PATCHes ON CHANGE, so the failure mode is not merely a wrong readout: a
// user "correcting" a switch that was never loaded writes the opposite of what they believe is stored.
//
// 🔴 AND ONE PANEL'S EXISTING FIX WAS INERT ON THE REAL JOURNEY. `#/settings/legibility` refuses
// fabricated values — but the hub tile shares its cache key, so opening `#/settings` first primed it:
//
//   direct to the panel   cache=null   → the alert          ✅ its own fix works
//   hub → the panel       cache="{}"   → **2 switches, no alert**   🔴 defeated
//   after                 cache=null   → the alert, both ways
//
// Same key-poisoning shape cycle 117 found for `apps` / `settings:archives` /
// `settings:projection-rules`. **A per-surface honesty fix is not done until every consumer of its cache
// key stops substituting** — and the hub is a consumer of eleven of them.
//
// 🔑 WHAT KEEPS ITS FALLBACK, AND WHY: reads that DECORATE rather than define. `dashboardConfig` (the
// starter list), `durabilityStatus` + `durabilitySnapshots` (a status strip and a list), `api.agents()`
// in AgentDefaults. Losing one of those degrades a section; losing the config fabricates the panel.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const codeOf = (rel: string) =>
  read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** Panels whose `personalclawConfig()` read defines what their controls claim. */
const PANELS = [
  'pages/settings/ChatPanel.tsx',
  'pages/settings/DurabilityPanel.tsx',
  'pages/settings/PacksPanel.tsx',
  'pages/settings/AgentDefaultsPanel.tsx', // the one that was already right — the control
]

describe('a config panel does not present fabricated values as saved state', () => {
  for (const rel of PANELS) {
    it(`${rel.split('/').pop()} lets the config rejection reach the hook`, () => {
      const code = codeOf(rel)
      expect(code, 'the config read must be bare').toMatch(/api\.personalclawConfig\(\)/)
      // 🪤 SAME LINE, not a character window. `[\s\S]{0,80}` from `personalclawConfig()` reaches the NEXT
      // element of the `Promise.all` — `api.durabilityStatus().catch(() => null)`, a legitimate
      // decorating fallback — and reported it as this read's. Third time in this session an over-wide
      // proximity window has produced a false positive (cycle 120 replaced one with paren-matching,
      // cycle 122 with per-definition segmentation). A chained `.catch` cannot be on another line.
      const chain = code.split('\n').find((l) => l.includes('api.personalclawConfig()')) ?? ''
      expect(chain, 'a `.catch` chained onto THIS read fabricates the whole panel')
        .not.toMatch(/\.catch\(\(\)\s*=>/)
    })

    it(`${rel.split('/').pop()} shows the failure instead of the form`, () => {
      const code = codeOf(rel)
      expect(code).toMatch(/<LoadError what="settings" error=\{loadErr\} onRetry=\{refresh\} \/>/)
      // Reachability, not source order for its own sake: `data` is undefined for loading AND failure, so
      // the error test must precede the skeleton.
      const errAt = code.search(/<LoadError\b/)
      const skelAt = code.search(/<FormSkeleton\b/)
      expect(errAt, 'the error branch must come first or it never runs').toBeLessThan(skelAt)
    })
  }

  it('the decorating reads KEEP their fallbacks — this is not a no-catch sweep', () => {
    // Deliberate, and pinned: a future "finish the job" pass would make a missing snapshot list blank a
    // panel that could have rendered.
    expect(codeOf('pages/settings/ChatPanel.tsx')).toMatch(/api\.dashboardConfig\(\)\.catch\(\(\) => null\)/)
    const dur = codeOf('pages/settings/DurabilityPanel.tsx')
    expect(dur).toMatch(/api\.durabilityStatus\(\)\.catch\(\(\) => null\)/)
    // `durabilityArchive` since DAS-10 — it replaced `durabilitySnapshots` when the §6
    // archive browser landed. Still a DECORATING read, so it keeps its fallback.
    expect(dur).toMatch(/api\.durabilityArchive\(\)\.catch\(\(\) => null\)/)
  })

  it('the hub stops poisoning the legibility key it shares with that panel', () => {
    const widgets = codeOf('pages/settings/settingsWidgets.tsx')
    const at = widgets.indexOf("'settings:legibility'")
    expect(at, 'the hook must still exist').toBeGreaterThan(-1)
    expect(widgets.slice(at, at + 220), 'a substitute here defeats the panel on the hub journey')
      .not.toMatch(/\.catch\(\(\)\s*=>/)
  })

  it('the legibility tile says it failed, like the other four', () => {
    const widgets = codeOf('pages/settings/settingsWidgets.tsx')
    const at = widgets.indexOf('title="Legibility"')
    const body = widgets.slice(at, at + 900)
    expect(body).toMatch(/loading=\{c === undefined && !legErr\}/)
    expect(body).toMatch(/Boolean\(legErr\) && <div className="text-on-surface-low text-\[0\.75rem\]">Couldn&rsquo;t load/)
  })

  it('the census is reproducible, and the rest of the population is stated not swept', () => {
    // 55 readers in this directory still substitute a value. That is deliberate: for a counter or a
    // decorative strip, a fallback is right. This rail owns the family where the substitution decides
    // what an editable CONTROL claims — and records the number so the next pass starts from a count.
    const files = ['ChatPanel', 'DurabilityPanel', 'PacksPanel', 'AgentDefaultsPanel', 'settingsWidgets']
    for (const f of files) expect(read(`pages/settings/${f}.tsx`).length, `${f} must be readable`).toBeGreaterThan(500)
    const stillSubstituting = files
      .map((f) => (codeOf(`pages/settings/${f}.tsx`).match(/\.catch\(\(\)\s*=>\s*(\[\]|null|undefined|\{\}|\(\{\}|'')/g) ?? []).length)
      .reduce((a, b) => a + b, 0)
    // 🪤 THIS NUMBER WAS 3 AND THE REAL COUNT IS 31 — the comment above says "records the number so the
    // next pass starts from a count", and it recorded a tenth of it. 28 of the 31 could have vanished with
    // the rail still green. Measured by instrumenting every floor assertion in the suite (cycle 134).
    // It moves only deliberately: de-swallowing one of these is a real change, so lower it in that PR.
    //
    // 🔻 31 → 30, and this is that PR. Cycle ux-673 de-swallowed the `settings:doctor` and
    // `settings:incident` tiles (a health card and a SAFETY card, both of which rendered a blank body
    // on a failed read because the substituted `null` resolved the fetcher and cleared `loading`).
    // Measured on both sides: the population was 32 before — the floor had drifted BELOW the real count
    // again — and is 30 after. Lowered to the measured value, not to 30-because-two-left.
    expect(stillSubstituting, 'the decorating fallbacks in these five files, measured')
      .toBeGreaterThanOrEqual(30)
  })
})
