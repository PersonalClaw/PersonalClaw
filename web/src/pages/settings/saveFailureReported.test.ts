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

  it('no OTHER settings panel silently swallows a save', () => {
    // Vacuity floor + ratchet in one: scan every settings panel for a `save*`/`patchConfig` call whose
    // chain ends in an empty catch. Reads, deletes and confirm-flows are out of scope — this is about a
    // write that a control has already optimistically reflected.
    const files = readdirSync(SETTINGS).filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))
    expect(files.length, 'the settings panels must be discoverable').toBeGreaterThan(20)
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      for (const m of src.matchAll(/api\.(save[A-Z]\w*|patchConfig)\(/g)) {
        const chain = src.slice(m.index!, m.index! + 420)
        if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain)) {
          offenders.push(`${f}:${src.slice(0, m.index).split('\n').length} (${m[1]})`)
        }
      }
    }
    expect(offenders, 'an optimistic write with an empty catch tells the user nothing').toEqual([])
  })
})
