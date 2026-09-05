import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { RungChip } from '../ui/RungChip'
import { RUNG_PRESENTATION } from '../lib/rungs'
import { accentChip, toneChipSkin } from './accent'

// ── The accent-chip failure, THIRD SPELLING: the tone arrives from a REGISTRY ──────────────────────
//
// `accentChip.test.ts` owns this defect and already sweeps two spellings — the style object with a
// literal token (`background: color-mix(in srgb, var(--color-primary) N%…)` + `color:
// var(--color-primary)`) and the utility pair (`bg-primary/N text-primary`). A third population is
// invisible to both, because the colour is INTERPOLATED from a meta registry:
//
//     style={{ background: `color-mix(in srgb, ${meta.tone} 14%, transparent)`, color: meta.tone }}
//
// No accent token appears on the line at all, so neither regex can fire. `ui/RungChip` was one of
// these, and `lib/rungs.ts` maps `autonomous → var(--color-primary)`, so the chip that says "runs on
// its own" drew coral ink on a 14% coral tint: **3.97:1 in light** against a 4.5 floor — measured live
// on `#/triggers` (7 chips desktop, 6 at 390px) and exactly the 14% row of cycle 146's own table.
// Dark was never affected (5.52), the asymmetry that cycle explained: a tint lifts a light backdrop
// TOWARD a dark accent until the two converge.
//
// 🔑 ONE TONE, NOT A SWEEP. Recomputed for all four rungs as ink over a 14% tint of themselves on
// `--color-surface`:
//
//     on-surface-low   7.46 light / 5.43 dark      on-surface-var  4.99 / 8.19
//     info             5.13 / 5.35                 primary         **3.97 FAIL** / 5.52
//
// Only the coral rung moves. The other three keep the tint, so the fix cannot be "route rung chips
// through the container pair" — semantic and neutral tones have no `<tone>-container` sibling, which is
// the same reason cycle 146 left 47 semantic sites alone.
//
// 🔑 AND THE CONTAINER FILL IS GROUND-INDEPENDENT, which the tint never was. `color-mix(… ,
// transparent)` composites against whatever surface the chip lands on, so the same chip measured
// differently in a trigger row than it would in a panel. `primary-container` is opaque: one number,
// 13.1:1 light / 10.43:1 dark, guaranteed for all 12 schemes by `schemeContrast`.
//
// 🪤 THE POPULATION IS 43 SITES AND IS NOT SWEPT HERE — stated so it is recorded debt, not a silent
// cap. A regex cannot decide these: whether `${x.tone}` reaches coral depends on the registry behind
// `x`, and most of them resolve to semantic tones that pass. Converging them is per-registry work.
// The measured, drivable ones, for whoever takes the next slice:
//
//   pages/notifications/NotificationsPage.tsx:162   `toneChipBg(km.tone)` + `km.tone` ink, 16%.
//                                                   13 of its kinds are coral, and this home holds 36
//                                                   `proposal` + 6 `subagent` notifications, so it is
//                                                   drivable — but only with a row OPEN, which is why
//                                                   the surface census (default state) reports clean.
//   pages/schedule/ScheduleDetail.tsx:194,195       scheduleMeta `cron` and `agent` are both coral.
//   ui/Segmented.tsx:136                            **DEFERRED ON PURPOSE**: interactive, and its coral
//                                                   branch carries a hover. Exactly why cycle 146 held
//                                                   `ui/Button` and `pages/code/CodeCockpitPage` back
//                                                   in `CLASS_TINT_ALLOWED` — a container fill has no
//                                                   hover shade in the token set, and inventing one is
//                                                   a visual-language decision, not a contrast fix.
//
// 🪤 `toneChipBg`'s ICON-ONLY consumers are NOT part of this — `ui/NotificationBell:168` and
// `NotificationsPage:256` tint a tile behind an icon, which carries a 3:1 non-text floor it clears.
// Moving those would repaint five surfaces for no accessibility reason.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })

