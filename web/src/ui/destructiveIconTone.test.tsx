import { describe, it, expect } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Trash2 } from 'lucide-react'
import { IconButton } from './IconButton'

// ── The primitive WITH the danger affordance guarded the reversible edits ──────────────────────────
//
// `ui/SquareIconButton` has carried `tone?: 'neutral' | 'danger'` for a while, and 14 of its
// destructive call sites pass it — including three in `pages/tasks/formControls` that only splice a row
// out of an UNSAVED draft array. `ui/IconButton` had no such prop at all. So the split fell the wrong
// way round: the tier with the affordance guarded the reversible edits, and the tier without it guarded
// **nine persisted destroys** — Delete chat, Delete comment (two surfaces), Delete report, Delete
// template, and the two armed list deletes — each wearing the same neutral grey as a Copy button.
//
// 🔑 TWO SITES HAD ALREADY HAND-ROLLED IT, which is the clearest evidence the prop was missing rather
// than unwanted: `ChatPage`'s Delete chat and `TaskDetail`'s Delete comment both carried
// `hover:text-danger` inside their `className`. Those two now pass the prop and drop the duplicate.
//
// 🪤 THE CLASS IS NOT COPIED FROM THE SIBLING — ONLY THE RULE IS. `SquareIconButton`'s danger branch is
// `text-on-surface-low hover:text-danger`; this tier's resting ink is `text-on-surface-var`. Pasting the
// sibling's class would silently re-ink every danger button AT REST, which is a different change from
// adding a hover tint. Asserted from both sides below.
//
// 🪤 AND THREE "DESTRUCTIVE-LOOKING" SITES DELIBERATELY STAY NEUTRAL. A verb sweep flags them; reading
// them shows they are not destroys:
//   · `ProjectsSection` "Clear workspace"      → `setWorkspaceDir('')`, clears an INPUT FIELD
//   · `ChatPage` "Remove file/paste/knowledge" → composer attachments, re-attachable draft edits
//   · `ModelsPanel` "Remove … from chain"      → a reversible settings edit
// Tinting those trains people to read red as "routine", which is how a danger colour stops working.

const SRC = join(process.cwd(), 'src')
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')
const codeOf = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

describe('IconButton renders the danger tone as a hover tint, not a fill', () => {
  it('a danger button keeps this tier’s resting ink and tints on hover', () => {
    render(<IconButton icon={Trash2} label="Delete thing" tone="danger" />)
    const cls = screen.getByRole('button', { name: 'Delete thing' }).className
    // The RULE: hover tints the glyph.
    expect(cls).toContain('hover:text-danger')
    // 🪤 …and the resting ink is THIS tier's, not the sibling's. If this ever reads
    // `text-on-surface-low`, someone pasted `SquareIconButton`'s class and changed every danger
    // button's resting colour along with it.
    expect(cls).toContain('text-on-surface-var')
    expect(cls, 'the sibling tier’s ink must not leak in').not.toContain('text-on-surface-low')
    // No fill — a destructive icon button is not a solid red button.
    expect(cls).not.toContain('bg-danger')
    cleanup()
  })

  it('the default is neutral, so no existing call site changes appearance', () => {
    render(<IconButton icon={Trash2} label="Copy thing" />)
    const cls = screen.getByRole('button', { name: 'Copy thing' }).className
    expect(cls).not.toContain('hover:text-danger')
    expect(cls).toContain('hover:text-on-surface')
    cleanup()
  })

  it('`filled` and `active` win over the tone — a selected destructive button is not a pattern here', () => {
    render(<IconButton icon={Trash2} label="Filled danger" tone="danger" filled />)
    const filled = screen.getByRole('button', { name: 'Filled danger' }).className
    expect(filled, 'filled claims the colour').toContain('bg-primary')
    expect(filled).not.toContain('hover:text-danger')
    cleanup()
    render(<IconButton icon={Trash2} label="Active danger" tone="danger" active />)
    const active = screen.getByRole('button', { name: 'Active danger' }).className
    expect(active).toContain('bg-surface-high')
    expect(active).not.toContain('hover:text-danger')
    cleanup()
  })

  it('a disabled danger button still reads as unavailable, not as danger', () => {
    render(<IconButton icon={Trash2} label="Gone" tone="danger" disabled />)
    const cls = screen.getByRole('button', { name: 'Gone' }).className
    expect(cls).toContain('cursor-not-allowed')
    expect(cls).not.toContain('hover:text-danger')
    cleanup()
  })
})

