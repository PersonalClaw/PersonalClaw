import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A settings write that fails must say so ──────────────────────────────────────
//
// Every settings toggle in this app saves OPTIMISTICALLY: the local state flips first, then the PUT
// goes out. That is the right feel — and it makes a swallowed rejection a lie, because the control is
// left showing a value the server refused.
//
// Measured on `#/settings/chat` with the PUT returning 500 (route-intercepted, so nothing was written):
//
//   toggle `aria-pressed`  false → true, and it STAYED true
//   "Saved" confirmation   never appeared (correct — it is `.then`-only)
//   live-region content    []            ← nothing was announced
//   page text              no error anywhere
//
// A reload silently reverts it. The app already answers this everywhere else: eight sibling handlers in
// `AccountPanel`, `AmbientPanel` and `AgentDefaultsPanel` do
// `notify(\`Couldn't save …: ${message}\`, 'error')`. Five handlers did not, all with the same
// optimistic-then-silent shape, and this rail keeps them converged:
//
//   settings/ChatPanel.tsx           saveDashboardConfig   (Sessions + Messages sections)
//   settings/InboxSettingsPanel.tsx  saveInboxSettings
//   settings/MemoryPanel.tsx         saveMemorySettings
//   settings/NotificationsPanel.tsx  saveNotificationSettings
//   inbox/InboxSettingsPanel.tsx     saveInboxSettings     (the drawer copy — kept in parity)
//
// After: the same probe reports `Couldn't save this chat setting: {"detail":"save failed"}` and
// `Couldn't save your notification settings: …` in a live region.
//
// ⚠️ The probe's own `saysError` heuristic (regex over `document.body.innerText`) FALSE-POSITIVED on
// `#/settings/notifications`, whose static copy contains the word "failed". Only the live-region content
// is trustworthy for "was the user told?".

const PAGES = join(process.cwd(), 'src', 'pages')
const SETTINGS = join(PAGES, 'settings')

/** The settings writes that must report a failure, as `file → the api call that persists`. */
const REPORTERS: Array<[string, string]> = [
  [join('settings', 'ChatPanel.tsx'), 'saveDashboardConfig'],
  [join('settings', 'InboxSettingsPanel.tsx'), 'saveInboxSettings'],
  [join('settings', 'MemoryPanel.tsx'), 'saveMemorySettings'],
  [join('settings', 'NotificationsPanel.tsx'), 'saveNotificationSettings'],
  [join('inbox', 'InboxSettingsPanel.tsx'), 'saveInboxSettings'],
]