const rung = (resolved: string) =>
  ({
    key: 'test.action',
    floor: 'draft_only',
    ceiling: 'autonomous',
    leaves_machine: false,
    providers: ['p'],
    resolved_rung: resolved,
    granted_rung: resolved,
    held_by_incident: false,
    authority: 'declared floor',
    granted_at: '',
  }) as never

const chipStyle = (resolved: string) => {
  const { unmount } = render(<RungChip type={rung(resolved)} />)
  const el = screen.getByTitle(/declared floor/)
  const style = el.getAttribute('style') ?? ''
  unmount()
  return style
}

describe('a rung chip inks coral through the container pair, not a tint of itself', () => {
  it('the coral rung uses the shipped accent pair', () => {
    const style = chipStyle('autonomous')
    expect(style, 'the opaque container fill').toContain('var(--color-primary-container)')
    expect(style, 'and its guaranteed ink').toContain('var(--color-on-primary-container)')
    expect(style, 'no tint of the accent may remain — that is the 3.97:1 pairing')
      .not.toMatch(/color-mix/)
  })

  it('it is literally the shared pair, so it cannot drift from the other adopters', () => {
    const style = chipStyle('autonomous')
    expect(style).toContain(accentChip.background)
    expect(style).toContain(accentChip.color)
  })

  it('the three passing rungs keep their tint, each with its own tone', () => {
    // 4.99-7.46 in light. Routing these through the coral container would be a redesign, and they
    // have no `<tone>-container` sibling to pair with anyway.
    for (const [resolved, tone] of [
      ['draft_only', 'var(--color-on-surface-low)'],
      ['one_tap', 'var(--color-on-surface-var)'],
      ['auto_with_undo', 'var(--color-info)'],
    ] as const) {
      const style = chipStyle(resolved)
      expect(style, `${resolved} keeps the 14% tint`).toContain('color-mix')
      expect(style, `${resolved} tints with its OWN tone`).toContain(tone)
      expect(style, `${resolved} must not borrow the coral container`)
        .not.toContain('primary-container')
    }
  })

  it('the remap is not vacuous — the registry still declares exactly one coral rung', () => {
    // THE VACUITY FLOOR. If `RUNG_PRESENTATION` stopped using `--color-primary`, the branch in
    // `RungChip` would be dead code that still reads as an enforced rule; and if a SECOND rung became
    // coral, the measurement above would no longer describe the surface. Either way this must be a
    // deliberate edit, not a silent one.
    const coral = Object.entries(RUNG_PRESENTATION).filter(([, p]) => p.tone === 'var(--color-primary)')
    expect(coral.map(([k]) => k)).toEqual(['autonomous'])
  })

  it('every consumer gets the fix from the primitive, so none can be missed', () => {
    const users = walk(SRC).filter((abs) => /<RungChip\b/.test(readFileSync(abs, 'utf8')))
    expect(users.map((a) => a.slice(SRC.length + 1)).sort()).toEqual([
      'pages/chat/ApprovalCard.tsx',
      'pages/companion/CompanionPage.tsx',
      'pages/settings/GuardrailsPanel.tsx',
      'pages/triggers/TriggersListPage.tsx',
    ])
  })

  it("the family's rail points here, so the third spelling is findable from it", () => {
    expect(readFileSync(join(SRC, 'design/accentChip.test.ts'), 'utf8'))
      .toMatch(/accentChipTone\.test\.tsx/)
  })
})

