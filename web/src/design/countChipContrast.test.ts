import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── A dimmed token must not be dimmed AGAIN with opacity ────────────────────
//
// `text-on-surface-low` (#9a9b9c) IS the muted step of the ink ramp: 5.93:1 on
// `surface-container`, comfortably over the 4.5:1 AA floor. Wrapping it in `opacity-60/70`
// halves an already-intentional value and pushes it UNDER:
//
//     on-surface-low, no opacity      → 5.93:1  ✅
//     on-surface-low @ opacity-70     → 3.62:1  ❌
//     on-surface-low @ opacity-60     → 3.02:1  ❌
//
// axe agreed independently (`color-contrast`, serious) on #/tools and #/settings/memory.
// The canonical count chip — `LoopsListPage` — has never carried opacity.
//
// This is a SOURCE scan rather than a render assertion because the family is defined by a
// class pairing, and the three call sites live on three unrelated pages: a render test would
// need all three mounted with data, and would still miss a fourth site added later.
//
// Deliberately narrow: it bans `opacity-*` only where it multiplies a DIMMED ink token on the
// same element. Opacity remains correct for hover-reveal (`opacity-0 group-hover:opacity-100`),
// disabled states, done/struck rows, and icons — none of which this pattern matches.

const WEB_SRC = join(process.cwd(), 'src')

/** Every `className` string literal in the tree, with its file + line. */
function classAttributes(): Array<{ file: string; line: number; value: string }> {
  const out: Array<{ file: string; line: number; value: string }> = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name)
      if (entry.isDirectory()) { walk(p); continue }
      if (!/\.tsx$/.test(entry.name) || /\.test\.tsx$/.test(entry.name)) continue
      readFileSync(p, 'utf8').split('\n').forEach((ln, i) => {
        for (const m of ln.matchAll(/className="([^"]*)"/g)) {
          out.push({ file: p.slice(WEB_SRC.length + 1), line: i + 1, value: m[1] })
        }
      })
    }
  }
  walk(WEB_SRC)
  return out
}

const DIMS = /\bopacity-(40|50|60|70|75|80)\b/
// `tabular-nums` marks the count-chip idiom specifically: a NUMBER rendered beside a label —
// which is text, and therefore subject to 1.4.3. Scoping to it keeps the rail off the cases
// where opacity is correct and 1.4.3 does not apply:
//   · ICONS / glyphs (a dimmed GripVertical, a Spark mark) — decorative, not text.
//   · hover-reveal (`opacity-* transition-opacity`) — the rest state is deliberately faint.
//   · disabled and done/struck rows — a lower contrast IS the message.
// Those made up every other hit when the sweep was keyed on the ink token alone; a text-only
// rail is the one that stays true.
const COUNT_ISH = /\btabular-nums\b/

describe('count chips do not double-dim a dimmed token', () => {
  const attrs = classAttributes()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(attrs.length).toBeGreaterThan(500)
  })

  it('no tabular-nums count carries an opacity-* dimmer', () => {
    // Catches the same defect when the colour is inherited from the chip rather than named on
    // the span — which is how two of the three shipped sites were written.
    const offenders = attrs.filter((a) => COUNT_ISH.test(a.value) && DIMS.test(a.value))
      .map((a) => `${a.file}:${a.line} — ${a.value.slice(0, 90)}`)
    expect(offenders, `A count chip inherits its chip's already-dimmed colour; opacity halves it again.\n${offenders.join('\n')}`).toEqual([])
  })
})