describe('an optimistic settings write reports its failure', () => {
  it.each(REPORTERS)('%s reports a failed %s', (rel, call) => {
    const src = readFileSync(join(PAGES, rel), 'utf8')
    expect(src.includes(`api.${call}(`), `${rel} must still perform the write`).toBe(true)
    // The write's own `.catch` must not discard the rejection.
    const at = src.indexOf(`api.${call}(`)
    const chain = src.slice(at, at + 420)
    expect(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain), `${rel}: a silent catch leaves the control lying`).toBe(false)
    expect(/\.catch\(\((?:e|err|error)\)\s*=>/.test(chain), `${rel}: the rejection must be captured`).toBe(true)
    expect(/notify\(/.test(chain), `${rel}: and reported with notify(), like the sibling panels`).toBe(true)
  })

  it('every reporter says "Couldn\'t save" and includes the server message', () => {
    for (const [rel] of REPORTERS) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      expect(src, `${rel}: the copy must match the family`).toMatch(/Couldn't save/)
      expect(src, `${rel}: a bare "it failed" is not actionable`).toMatch(/\(e as Error\)\?\.message/)
    }
  })

  // ── The sweep, WIDENED from verb-matching to shape-matching (cycle 629) ─────────────────────────
  //
  // 🪤 THIS RAIL USED TO FILTER BY VERB NAME — `api.save*` and `api.patchConfig` — and therefore could not
  // see the two writes it was written for. `UpdatesPanel`'s `setAutoUpdate` / `setUpdateDevMode` flipped
  // local state, showed "Saved" on `.then` only, and discarded the rejection: the exact defect described in
  // this file's header, invisible for four cycles because it is spelled `set*`.
  //
  // **A rail that filters by verb misses every future verb.** So the sweep now matches the SHAPE — any
  // `api.<anything>(` whose chain discards the rejection — and every legitimate swallow is named below with
  // its reason. That inverts the default: a new swallowed write fails this test unless someone deliberately
  // adds it to the allowlist, which is the only version of this rail that can stay true.
  const ALLOWED_SWALLOWS: Array<[string, string, string]> = [
    ['AccountPanel.tsx', 'dashboardConfig', 'a READ — best-effort hydrate, the panel renders without it'],
    ['AccountPanel.tsx', 'personalclawConfig', 'a READ — the panel falls back to defaults when absent'],
    ['AccountPanel.tsx', 'authSession', 'a READ — an absent session renders the signed-out state'],
    ['DiagnosticsPanel.tsx', 'logLevel', 'a READ — the control renders empty until it resolves'],
  ]

  it('no settings panel silently swallows ANY api rejection — allowlist or fix', () => {
    const files = readdirSync(SETTINGS).filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))
    expect(files.length, 'the settings panels must be discoverable').toBeGreaterThan(20)
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      // 🪤 Neutralise `=>` before any bounded scan: it contains a `>` and, more importantly here, the
      // catch bodies themselves are arrow functions. Four regex traps this session came from forgetting it.
      const scan = src.replace(/=>/g, '\u21d2')
      for (const m of scan.matchAll(/api\.(\w+)\(/g)) {
        const chain = scan.slice(m.index!, m.index! + 420)
        if (!/\.catch\(\s*\(\s*\)\s*\u21d2\s*\{\s*\}\s*\)/.test(chain)) continue
        if (ALLOWED_SWALLOWS.some(([file, call]) => file === f && call === m[1])) continue
        offenders.push(`${f}:${scan.slice(0, m.index).split('\n').length} (api.${m[1]})`)
      }
    }
    expect(offenders, 'a discarded rejection tells the user nothing — report it or allowlist it with a reason').toEqual([])
  })

  it('the allowlist is not a dumping ground — every entry still exists and is still a read', () => {
    // The vacuity floor for the allowlist. If an allowed call disappears or becomes a write, the entry must
    // be removed deliberately rather than quietly covering something new.
    for (const [f, call, why] of ALLOWED_SWALLOWS) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      expect(src, `${f}: allowlisted ${call} no longer exists — drop the entry`).toContain(`api.${call}(`)
      expect(why.length, `${f}/${call} needs a stated reason`).toBeGreaterThan(10)
    }
    expect(ALLOWED_SWALLOWS.length, 'the allowlist should stay small; a growing one means the rule is wrong').toBeLessThan(8)
  })

  it('the two writes this widening caught now report', () => {
    const upd = readFileSync(join(SETTINGS, 'UpdatesPanel.tsx'), 'utf8')
    expect(upd, 'the optimistic toggles must report').toMatch(/\.catch\(reportSettingFailure\(/)
    expect(upd).toMatch(/notify\(`Couldn't \$\{what\}: \$\{msg\}`, 'error'\)/)
    expect(upd, 'and still confirm on success — the `.then` was correct').toContain('.then(() => { setSaved(true)')
  })
})

// ── 2026-08-19: REPORTED, AND STILL UNFINDABLE — the shape a "was it swallowed?" rail cannot see ───
//
// The sweep above asks one question: *is the rejection discarded?* `#/settings/portability` answered
// **no** — it captured every rejection and rendered the message. Measured with `/api/durability/export`
// route-intercepted to a 500:
//
//   the message            "Packaging failed: no space left on device"   ← a good message
//   the button that failed y = 249   ("Export knowledge")
//   where it rendered      y = 493   — **244px below**, and below the "Import" heading at y = 379
//   its DOM ancestry       span.text-on-surface-low → div.mt-3.flex → div.rounded-lg.border-warn/30
//                          → i.e. INSIDE THE IMPORT CARD, in the Import button's own row
//   its voice              rgb(154,155,156), 12px, role=null, no aria-live ancestor
//   the same action's SUCCESS   a toast (`notify(…, 'success')`)
//
// So one button reported success as a toast and failure as 12px grey text in a different section, under a
// notice that begins "Importing merges the archive's data…". A user who clicks Export and gets nothing
// looks at the button; if they ever find the sentence, it appears to be about importing.
//
// 🔑 THE CAUSE WAS THE VARIABLE, NOT THE SPAN. A single `msg` state was written by all three handlers
// (export, validate, import) and rendered at one site inside the Import card. Moving the span would have
// fixed one of the three. Failures now go to the error channel — `notify(…, 'error')`, which is where the
// other **61** failure reports across these panels go, and exactly what the sibling `DurabilityPanel`
// does for the same `/api/durability/*` endpoints — and the state was renamed `importResult`, so a catch
// has nothing left to write to. That is the guard: not a convention, an absence.
//
// 🔑 THE FAMILY IS 6 MORE, MEASURED, AND MOST OF IT MAY BE FINE. An inline `setErr` beside the control
// that failed is a GOOD report — often better than a toast. What made this one a defect was the distance
// plus the shared channel. The census below is therefore a ceiling on "a message-shaped failure state
// whose render sites carry no error tone", not a to-do list: 7 before this change, **6 after**, and the
// panel this cycle fixed must no longer appear in it. That drop is also the vacuity proof — a census that
// does not shrink when you fix a member was never measuring the member.