// ── Cycle 175: the SECOND adopter, so the rule moved to `design/accent` ───────────────────────────
//
// Cycle 172 kept the coral remap inline in `RungChip` and recorded that it should move to
// `design/accent` "when the NEXT cycle converges NotificationsPage/ScheduleDetail — with real
// adopters, not speculatively". That cycle is this one.
//
// 🔑 THE SECOND SITE, MEASURED LIVE. `NotificationsPage`'s kind chip in the detail panel paints
// `toneChipBg(km.tone)` (a 16% tint) under `km.tone` ink. Driven at `#/notifications` with a row
// OPEN — which is why no surface census ever saw it, the default state has no panel — the coral
// kinds measure **3.85:1** in light at 13px against a 4.5 floor; dark is clean. `notificationMeta`
// declares **13** coral kinds, and this validation home holds 36 `proposal` + 6 `subagent`
// notifications, so it is the common case rather than an edge.
//
// 🪤 AND THE FIRST ATTEMPT TO MEASURE IT INVENTED A NUMBER. The tint resolves to
// `color(srgb 0.784314 0.270588 0.180392 / 0.16)`; a probe that pulls the first three numbers out of
// that string reads 0.78/0.27/0.18 as RGB — near-black — and reports ~1.27:1 on anything. Same bug had
// already faked a finding on an `oklab()` backdrop the cycle before. `probes/lib/color.mjs` now owns
// the conversion (srgb components are 0-1, NOT 0-255) and REFUSES to guess on notations it cannot
// parse rather than returning a plausible lie.
//
// 🔑 THE THIRD ADOPTER, and the widest one. `ScheduleDetail`'s summary row paints TWO of these:
// the schedule KIND and the exec MODE, and `scheduleMeta` makes both `cron` and `agent` coral. Driven
// by opening every schedule trigger on `#/triggers` (the detail is a panel there, not a route of its
// own — there is no `/api/schedule`; a schedule is the `kind:'schedule'` projection of a Trigger):
// **9 failing chips at 3.85:1 across all 5** schedule triggers in this home, because a cron schedule
// that invokes an agent lands two coral chips side by side. Dark: 0.
//
// 🪤 `strength` stays a parameter. The two adopters ship 14% and 16%, and those percentages apply
// only to tones that already pass — collapsing them would repaint passing chips for no accessibility
// reason. The coral branch has no strength at all, which is the half cycle 146 cared about.
//
// 🪤 `toneChipBg`'s ICON-ONLY consumers are still NOT migrated, and that is the distinction this rail
// exists to protect: `NotificationBell` and `NotificationsPage`'s list tile tint a square behind an
// icon, which carries a 3:1 non-text floor it clears at every strength. Moving them would repaint
// five surfaces for nothing.

