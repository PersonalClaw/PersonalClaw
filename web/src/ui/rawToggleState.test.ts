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

const PAGES = join(process.cwd(), 'src', 'pages')
const read = (rel: string) => readFileSync(join(PAGES, rel), 'utf8')

/** [file, the flag its content is gated on, an anchor unique to that button] */
const DISCLOSURES: [string, string, string][] = [
  // The chat turn-context strip is the `ContextLedger`, extracted out of `ChatPage.tsx` (LV-2) so
  // its one-action reach could be mounted and proved; the census follows the code, not the address.
  ['chat/ContextLedger.tsx', 'open', 'aria-expanded={open}\n        className="flex items-center gap-1.5 rounded-pill'],
  ['artifacts/ArtifactViewer.tsx', 'metaOpen', 'setMetaOpen((v) => !v)} aria-expanded={metaOpen}'],
  ['loops/CockpitPromptBar.tsx', 'open', 'aria-expanded={open} className="flex w-full items-center gap-s text-left min-w-0"'],
  ['loops/LoopCockpitPage.tsx', 'promptOpen', 'setPromptOpen(!promptOpen)} aria-expanded={promptOpen}'],
  ['loops/LoopCockpitPage.tsx', 'open', 'aria-expanded={open} className="w-full flex items-center gap-s px-m py-2 text-left"'],
  ['settings/AuditPanel.tsx', 'open', 'aria-expanded={open} className="flex w-full items-center gap-2 text-left'],
  ['settings/MemoryPanel.tsx', 'open', 'aria-expanded={open} className="w-full text-left"'],
  ['settings/ModelsPanel.tsx', 'open', 'aria-expanded={open} className="flex w-full items-center gap-3 px-4 py-3 text-left'],
  ['settings/SearchPanel.tsx', 'open', 'aria-expanded={open} className="flex w-full items-center gap-3 px-4 py-3 text-left'],
  ['tools/ToolOutput.tsx', 'open', 'aria-expanded={open} className="inline-flex items-center gap-1 text-on-surface-var'],
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
    expect(read('settings/DiagnosticsPanel.tsx')).toContain('aria-pressed={autoscroll}')
  })

  it('pause stays silent, because its title names the next action', () => {
    const src = read('settings/DiagnosticsPanel.tsx')
    const at = src.indexOf('setPaused((v) => !v)')
    expect(at).toBeGreaterThan(-1)
    expect(src.slice(at, at + 200), 'a name that flips needs no second channel').not.toMatch(/aria-pressed|aria-expanded/)
    expect(src.slice(at, at + 200)).toMatch(/title=\{paused \? 'Resume' : 'Pause'\}/)
  })

  it("PromptDetail's raw/rendered switch stays silent for the same reason", () => {
    const src = read('prompts/PromptDetail.tsx')
    const at = src.indexOf('setRaw((r) => !r)')
    expect(at).toBeGreaterThan(-1)
    expect(src.slice(at, at + 260)).not.toMatch(/aria-pressed|aria-expanded/)
    expect(src.slice(at, at + 260), 'its LABEL flips too, not just the title').toMatch(/Rendered|Raw/)
  })
})

describe('the census ceiling falls', () => {
  it('48 toggles, at most 16 silent', () => {
    // 🪤 The count is over CALL SITES, so ten of the sixteen are primitive-backed and already announce
    // (cycle 130's lesson). The ceiling exists to stop a NEW silent toggle landing, not to claim zero.
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const TOGGLE = /onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/g
    const windows = walk(PAGES).flatMap((abs) => {
      const src = readFileSync(abs, 'utf8')
      return [...src.matchAll(TOGGLE)].map((m) => src.slice(Math.max(0, m.index! - 340), m.index! + 380))
    })
    expect(windows.length, 'the population must still be found').toBeGreaterThanOrEqual(48)
    const silent = windows.filter((w) => !/aria-expanded|aria-pressed|ariaExpanded|ariaPressed/.test(w))
    expect(silent.length, 'was 34 after #1201; may only fall').toBeLessThanOrEqual(16)
  })
})
