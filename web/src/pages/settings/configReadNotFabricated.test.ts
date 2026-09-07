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
    expect(body).toMatch(/Boolean\(legErr\) && <div data-type="caption" className="text-on-surface-low">Couldn&rsquo;t load/)
  })

  // ── `settings:agent-defaults` — the same defect, on the two most dangerous controls in Settings ──
  //
  // 🪤 THIS KEY WAS THE ONE THE RAIL DID NOT NAME. The legibility case above was written as a
  // per-key assertion, and `settings:agent-defaults` was never added — so the hub kept the divergent
  // `.catch(() => ({}))` that `AgentDefaultsPanel` had already removed from its own copy, and the
  // panel's `if (!data && loadErr)` guard stayed unreachable on the hub→panel journey. `data` was
  // defined, just empty.
  //
  // Why this key matters more than legibility's two switches: with `{}` the tile claimed
  // **"Ask each time"** for approval mode (from `?? 'interactive'`) while the stored default is
  // `auto` — the UI showed the SAFE mode and the runtime ran the permissive one — and rendered the
  // **YOLO** auto-approve-everything switch as OFF. Both of those controls PATCH on change, so a user
  // "correcting" one writes against a config they never loaded.
  it('🔴 the hub stops poisoning the agent-defaults key it shares with that panel', () => {
    const widgets = codeOf('pages/settings/settingsWidgets.tsx')
    const at = widgets.indexOf("'settings:agent-defaults'")
    expect(at, 'the hook must still exist').toBeGreaterThan(-1)
    // Bounded to the hook body. The window has to reach BOTH reads, because the second one keeps its
    // fallback legitimately — so this cannot just forbid `.catch` in the hook and be done.
    const hook = widgets.slice(at, widgets.indexOf('persist: true', at) + 20)
    expect(hook, 'found the whole hook').toMatch(/api\.agents\(\)/)
    // The config read: no substitute, byte-identical to the panel's.
    expect(hook, 'a substitute on the CONFIG read defeats the panel on the hub journey')
      .not.toMatch(/personalclawConfig\(\)[^\n]*\.catch\(/)
    // 🪤 …and the DECORATING read keeps its own, exactly as the panel spells it. Stripping this one
    // would be the over-correction: the default agent's NAME renders as '—', it is not a control's
    // claimed state, and blanking the tile for it would be a regression dressed as a fix.
    expect(hook, 'the default-agent name keeps its fallback').toMatch(/api\.agents\(\)\.then\(\(a\) => a\.default_agent\)\.catch\(\(\) => ''\)/)
  })

  it('🔴 the agent-defaults tile substitutes the TRUE default, not the safe-looking one', () => {
    // The tile's shimmer/failure-line half belongs to `tileLoadFailure.test.ts`, which owns that
    // property for all five failable tiles; this file owns the FABRICATED VALUE, which is the half
    // that made the readout contradict the runtime.
    const widgets = codeOf('pages/settings/settingsWidgets.tsx')
    const at = widgets.indexOf('title="Agent defaults"')
    expect(at, 'found the tile').toBeGreaterThan(-1)
    const body = widgets.slice(at - 1400, at + 1200)
    // 🪤 `'interactive'` claimed the app would ask before every tool call; the stored default is
    // `auto`. Unreachable now that the fetcher rejects — but a readout that lies whenever it IS
    // reached is not worth keeping, and the direction matters: this string governs only the DISPLAY,
    // so guessing the restrictive mode manufactures false assurance rather than adding safety.
    expect(body, 'the honest fallback').toMatch(/approval_mode \?\? 'auto'/)
    expect(body, 'the safe-looking lie is gone').not.toMatch(/approval_mode \?\? 'interactive'/)
  })

  it("VACUITY: the backend default really is `auto`, so 'auto' is the truthful fallback", () => {
    // If this ever becomes `interactive`, the assertion above inverts and the tile should follow the
    // config rather than this rail. Guards the fallback's TRUTH, not its spelling.
    const { readFileSync } = require('node:fs') as typeof import('node:fs')
    const { join } = require('node:path') as typeof import('node:path')
    const loader = readFileSync(join(process.cwd(), '..', 'src/personalclaw/config/loader.py'), 'utf8')
    // Bounded to AgentConfig's own block, not the file — `approval_mode` also exists per-agent.
    const cls = loader.match(/class AgentConfig:[\s\S]*?\n\n/)?.[0] ?? ''
    expect(cls, 'found AgentConfig').not.toBe('')
    expect(cls, 'approval_mode defaults to auto').toMatch(/approval_mode[\s\S]{0,120}?default="auto"/)
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
    //
    // 🔺 30 → 32. Re-measured while adding the four missing settings-hub tiles, and the floor had
    // drifted below the real count for the THIRD time: `origin/main` scores **31**, not 30, so one
    // swallow had already landed unrecorded. This PR adds ONE more — `settings:packs:installed` in
    // `settingsWidgets.tsx`, which copies `PacksPanel`'s existing `.catch(() => [])` verbatim because
    // the two share that cache key and a DIVERGENT fetcher on a shared key is the defect this whole
    // file is about. That is a decorating read (the panel's own answer to a failed ledger read is
    // "No packs installed yet"), so it keeps the fallback, and the floor moves to the measured 32.
    // Per-file, that tree: ChatPanel 1 · DurabilityPanel 2 · PacksPanel 2 · AgentDefaults 1 ·
    // settingsWidgets 26.
    //
    // 🔺 32 → 33, and THE FLOOR HAD DRIFTED BELOW THE REAL COUNT FOR THE FOURTH TIME. Re-measured by
    // replaying this census's own regex against `origin/main`: the true count was **34**, not 32, and
    // the per-file line above under-reports `settingsWidgets` by two (28, not 26). So two swallows
    // landed unrecorded since the last measurement — and, worse, the drift made this rail unable to
    // see the change in this very PR: removing one takes 34 → 33, which still satisfied `>= 32`.
    //
    // That is the fourth occurrence of the same failure, so it is worth naming the cause rather than
    // just fixing the number: a `toBeGreaterThanOrEqual` floor cannot detect an ADDITION, only a
    // removal. It is the right shape for "do not silently de-swallow", and the wrong shape for "do not
    // silently add one" — which is the direction that actually happens, because adding a `.catch` is
    // what a developer does under time pressure. An exact-equality assertion would catch both and
    // force this comment to be updated in the same PR; that is a bigger change than this one and it
    // belongs to whoever next touches this rail.
    //
    // This PR de-swallows ONE: `settings:agent-defaults` in `settingsWidgets.tsx`, whose divergent
    // `.catch(() => ({}))` on the config read defeated `AgentDefaultsPanel`'s own honesty fix on the
    // hub→panel journey. Lowered to the MEASURED post-fix value, not to 32-minus-nothing.
    // Per-file, this tree: ChatPanel 1 · DurabilityPanel 2 · PacksPanel 2 · AgentDefaultsPanel 1 ·
    // settingsWidgets 27 = 33.
    expect(stillSubstituting, 'the decorating fallbacks in these five files, measured')
      .toBeGreaterThanOrEqual(33)
  })
})