describe('the nine persisted destroys adopted it', () => {
  /** file → the label text that identifies the destructive call site. */
  const ADOPTERS: [string, string][] = [
    ['pages/ChatPage.tsx', 'Delete chat'],
    ['pages/tasks/TaskDetail.tsx', 'Delete comment'],
    ['pages/files/comments/CommentLayer.tsx', 'Remove comment'],
    ['pages/knowledge/ReportsPage.tsx', 'Delete ${report.name}'],
    ['pages/notifications/NotificationsPage.tsx', 'Delete: ${subject}'],
    ['pages/settings/ChatPanel.tsx', 'Delete ${t.name}'],
    ['pages/chat/SdlcProgressCard.tsx', 'Click again to delete'],
    ['pages/loops/LoopsListPage.tsx', 'Delete loop'],
  ]

  for (const [rel, label] of ADOPTERS) {
    it(`${rel} passes tone="danger" on its destructive IconButton`, () => {
      const code = codeOf(rel)
      // The tag containing that label must also carry the tone. Bounded to the tag, so a `tone` on some
      // OTHER button in the same file cannot satisfy this.
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const tag = tags.find((t) => t.includes(label))
      expect(tag, `found the IconButton labelled ${label}`).toBeTruthy()
      expect(tag!, `${label} must declare the danger tone`).toMatch(/tone="danger"/)
    })
  }

  it('🔑 the two hand-rolled copies are GONE from their className', () => {
    // The duplication this prop removes. If `hover:text-danger` reappears in a className, the site is
    // re-implementing the primitive instead of asking for it.
    for (const rel of ['pages/ChatPage.tsx', 'pages/tasks/TaskDetail.tsx']) {
      const code = codeOf(rel)
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const offenders = tags.filter((t) => /className="[^"]*hover:text-danger/.test(t))
      expect(offenders, `${rel} still hand-rolls the danger hover:\n${offenders.join('\n')}`).toEqual([])
    }
  })

  it('🪤 the ARMED className is deliberately KEPT — it is a different signal', () => {
    // `text-danger` (no `hover:`) on an armed two-click delete means "you are one click away", shown
    // persistently. The tone prop supplies the PRE-arm hover tint; replacing the armed class with it
    // would delete the stronger signal at the more dangerous moment.
    for (const rel of ['pages/chat/SdlcProgressCard.tsx', 'pages/loops/LoopsListPage.tsx']) {
      expect(codeOf(rel), `${rel} keeps its armed tint`).toMatch(/\? 'text-danger'/)
    }
  })
})

describe('🔴 the composer controls were ALREADY tinted, and that decision stands', () => {
  // A correction worth recording, because the tempting move was the wrong one. My first pass classified
  // the composer's remove-attachment controls as reversible draft edits that should stay neutral —
  // "tinting a routine edit teaches red as routine". Then the hand-rolled sweep above found FIVE more
  // `hover:text-danger` classNames in `ChatPage`, on exactly those controls plus the two cancels.
  //
  // So the shipped design had already made the opposite call, consistently, across five controls. Acting
  // on my classification would have REMOVED a tint the product deliberately has — a visual regression
  // imposed by taste over a consistent existing decision. They adopt the prop instead, which preserves
  // their appearance byte-for-byte and removes the duplicate.
  const ALREADY_TINTED = [
    'Cancel upload', 'Remove file', 'Remove knowledge reference', 'Cancel queued message', 'Remove paste',
  ]
  for (const label of ALREADY_TINTED) {
    it(`ChatPage — "${label}" keeps its danger tint, now via the prop`, () => {
      const tags = [...codeOf('pages/ChatPage.tsx').matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const tag = tags.find((t) => t.includes(label))
      expect(tag, `found the IconButton for ${label}`).toBeTruthy()
      expect(tag!, 'appearance preserved through the prop').toMatch(/tone="danger"/)
    })
  }
})

describe('the two that stay neutral, because they are not destroys', () => {
  const NEUTRAL: [string, string][] = [
    // `setWorkspaceDir('')` — clears an INPUT FIELD, nothing stored.
    ['pages/projects/ProjectsSection.tsx', 'Clear workspace'],
    // A reversible settings edit: the entry can be added back.
    ['pages/settings/ModelsPanel.tsx', 'from chain'],
  ]
  for (const [rel, label] of NEUTRAL) {
    it(`${rel} — "${label}" is NOT tinted`, () => {
      const code = codeOf(rel)
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const tag = tags.find((t) => t.includes(label))
      expect(tag, `found the IconButton for ${label}`).toBeTruthy()
      // Neither carries a hand-rolled tint today either, so leaving them neutral preserves the
      // shipped appearance — the same standard applied to the composer controls, in the other direction.
      expect(tag!, `${label} changes nothing stored`).not.toMatch(/tone="danger"/)
      expect(tag!, 'and it is not hand-rolling one').not.toMatch(/hover:text-danger/)
    })
  }
})

describe('VACUITY: the sweep is measuring something', () => {
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

  it('IconButton still has many call sites, and the danger ones are the minority', () => {
    // A tone that spread everywhere would stop meaning anything; one that spread nowhere is a dead
    // prop. Both failure modes are bounded here.
    let total = 0
    let danger = 0
    for (const abs of walk(SRC)) {
      const code = strip(readFileSync(abs, 'utf8'))
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      total += tags.length
      danger += tags.filter((t) => /tone="danger"/.test(t)).length
    }
    expect(total, 'IconButton call sites').toBeGreaterThanOrEqual(30)
    expect(danger, 'danger-toned sites').toBeGreaterThanOrEqual(8)
    expect(danger, 'danger stays the exception, or the colour stops meaning anything')
      .toBeLessThan(total / 3)
  })

  it('the sibling tier still declares the tone this one borrowed the rule from', () => {
    // If `SquareIconButton` ever drops it, the "two tiers, one rule" story needs re-deriving.
    expect(codeOf('ui/SquareIconButton.tsx')).toMatch(/tone\?: 'neutral' \| 'danger'/)
  })
})
