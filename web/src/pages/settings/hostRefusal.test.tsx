import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { hostRefusal } from './SecurityPanel'

// ── Three silent refusals, on the panel that decides what the agent may reach ──────────────────────
//
// `#/settings/security` → Network egress → "Allowed hosts". Driven live, typing and pressing Enter:
//
//   input                  list      draft      any error / live region
//   https://nas.local      unchanged KEPT       none
//   nas.local:8080         unchanged KEPT       none
//   two words              unchanged KEPT       none
//   nas.local  (valid)     +1        cleared    —
//   nas.local  (again)     unchanged **CLEARED** none      ← reads as SUCCESS
//
// On this surface that is a security defect, not a rough edge: a user who pastes `https://nas.local`
// believes the homelab host is now reachable, and it is not. The duplicate case is worse still, because
// clearing the box is exactly what a successful add does — the interface actively said "done".
//
// 🔑 THE PANEL ALREADY OWNED THE ANSWER. `FieldError` (`role="alert"`, danger ink) is imported in this
// same file and used for both save failures; the add path was the one refusal that never spoke. Same
// family as `ui/fieldErrorAnnounced`: "the failure is on screen" and "the user was told" are different
// claims, and only the second one is worth anything.
//
// 🪤 KEEPING THE DRAFT IS PART OF THE FIX, not a detail. A refusal that empties the input destroys the
// text the user must correct AND mimics success. Asserted below, because it is the half most likely to
// be "tidied up" later.

const PANEL = join(process.cwd(), 'src', 'pages', 'settings', 'SecurityPanel.tsx')
const read = () => readFileSync(PANEL, 'utf8')

describe('a refused host says why', () => {
  it('a pasted URL is refused with the rule, not silence', () => {
    for (const bad of ['https://nas.local', 'http://nas.local/path', 'nas.local:8080', 'two words']) {
      const why = hostRefusal(bad, [])
      expect(why, `${bad} must be refused`).toBeTruthy()
      expect(why, `${bad}: the message must state the rule`).toMatch(/bare hostname/)
      expect(why, 'and show the shape that works').toMatch(/nas\.local/)
    }
  })

  it('a duplicate names the host that is already there', () => {
    expect(hostRefusal('nas.local', ['nas.local'])).toBe('nas.local is already listed.')
    // Case and padding are normalised before the comparison, so a re-typed entry is still caught.
    expect(hostRefusal('  NAS.Local  ', ['nas.local'])).toBe('nas.local is already listed.')
  })

  it('a valid host is not refused', () => {
    expect(hostRefusal('nas.local', [])).toBeNull()
    expect(hostRefusal('printer.lan', ['nas.local'])).toBeNull()
    // Empty is the Add button's own gate (`unavailableWhen`), not an error to shout about.
    expect(hostRefusal('   ', [])).toBeNull()
  })

  it('the refusal is ANNOUNCED, not merely rendered', () => {
    const src = read().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'the add path must render through FieldError').toMatch(/\{refused && <FieldError>\{refused\}<\/FieldError>\}/)
    expect(src, 'a hand-rolled danger line would be silent — that is this family\'s defect')
      .not.toMatch(/refused && <p className="text-danger/)
    expect(src, 'and the input is marked invalid while it stands').toMatch(/aria-invalid=\{refused \? true : undefined\}/)
  })

  it('a refusal KEEPS the draft, and typing clears the refusal', () => {
    const src = read().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    // 🪤 The old code did `if (!h || hosts.includes(h)) { setDraft(''); return }` — emptying the box on a
    // duplicate, which is indistinguishable from a successful add.
    expect(src, 'the duplicate branch must not clear the draft').not.toMatch(/hosts\.includes\(h\)\) \{ setDraft\(''\)/)
    expect(src, 'a refusal sets the message and returns').toMatch(/if \(why\) \{ setRefused\(why\); return \}/)
    expect(src, 'a successful add clears both').toMatch(/setRefused\(''\); onChange\(\[\.\.\.hosts, h\]\); setDraft\(''\)/)
    expect(src, 'and editing the text dismisses the stale refusal').toMatch(/if \(refused\) setRefused\(''\)/)
  })

  it('both lists get this — the component renders twice', () => {
    // `HostList` is used for BOTH "Allowed hosts" and "Denied hosts". A fix that only reached one would
    // leave the deny box silent, which is the direction that fails closed-looking but open.
    const src = read()
    expect((src.match(/<HostList\b/g) || []).length, 'allow + deny').toBe(2)
    expect((src.match(/function HostList\b/g) || []).length, 'one implementation, so one fix').toBe(1)
  })
})