describe('a failure report lands where the failure happened', () => {
  const P = 'PortabilityPanel.tsx'
  const portability = () => readFileSync(join(SETTINGS, P), 'utf8')

  /** Brace-matched `catch (e) { … }` bodies. A bounded window would stop inside the arrow functions. */
  function catchBodies(src: string): string[] {
    const out: string[] = []
    for (const m of src.matchAll(/catch\s*\((\w+)\)\s*\{/g)) {
      let depth = 1, i = m.index! + m[0].length
      for (; i < src.length && depth > 0; i++) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
      }
      out.push(src.slice(m.index!, i))
    }
    return out
  }

  it('every catch on the import/export panel reaches the error channel', () => {
    const bodies = catchBodies(portability())
    expect(bodies.length, 'the panel must still have its three failure paths').toBeGreaterThanOrEqual(3)
    for (const b of bodies) {
      expect(b, `a catch that only sets state cannot be found from the control:\n${b}`).toMatch(/notify\(/)
      expect(b, 'and it must be the error tone, not a neutral aside').toMatch(/'error'\)/)
    }
  })

  it('no failure path can write to the panel’s durable result line', () => {
    // The rename IS the fix. If `importResult` ever appears in a catch, the shared-channel defect is back.
    for (const b of catchBodies(portability())) {
      expect(b, `a failure must not write the import's result line:\n${b}`).not.toMatch(/setImportResult\(/)
    }
    const src = portability()
    expect(src, 'the old shared name must not come back').not.toMatch(/\bsetMsg\(/)
    expect(src, 'and the line still renders the import result').toMatch(/\{importResult && </)
  })

  it('the export path reports its own action by name, and still toasts on success', () => {
    const src = portability()
    expect(src, 'naming the action is what makes a toast locatable').toMatch(/Couldn't export \$\{spec\.label\.toLowerCase\(\)\}/)
    expect(src, 'success was already right — it must stay').toMatch(/export downloaded`, 'success'\)/)
  })

  it('the sibling panel on the same endpoints is still the precedent this converged onto', () => {
    // If `DurabilityPanel` ever stops using the error channel, the "canonical form" claim above is stale
    // and this convergence needs re-arguing rather than silently becoming the outlier itself.
    const dur = readFileSync(join(SETTINGS, 'DurabilityPanel.tsx'), 'utf8')
    // 🪤 NOT `notify\([^)]*'error'\)` — every one of these interpolates
    // `${String((e as Error)?.message || e)}`, so a `[^)]*` run stops at the first inner `)` and the
    // match count reads 0. Count the tone argument itself; the same trap is recorded twice above.
    expect((dur.match(/, 'error'\)/g) || []).length,
      'DurabilityPanel reports its failures through notify()').toBeGreaterThanOrEqual(5)
  })

  // ── the measured family ─────────────────────────────────────────────────────────────────────────
  const TONE = /text-danger|text-warn|var\(--color-danger\)|var\(--color-warning\)|bg-danger|border-danger|role="alert"|LoadError|AlertTriangle|<Banner/
  const MESSAGE_SHAPED = /^(msg|err|error)$|Msg$|Err$|Error$/

  /** Panels whose caught-failure MESSAGE state renders with no error tone at any of its sites. */
  function mutedFailureStates(): string[] {
    const out: string[] = []
    for (const f of readdirSync(SETTINGS).filter((x) => /\.tsx$/.test(x) && !/\.test\./.test(x))) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      const caught = new Set<string>()
      for (const body of catchBodies(src)) {
        if (/notify\(/.test(body)) continue
        for (const s of body.matchAll(/\bset([A-Z]\w*)\(/g)) caught.add(s[1][0].toLowerCase() + s[1].slice(1))
      }
      for (const v of caught) {
        if (!MESSAGE_SHAPED.test(v)) continue
        const sites = [...src.matchAll(new RegExp(`\\{${v}(?:\\s*&&|\\})`, 'g'))]
        if (!sites.length) continue
        const toned = sites.filter((s) => TONE.test(src.slice(Math.max(0, s.index! - 320), s.index! + 200)))
        if (!toned.length) out.push(`${f}:${v}`)
      }
    }
    return out
  }

  it('the panel this cycle fixed has left the family', () => {
    // Not a tautology: `mutedFailureStates` reads the same tree the assertion above constrains, so this
    // is the end-to-end proof that routing failures to `notify` removes a member from the census.
    expect(mutedFailureStates(), `${P} must no longer hold a muted failure message`)
      .not.toContain(`${P}:importResult`)
    expect(mutedFailureStates().filter((s) => s.startsWith(P)), `nor any other state in ${P}`).toEqual([])
  })

  it('the remainder is a measured ceiling of 6 — classify, do not add', () => {
    const muted = mutedFailureStates()
    expect(muted.length, `a caught failure in the hint's voice:\n${muted.join('\n')}`).toBeLessThanOrEqual(6)
    // Vacuity floor: if the scan stops finding the population the ceiling passes for the wrong reason.
    expect(muted.length, 'the census must still see the family it is bounding').toBeGreaterThan(0)
  })
})
