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
