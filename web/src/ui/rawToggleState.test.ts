import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The eleven raw toggles left after the primitives were fixed ───────────────────────────────
//
// Cycles 128–130 closed the primitive side of this family (`Button`, `HeaderControl`, `FilterChip`,
// `IconButton`, `SquareIconButton`, `QuietButton` all announce now). What remained were hand-rolled
// `<button>`s. Classified per site — which is the whole job, since the same measurement has three
// different right answers:
//
//   10  DISCLOSURES → `aria-expanded={state}`
//        chat's turn-context strip (`ContextLedger`) · ArtifactViewer's Details · CockpitPromptBar's Prompt ·
//        LoopCockpitPage's Prompt + phase row · AuditPanel's event row · MemoryPanel's digest ·
//        ModelsPanel + SearchPanel accordions · ToolOutput's JSON expander
//    1  MODE → `aria-pressed={autoscroll}`  (`DiagnosticsPanel`)
//    2  LEAVE — the accessible NAME already flips:
//        `DiagnosticsPanel`'s pause ("Resume" ⇄ "Pause") and `PromptDetail`'s raw/rendered switch
//        ("Raw" ⇄ "Rendered", title "Show raw template" ⇄ "Show rendered"). A second channel adds
//        nothing when the name itself carries the state — the ruling from cycle 128, applied twice more.
//
// 🔑 WHY AUTOSCROLL IS PRESSED AND PAUSE IS NOT, given both flip a mode: autoscroll's `title` names the
// STATE ("Autoscroll on"/"Autoscroll off") and its only other cue is a coral tint, so nothing announced
// which way it was set. Pause's title names the NEXT ACTION, which is self-describing. **Read what the
// name says before deciding whether the state needs a second channel.**
//
// Driven, parent worktree vs this one (`grep -c 'aria-expanded={open}' SearchPanel.tsx` = 0 there, 1 here):
//
//   route                    nodes            the control actually exercised
//   #/settings/search        3 → **7**        `false → true` on "Article fetch · tavily" (a provider row)
//   #/settings/models        3 → **19**       `false → true` on "Video · Generation" (a use-case card)
//   #/settings/audit         3 → **203**      `false → true` on an event row
//   #/settings/diagnostics   pressed 0 → **1** **`true → false`** on autoscroll
//
// 🪤 THE FIRST RUN OF THAT PROBE CLICKED THE WRONG CONTROL AND STILL LOOKED FINE. It exercised
// `els[0]`, which on every settings route is the shell's own pre-existing "2 degraded" disclosure — so
// `false → true` was reported on the BEFORE tree too. **Report the identity of the element you
// exercised**, or a pre-existing control stands in for your fix and the flip proves nothing.
//
// The census ceiling drops with this: **48 toggles, 16 still silent** (was 34). Of those 16, ten are
// primitive-backed and already announce through `HeaderControl`/`FilterChip`/`IconButton`/
// `SquareIconButton` — the census counts call sites, not behaviour (cycle 130's lesson) — and the
// remaining six are the name-flipping exceptions plus `ModelBackends`' show/hide pair.

// 🪤 THIS CENSUS USED TO WALK `src/pages` ONLY, and that scope was wrong about its own subject.
// A hand-rolled disclosure is a hand-rolled disclosure wherever it lives, and `src/ui` is full of
// them — so extracting one OUT of `pages/` removed it from the census entirely and the ceiling fell
// for a reason that was not a fix. That is exactly what happened here: `ModelsPanel` and
// `SearchPanel`'s accordion shells became `ui/DisclosureCard`, and two rows would have gone quiet.
//
// Re-scoped to `src/`. Measured across both scopes at the moment of the move:
//
//     src/pages   population=54  silent=13     ← what the rail could see
//     src         population=67  silent=20     ← what there actually is
//
// The ceiling therefore RISES from 16 to 20, and that is not a regression: the population grew
// because the SCOPE grew, not because new silent toggles landed. The 7 newly-visible silent toggles
// live in six files and are **BUGS, NOT EXEMPTIONS** — a worklist, recorded so the next pass does not
// have to re-derive it:
//
//     ui/Composer.tsx · ui/DegradedChip.tsx · ui/NotificationBell.tsx
//     ui/content/ContentSurface.tsx · ui/widget/ReactWidgetFrame.tsx · ui/widget/WidgetFrame.tsx
//
// Shrink-only from here in either half of the pair.
const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** [file (relative to `src/`), the flag its content is gated on, an anchor unique to that button] */
const DISCLOSURES: [string, string, string][] = [
  // The chat turn-context strip is the `ContextLedger`, extracted out of `ChatPage.tsx` (LV-2) so
  // its one-action reach could be mounted and proved; the census follows the code, not the address.
  ['pages/chat/ContextLedger.tsx', 'open', 'aria-expanded={open}\n        data-type="caption"\n        className="flex items-center gap-1.5 rounded-pill'],
  ['pages/artifacts/ArtifactViewer.tsx', 'metaOpen', 'setMetaOpen((v) => !v)} aria-expanded={metaOpen}'],
  ['pages/loops/CockpitPromptBar.tsx', 'open', 'aria-expanded={open} className="flex w-full items-center gap-s text-left min-w-0"'],
  ['pages/loops/LoopCockpitPage.tsx', 'promptOpen', 'setPromptOpen(!promptOpen)} aria-expanded={promptOpen}'],
  ['pages/loops/LoopCockpitPage.tsx', 'open', 'aria-expanded={open} className="w-full flex items-center gap-s px-m py-2 text-left"'],
  ['pages/settings/AuditPanel.tsx', 'open', 'aria-expanded={open} data-type="caption" className="flex w-full items-center gap-2 text-left'],
  ['pages/settings/MemoryPanel.tsx', 'open', 'aria-expanded={open} className="w-full text-left"'],
  // 🔑 TWO ROWS BECAME ONE. `ModelsPanel` and `SearchPanel` held byte-identical accordion shells;
  // both are now `ui/DisclosureCard`, so there is one disclosure button to police instead of two
  // copies that had to be fixed twice (they were — the same clipped-focus-ring fix, applied at both
  // sites in one PR). `aria-controls` is part of the anchor because the extraction added it: the two
  // originals announced that they expanded WITHOUT naming what they expanded.
  ['ui/DisclosureCard.tsx', 'open', 'aria-expanded={open} aria-controls={bodyId}\n        className="flex w-full items-center gap-3 px-4 py-3 text-left'],
  ['pages/tools/ToolOutput.tsx', 'open', 'aria-expanded={open} className="inline-flex items-center gap-1 text-on-surface-var'],
]

