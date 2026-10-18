import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A notification action that fails must say so ─────────────────────────────────
//
// Eight notification mutations across two files swallowed their rejection, and every one is followed by a
// refetch — so a failure REVERTS the row and the click reads as a no-op. Measured with the mutation
// endpoints at 500 (route-intercepted, nothing written):
//
//   before   clicked "Mark all read" → toasts []            no error text anywhere
//   after    clicked "Mark all read" → "Couldn't mark all notifications read: {"detail":"probe"}"
//
// The read half was the false-empty shape: `.catch(() => [])` made a failed feed render **"You're all caught
// up"** — a reassuring sentence produced by a 500. Measured before/after with the GET at 500:
//
//   before   "You're all caught up …"                       alerts []
//   after    "Couldn't load your notifications …" + Retry
//
// ⚠️ TWO DELIBERATE ASYMMETRIES, both about POLLING:
//   • The page's feed read reports through `LoadError` (render state, shown once) and NOT `notify` — it
//     polls every 10s, so a toast per tick would be spam.
//   • `NotificationBell`'s read does NOT toast: it polls once a minute plus on every notification WS
//     frame. It keeps the failure as state instead, so a failed first read shows an unknown count
//     rather than a false zero, and a later failure keeps the previous items, the honest fallback for
//     a badge. Its three MUTATIONS are user-initiated and do report.

const SRC = join(process.cwd(), 'src')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

const PAGE = 'pages/notifications/NotificationsPage.tsx'
const BELL = 'ui/NotificationBell.tsx'

/** Every user-initiated notification mutation, and where it lives. */
const MUTATIONS: Array<[string, string]> = [
  [PAGE, 'ackNotification'],
  [PAGE, 'unackNotification'],
  [PAGE, 'deleteNotification'],
  [PAGE, 'ackAllNotifications'],
  [PAGE, 'clearNotifications'],
  [BELL, 'ackNotification'],
  [BELL, 'deleteNotification'],
  [BELL, 'ackAllNotifications'],
]

describe('a failed notification action is reported', () => {
  it.each(MUTATIONS)('%s reports a failed %s', (rel, call) => {
    const src = read(rel)
    const at = src.indexOf(`api.${call}(`)
    expect(at, `${rel} must still call ${call}`).toBeGreaterThan(-1)
    const chain = src.slice(at, at + 320)
    expect(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain), `${rel}: a silent catch makes the click a no-op`).toBe(false)
    expect(chain, `${rel}: the rejection must be captured`).toMatch(/\.catch\(\((?:e|err|error)\)\s*=>/)
    expect(chain, `${rel}: and reported with notify()`).toMatch(/notify\(/)
  })

  it('the feed read no longer fabricates an empty list', () => {
    const src = read(PAGE)
    expect(/api\.notifications\(\)[\s\S]{0,120}\.catch\(\(\)\s*=>\s*\[\]/.test(src),
      '"You\'re all caught up" is a claim, not an error').toBe(false)
    expect(src, 'the hook error must be read').toMatch(/error:\s*loadErr/)
    expect(src, 'and rendered before the skeleton branch').toMatch(/items === undefined && loadErr/)
    const errAt = src.search(/<LoadError\b/)
    const skelAt = src.search(/<ListSkeleton\b/)
    expect(errAt).toBeGreaterThan(-1)
    expect(errAt, 'a failed first read would otherwise spin the skeleton forever').toBeLessThan(skelAt)
  })

  it("the bell's POLLED read keeps its failure as state and never toasts it, deliberately", () => {
    // Documented distinction, pinned so a later sweep does not "converge" it into a toast on every poll.
    // The poll became a once-a-minute safety net in #3620 (frames keep the badge current), so the pin
    // is the reason itself rather than an interval that no longer exists. The failure is not swallowed
    // either: it is kept, so the bell can show an unknown count instead of a false zero
    // (`ui/bellUnknownWhenUnread.test.tsx` holds the rendering).
    const src = read(BELL)
    const at = src.indexOf('api.notifications()')
    expect(at, 'the bell must still read').toBeGreaterThan(-1)
    const chain = src.slice(at, at + 200)
    expect(chain, 'the failure is kept as state').toMatch(/\.catch\(\(e\) => setReadErr\(e\)\)/)
    expect(chain, 'and the poll does not report it').not.toMatch(/notify\(/)
    expect(readFileSync(join(SRC, BELL), 'utf8'), 'and the reason must stay written down')
      .toMatch(/reporting a failed poll would toast every tick/)
  })

  it('reads the real files (not vacuously green)', () => {
    expect(read(PAGE).length).toBeGreaterThan(4000)
    expect(read(BELL).length).toBeGreaterThan(1500)
  })
})
