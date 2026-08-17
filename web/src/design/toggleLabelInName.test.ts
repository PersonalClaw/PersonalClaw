import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── WCAG 2.5.3 Label in Name (Level A): a switch's name must contain what the row says ────────────
//
// `ui/Toggle` puts its `label` prop on the switch as **`aria-label`**, and an `aria-label` OVERRIDES
// every other naming source. So when a `Row` renders the visible text and the `Toggle` carries its own
// shorter string, the accessible name does not contain the visible label — and a speech-input user who
// says what they can see does not activate the control.
//
// Measured from the ACCESSIBILITY TREE, not from the source: a runtime census over 12 settings routes
// read `[role="switch"]` computed names against their rendered row text. 33 switches, and four failed:
//
//   #/settings/notifications   "Mute all notifications"          → name "Mute all"
//   #/settings/notifications   "Enable quiet hours"              → name "Quiet hours"
//   #/settings/chat            "Restore sessions on startup"     → name "Restore sessions"
//   #/settings/chat            "Confirm before closing a session" → name "Confirm before closing"
//
// A FIFTH lives in `NotificationRulesMatrix`: visible "Escalate on name mention" → name "Name mention".
// The runtime census could not see it, because those rows sit inside a COLLAPSED per-kind section and are
// absent from the initial DOM a page scan reads. The source sweep below found it. **Neither method is
// complete alone** — the runtime pass caught what the source regex first missed (an arrow-function trap),
// and the source pass caught what the runtime pass could not render.
//
// Every one is the same shape: the name is a TRUNCATION of the visible label. The fix is to use the
// row's own string, which is what the other 29 switches already do — `VoicePanel` even threads one
// `enableLabel` into both, the canonical form this converges onto.
//
// 🪤 TWO THINGS THE RUNTIME CENSUS GOT WRONG, and both were checked rather than trusted:
//   · `#/settings/voice` reported two failures with a visible label of "no model". That was the PROBE:
//     its walker takes the first text run near the switch, and on a seed with no bound model a "no
//     model" status chip sits closer than the row label. Both toggles actually pass — `VoicePanel`
//     threads `enableLabel` into the Row AND the Toggle.
//   · `#/settings/chat`'s `Offer “Check this work”` differs from its name `Offer 'Check this work'` in
//     the QUOTE GLYPHS only. 2.5.3 is about what a user can say, and nobody speaks a curly quote, so
//     that one is satisfied and is deliberately left alone.
//
// 🪤 AND THE SOURCE-LEVEL SWEEP BELOW HAS TO NEUTRALISE `=>` FIRST. A `[^>]*` inside a JSX tag stops at
// the `>` in `onChange={(v) => …}`, so the first version of this census found ZERO mismatches in a file
// that demonstrably had one. Third time this trap has bitten in this session.
//
// The change is invisible: an `aria-label` paints nothing.

const SRC = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

// A speech user says words, not punctuation: fold case, drop quote glyphs, treat hyphens as spaces.
const spoken = (s: string) => s.toLowerCase().replace(/[‘’“”'"`]/g, '').replace(/[-–—]/g, ' ').replace(/\s+/g, ' ').trim()

type Pair = { file: string; visible: string; name: string }

function pairs(): Pair[] {
  const found: Pair[] = []
  for (const abs of walk(SRC)) {
    // 🪤 `=>` must die before any `[^>]*` can mean "still inside the tag".
    const code = readFileSync(abs, 'utf8').replace(/=>/g, '⇒')
    const re = /<(?:Row|Field)\s+label="([^"]+)"[^>]*>\s*(?:\{[^}]*\}\s*)?(?:<div[^>]*>\s*)?(?:<[A-Z][^>]*\/>\s*)*<Toggle\b[^>]*?label="([^"]+)"/gs
    for (const m of code.matchAll(re)) found.push({ file: abs.replace(SRC + '/', ''), visible: m[1], name: m[2] })
  }
  return found
}

describe('a toggle’s accessible name contains its visible row label', () => {
  const all = pairs()

  it('the sweep sees a real population — the vacuity floor', () => {
    // If this drops to nothing the regex has rotted (see the `=>` trap above) and the rule below would
    // pass while measuring no one.
    expect(all.length, 'Row/Field-wrapped labelled toggles found').toBeGreaterThanOrEqual(25)
  })

  it('no switch name truncates the label the user can see', () => {
    const offenders = all
      .filter((p) => !spoken(p.name).includes(spoken(p.visible)))
      .map((p) => `${p.file}: visible ${JSON.stringify(p.visible)} → name ${JSON.stringify(p.name)}`)
    expect(offenders, 'WCAG 2.5.3 Label in Name').toEqual([])
  })

  it('the five sites use the row’s own words', () => {
    const notif = readFileSync(join(SRC, 'pages/settings/NotificationsPanel.tsx'), 'utf8')
    const chat = readFileSync(join(SRC, 'pages/settings/ChatPanel.tsx'), 'utf8')
    expect(notif).toMatch(/label="Mute all notifications" \/>/)
    expect(notif).toMatch(/label="Enable quiet hours" \/>/)
    expect(chat).toMatch(/label="Restore sessions on startup" \/>/)
    expect(chat).toMatch(/label="Confirm before closing a session" \/>/)
    expect(readFileSync(join(SRC, 'pages/settings/NotificationRulesMatrix.tsx'), 'utf8'))
      .toMatch(/label="Escalate on name mention" \/>/)
  })

  it('the visible rows are untouched — this changed a NAME, not any copy', () => {
    // The fix must not have quietly reworded what the user reads.
    const notif = readFileSync(join(SRC, 'pages/settings/NotificationsPanel.tsx'), 'utf8')
    const chat = readFileSync(join(SRC, 'pages/settings/ChatPanel.tsx'), 'utf8')
    expect(notif).toMatch(/<Row label="Mute all notifications" hint="Pause every notification regardless of severity\.">/)
    expect(notif).toMatch(/<Row label="Enable quiet hours">/)
    expect(chat).toMatch(/<Row label="Restore sessions on startup"/)
    expect(chat).toMatch(/<Row label="Confirm before closing a session"/)
  })

  it('the quote-glyph pair is deliberately left alone', () => {
    // Satisfied in speech; only the glyphs differ. If someone "fixes" it, the header's reasoning about
    // what 2.5.3 actually requires needs revisiting rather than silently passing.
    const chat = readFileSync(join(SRC, 'pages/settings/ChatPanel.tsx'), 'utf8')
    expect(chat).toMatch(/label="Offer 'Check this work'"/)
  })

  it('Toggle still puts its label on the switch as aria-label — the mechanism this rests on', () => {
    // If `label` ever stops being the accessible name, the whole rule changes shape.
    expect(readFileSync(join(SRC, 'ui/Toggle.tsx'), 'utf8')).toMatch(/role="switch"[^>]*aria-label=\{label\}/)
  })
})