describe('a raw disclosure button announces its state', () => {
  for (const [rel, state, anchor] of DISCLOSURES) {
    it(`${rel}${state === 'promptOpen' ? ' (prompt)' : ''} announces ${state}`, () => {
      expect(read(rel), `${rel} must carry the attribute on this specific button`).toContain(anchor)
    })

    it(`${rel} still gates content on ${state}`, () => {
      // The pairing, from the other side: an attribute bound to a flag nothing renders on is a lie.
      const src = read(rel)
      expect(src.includes(`{${state} && `) || src.includes(`${state} ?`), `${rel} must render on ${state}`).toBe(true)
    })
  }
})

describe('a mode toggle gets pressed — unless its name already says so', () => {
  it('autoscroll is pressed, because its title names the state and the rest is a tint', () => {
    expect(read('pages/settings/DiagnosticsPanel.tsx')).toContain('aria-pressed={autoscroll}')
  })

  it('pause stays silent, because its title names the next action', () => {
    const src = read('pages/settings/DiagnosticsPanel.tsx')
    const at = src.indexOf('setPaused((v) => !v)')
    expect(at).toBeGreaterThan(-1)
    expect(src.slice(at, at + 200), 'a name that flips needs no second channel').not.toMatch(/aria-pressed|aria-expanded/)
    expect(src.slice(at, at + 200)).toMatch(/title=\{paused \? 'Resume' : 'Pause'\}/)
  })

  it("PromptDetail's raw/rendered switch stays silent for the same reason", () => {
    const src = read('pages/prompts/PromptDetail.tsx')
    const at = src.indexOf('setRaw((r) => !r)')
    expect(at).toBeGreaterThan(-1)
    expect(src.slice(at, at + 260)).not.toMatch(/aria-pressed|aria-expanded/)
    expect(src.slice(at, at + 260), 'its LABEL flips too, not just the title').toMatch(/Rendered|Raw/)
  })
})

describe('the census ceiling falls', () => {
  it('67 toggles across src, at most 20 silent', () => {
    // 🪤 The count is over CALL SITES, so many of the silent ones are primitive-backed and already
    // announce through `HeaderControl`/`FilterChip`/`IconButton`/`SquareIconButton` (cycle 130's
    // lesson). The ceiling exists to stop a NEW silent toggle landing, not to claim zero.
    //
    // 🪤 AND THE FLOOR IS DELIBERATELY WELL BELOW THE MEASUREMENT (60 against 67). A `>=` floor
    // detects a REMOVAL and never an ADDITION, so its only job here is anti-vacuity — proving the
    // walk still finds the family rather than silently matching nothing after a regex or layout
    // change. Pinning it AT the measurement would red on the next honest extraction, which is what
    // this very PR does to two of the rows.
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const TOGGLE = /onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/g
    const windows = walk(SRC).flatMap((abs) => {
      const src = readFileSync(abs, 'utf8')
      return [...src.matchAll(TOGGLE)].map((m) => src.slice(Math.max(0, m.index! - 340), m.index! + 380))
    })
    expect(windows.length, 'the population must still be found').toBeGreaterThanOrEqual(60)
    const silent = windows.filter((w) => !/aria-expanded|aria-pressed|ariaExpanded|ariaPressed/.test(w))
    expect(silent.length, 'measured 20 across src at the DisclosureCard extraction; may only fall').toBeLessThanOrEqual(20)
  })
})