describe('toneChipSkin is the one rule the tone-registry chips share', () => {
  it('routes coral to the container pair, with no tint left to fail', () => {
    const skin = toneChipSkin('var(--color-primary)', 14)
    expect(skin).toEqual({ ...accentChip })
    expect(JSON.stringify(skin)).not.toMatch(/color-mix|%/)
  })

  it('leaves every passing tone on its own tint, at the caller\'s strength', () => {
    for (const tone of ['var(--color-info)', 'var(--color-ok)', 'var(--color-warn)', 'var(--color-danger)',
                        'var(--color-on-surface-low)', 'var(--color-on-surface-var)']) {
      expect(toneChipSkin(tone, 16)).toEqual({
        background: `color-mix(in srgb, ${tone} 16%, transparent)`, color: tone,
      })
    }
  })

  it('honours each adopter\'s own strength rather than unifying them', () => {
    expect(toneChipSkin('var(--color-info)', 14).background).toContain('14%')
    expect(toneChipSkin('var(--color-info)', 16).background).toContain('16%')
    // …and the coral branch ignores it entirely, because a container has no strength.
    expect(toneChipSkin('var(--color-primary)', 20).background).toBe(accentChip.background)
  })

  it('every adopter goes through it, so none can re-decide the rule', () => {
    const rung = read('ui/RungChip.tsx')
    expect(rung).toMatch(/toneChipSkin\(meta\.tone, 14\)/)
    expect(rung, 'the inline ternary it replaced must be gone').not.toMatch(/\? accentChip\s*\n/)
    const notif = read('pages/notifications/NotificationsPage.tsx')
    expect(notif, 'the LABELLED kind chip').toMatch(/style=\{toneChipSkin\(km\.tone, 16\)\}/)
    // Cycle 176: BOTH of ScheduleDetail's summary chips — the schedule KIND and the exec MODE.
    const sched = read('pages/schedule/ScheduleDetail.tsx')
    expect(sched, 'the schedule-kind chip').toMatch(/style=\{toneChipSkin\(km\.tone, 16\)\}/)
    expect(sched, 'the exec-mode chip').toMatch(/style=\{toneChipSkin\(mm\.tone, 16\)\}/)
    expect(sched, 'no raw tint of a tone may remain on this surface')
      .not.toMatch(/color-mix\(in srgb, \$\{(?:km|mm)\.tone\}/)
  })

  it('the prompt SOURCE pills go through it too — the default source is coral', () => {
    // Cycle 177, the fourth adopter. `sourceTone` is a FUNCTION, not a `tone:` field, which is why a
    // registry grep for `tone: 'var(--color-primary)'` missed it entirely — and it returns coral for
    // `user`/undefined, i.e. the DEFAULT. In this validation home that is 47 of 47 prompts, so the
    // failing state was every prompt on the surface, measured at **3.85:1** (12px) in light.
    for (const rel of ['pages/prompts/PromptDetail.tsx', 'pages/prompts/SnippetDetail.tsx']) {
      const code = read(rel)
      expect(code, `${rel} source pill`).toMatch(/style=\{toneChipSkin\(sourceTone\(\w+\.source\), 16\)\}/)
      expect(code, `${rel} must keep no raw tint of the tone`)
        .not.toMatch(/color-mix\(in srgb, \$\{sourceTone\(/)
    }
  })

  it('the prompt tone function still returns coral for the default source', () => {
    // THE VACUITY FLOOR for this adopter. If `sourceTone` stopped returning coral for `user` the
    // remap would be dead code that still reads as an enforced rule; if a SECOND source became coral
    // the "only the default" claim above would quietly stop being true.
    const meta = read('pages/prompts/promptMeta.ts')
    expect(meta).toMatch(/if \(!source \|\| source === 'user'\) return 'var\(--color-primary\)'/)
    const coralReturns = [...meta.matchAll(/return 'var\(--color-primary\)'/g)]
    expect(coralReturns, 'exactly one coral branch in sourceTone').toHaveLength(1)
  })

  it('the icon-only and accent-BAR uses of the same tone are untouched', () => {
    // `PromptsListPage` spends `sourceTone` on a square icon tile, two SidePanel icons and a
    // `ListRow accent=` bar. All non-text (3:1 floor, or not text at all), so migrating them would
    // repaint the list for no accessibility reason — the same distinction as `toneChipBg`'s tiles.
    const list = read('pages/prompts/PromptsListPage.tsx')
    expect(list, 'the square icon tile keeps its plain tint')
      .toMatch(/size-10 items-center justify-center rounded-lg" style=\{\{ background: `color-mix\(in srgb, \$\{sourceTone\(r\.source\)\} 16%, transparent\)` \}\}/)
    expect(list).toMatch(/accent=\{sourceTone\(r\.source\)\}/)
  })

  it('the last two coral-capable registry chips go through it too', () => {
    // Cycle 178 closes the family's registry sweep. NEITHER of these was reachable with this
    // validation home's data, so they are SOURCE-verified only — stated rather than implied:
    //   · `InboxDetail`'s kind chip renders only in the ELSE of the classification branch, and all
    //     45 inbox items here HAVE a classification (39 needs_reply, 6 fyi). Measured, not assumed.
    //   · `AgentDetail`'s provider chip at the summary row did not render for a native agent; the
    //     chip that DID render is line 84, which already uses `accentChip` and measured **13.1:1**
    //     live. So this convergence is onto a form proven in the same file.
    // The pairing they used — coral ink over a 16% tint of itself — is the one measured at
    // **3.85:1** on four other sites this session, which is why they are converged rather than left.
    const inbox = read('pages/inbox/InboxDetail.tsx')
    expect(inbox, 'the kind chip').toMatch(/style=\{toneChipSkin\(km\.tone, 16\)\}/)
    expect(inbox).not.toMatch(/color-mix\(in srgb, \$\{km\.tone\}/)
    const agent = read('pages/agents/AgentDetail.tsx')
    expect(agent, 'the provider chip').toMatch(/style=\{toneChipSkin\(pm\.tone, 16\)\}/)
    expect(agent).not.toMatch(/color-mix\(in srgb, \$\{pm\.tone\}/)
  })

  it('only the coral-capable registries were touched — the census that removed work', () => {
    // 🔑 TWO censuses are needed, and running only the first is why this family looked smaller than
    // it was for four cycles:
    //   1. a `tone:` FIELD in a registry table      → grep "tone: 'var(--color-primary)'"
    //   2. a `return 'var(--color-primary)'` inside a tone FUNCTION → invisible to (1)
    // Census (2) is what found `promptMeta.sourceTone` and `agentMeta.providerMeta`.
    //
    // And the registries WITHOUT coral are why 8 flagged sites needed nothing at all: `taskMeta`
    // has none, so TaskDetail/TasksListPage/TaskBoard's tinted chips are all semantic tones. Pinned
    // so a later pass does not "finish" them.
    expect(read('pages/tasks/taskMeta.tsx'), 'no coral in the task registry')
      .not.toMatch(/var\(--color-primary\)/)
    // inboxMeta's coral is exactly the three message-ish kinds; classMeta/confMeta have none, which
    // is why only ONE of InboxDetail's three chips moved.
    const inboxMeta = read('pages/inbox/inboxMeta.ts')
    const coralKinds = [...inboxMeta.matchAll(/key: '(\w+)', label: '[^']*', tone: 'var\(--color-primary\)'/g)].map((m) => m[1])
    expect(coralKinds).toEqual(['message', 'mention', 'email'])
    // providerMeta returns coral for the native runtime only.
    const agentMeta = read('pages/agents/agentMeta.ts')
    expect([...agentMeta.matchAll(/return \{ label: '[^']*', icon: \w+, tone: 'var\(--color-primary\)' \}/g)]).toHaveLength(1)
  })

  it('the schedule registry still has exactly the two coral tones this covers', () => {
    // THE VACUITY FLOOR for the third adopter. `cron` (kind) and `agent` (mode) are the coral pair;
    // if a third became coral the measurement above would stop describing the surface, and if one
    // stopped being coral the remap would be dead code that still reads as an enforced rule.
    const meta = read('pages/schedule/scheduleMeta.ts')
    const coral = [...meta.matchAll(/key: '(\w+)'[^}]*tone: 'var\(--color-primary\)'/g)].map((m) => m[1])
    expect(coral.sort()).toEqual(['agent', 'cron'])
  })

  it('the icon-only tiles keep the plain tint — the distinction, not an oversight', () => {
    const notif = read('pages/notifications/NotificationsPage.tsx')
    // The list tile: a tint behind an ICON, 3:1 floor, deliberately untouched.
    expect(notif).toMatch(/style=\{\{ background: toneChipBg\(km\.tone\) \}\}><km\.icon/)
    expect(read('ui/NotificationBell.tsx')).toMatch(/background: toneChipBg\(km\.tone\)/)
  })
})

// ── Cycle 616: THE SOURCE_TONE REGISTRY — the slice this rail's worklist asked for ────────────────
//
// The header above records 43 registry-tone sites as debt and names the drivable ones "for whoever
// takes the next slice". This is one, and it was found the way the header predicted a registry site
// would have to be found — by driving the surface, not by a regex, because `${tone}` puts no accent
// token on the line.
//
// `#/skills` in LIGHT reported **9 blocking contrast findings** (one per bundled skill in view, 14
// chips in the DOM), axe agreeing [serious color-contrast]:
//
//     3.97:1 (need 4.5) 12px/400 — span.shrink-0.rounded-pill.px-2 "bundled"
//
// `skillMeta.SOURCE_TONE` maps **`bundled` AND `native`** to `var(--color-primary)`, so the chip drew
// coral ink on a 14% tint of itself — the exact 14% row of cycle 146's table and the exact number
// `ui/RungChip` measured. Dark was never affected (5.9), the same asymmetry: a tint lifts a light
// backdrop TOWARD a dark accent until ink and background converge.
//
// 🔑 TWO CALL SITES, ONE REGISTRY, so this is a complete per-registry slice rather than half a family:
// the list chip at 14% and the inspector's header chip at 16%. Both route through `toneChipSkin`, which
// returns the opaque container pair for coral and leaves every other tone's tint at the caller's own
// strength — `local`/`installed`/`marketplace`/`skills.sh`/`agent-local` all resolve to info/ok/warn
// and clear AA, so nothing else in the registry moves.
//
// 🪤 AND MY OWN ARITHMETIC WAS THE THING THAT WAS WRONG. A quick probe reported the chip at **4.83**,
// i.e. passing, because the tint is serialised as `color(srgb 0.784 0.271 0.180 / 0.14)` and the
// probe's `rgba?\(…\)` regex could not parse it — so it silently skipped the chip's own background and
// measured coral on plain white. `ux-audit` handles that colour syntax, axe agreed with it, and the
// number matches the family's recorded 14% value exactly. **When a hand-rolled measurement disagrees
// with two independent tools, the probe is the defect.**

describe('the skill source chip routes its registry tone through the helper', () => {
  const LIST = read('pages/skills/SkillsPage.tsx')
  const INSPECTOR = read('pages/skills/SkillInspector.tsx')

  it('both call sites use toneChipSkin, at their own strengths', () => {
    expect(LIST).toMatch(/style=\{toneChipSkin\(tone\)\}/)
    expect(INSPECTOR).toMatch(/style=\{toneChipSkin\(tone, 16\)\}/)
  })

  it('neither hand-rolls the tint any more', () => {
    const strip = (x: string) => x.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/^\s*\/\/.*$/gm, '')
    for (const [name, src] of [['SkillsPage', LIST], ['SkillInspector', INSPECTOR]] as const)
      expect(strip(src), `${name} raw tone tint`)
        .not.toMatch(/background: `color-mix\(in srgb, \$\{tone\} \d+%, transparent\)`, color: tone/)
  })

  it('the registry still reaches coral — the vacuity floor', () => {
    // If `SOURCE_TONE` ever stops mapping a source to `--color-primary`, `toneChipSkin`'s coral branch
    // stops firing here and this rail passes while measuring nothing. Then it must change deliberately.
    const meta = read('pages/skills/skillMeta.ts')
    const coral = [...meta.matchAll(/^\s*'?([\w.-]+)'?: 'var\(--color-primary\)'/gm)].map((m) => m[1])
    expect(coral, 'sources whose tone is coral').toEqual(['bundled', 'native'])
  })

  it("the inspector's semantic sibling keeps its raw tint", () => {
    // `always loaded` is warn at 16% and measures 4.54-4.71. Routing it through the coral container
    // would be a redesign — the same call cycle 146 made when it left 47 semantic sites alone.
    expect(INSPECTOR).toMatch(/background: 'color-mix\(in srgb, var\(--color-warn\) 16%, transparent\)', color: 'var\(--color-warn\)'/)
  })

  it('the call site carries the measurement, not just the token', () => {
    expect(LIST).toMatch(/3\.97:1/)
  })
})
