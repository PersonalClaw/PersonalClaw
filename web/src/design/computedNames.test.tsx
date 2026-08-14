import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { TileButton } from '../ui/TileButton'
import { IconButton } from '../ui/IconButton'
import { Trash2 } from 'lucide-react'

// ── What Chrome COMPUTES as a control's name, which is not what the source says ─────────────────
//
// Four cycles (136, 137, 139, 140) shipped changes whose entire effect is announcement, and every one
// was verified by reading ATTRIBUTES off the DOM. This cycle read the layer below: Chrome's computed
// accessibility tree over CDP (`Accessibility.getFullAXTree`), 17 routes, **1260 exposed interactive
// nodes**. Attributes are the input; the AX tree is the output, and the two disagree in both
// directions.
//
// It confirmed the four cycles (23 dashboard row actions → 22 distinct computed names; the context
// menu's focus really lands on a `menuitem`; the typeahead editor really carries an activedescendant
// RELATION, not just an attribute) and **found two defects that attribute-reading cannot see**:
//
//   #/artifacts       5 tiles whose computed name was **438-695 characters** of their own rendered
//                     markdown preview — heading `#`, `**` emphasis and blockquote `>` included.
//                     Source looks fine: the tile passes `title={art.name}`. A button with CONTENT
//                     takes its name from the content, and `title` loses to it (cycle 139's lesson,
//                     one layer deeper).
//   #/notifications   83× "Investigate in chat", 83× "Delete", 81× "Mark unread" — three names for
//                     **247 controls**, on the one list surface cycle 139's DOM census undercounted
//                     (its row grouping keyed on 40 characters of the row's text, so it reported
//                     "none"). The AX tree does the grouping properly.
//
// 🔑 ONE RULE, TWO FAILURE MODES: a row control's name must be DISTINGUISHING **and** BOUNDED. Too
// little is 83 rows sharing a verb; too much is a paragraph. Measured trade-off at three caps:
//
//   cap      worst duplicate on #/notifications      interactive names >80ch app-wide
//   (none)   ×83 → ×3                                50 → 219
//   60       ×3                                      50 → 114
//   **55**   ×3                                      50 → **45**
//
// 55 beats the baseline on BOTH metrics. 🪤 The first version of this shipped at 90 and traded three
// duplicate names for 169 new over-long ones — caught only by re-running the sweep, and the reason the
// comment in NotificationsPage carries the table rather than a claim.
//
// 🪤 THE FIRST ATTEMPT AT THE FIX DID NOT FIX IT EITHER: naming from `n.title` alone left 35×
// "…: Refine a skill" and 26× "…: Loop progress", because the title is a KIND on this surface, not an
// identity. Second time in three cycles that the obvious subject field was not the distinguishing one
// (cycle 140's was `e.title` on inbox proposals). **Re-measure after composing a name.**

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const codeOf = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

// ── Cycle 154: THREE BUTTONS WHOSE ENTIRE BODY IS AN ICON, AND SO HAD NO NAME AT ALL ────────────
//
// `#/knowledge` → **Intents** had never been audited: the ledger's coverage for this surface is the
// Library view, and four of its five tabs had never been driven. Driven at both themes, the Intents
// view reported axe **`button-name` [critical]** on a 46×32 control at the right edge of every intent
// row — `<Button size="sm" variant="ghost"><Trash2 /></Button>`, a **destructive** action announcing
// as bare "button".
//
// A depth-aware source census found **three** of that exact shape:
//
//   knowledge/KnowledgeListPage  <Trash2/>     delete an intent   ← driven, axe-confirmed
//   settings/MemoryPanel         <RefreshCw/>  reload the audit log
//   projects/ProjectsSection     <Check/>      confirm a rename — **its sibling Cancel WAS named**
//
// 🪤 A NAIVE `<Button([^>]*)>` MATCHER FINDS ONE OF THE THREE. `onClick={() => …}` contains a `>`, so
// the attribute group stops early and the children never parse — the recorded JSX-matcher trap, and it
// hid the two sites that use an arrow function. The census below walks the tag with BRACE DEPTH.
//
// 🔑 THE FIX IS A PRIMITIVE PROP, NOT A TOOLTIP. `Button` had `title`, `ariaExpanded` and `ariaPressed`
// but no way to carry a name, so it gained `ariaLabel` — `title` alone is not an accessible name in
// every engine (the rule this repo already wrote down on `DegradedChip`). The delete name goes through
// `rowSubject` so an intent whose goal is a sentence cannot turn a control's name into a paragraph,
// which is this file's own 55-character rule applied at a 40-char budget.
//
// After: Intents reports axe **2 → 1** (the survivor is a contrast defect on the same view, its own
// concern) and **0** unnamed controls at either theme.

describe('a tile whose content is a document needs an explicit name', () => {
  it('TileButton takes ariaLabel, and it wins over the content', () => {
    render(
      <TileButton title="Design Notes" ariaLabel="Design Notes">
        <div># Verdant Hollow — Design Notes ## Core loop - Explore procedurally-generated forests…</div>
      </TileButton>,
    )
    const b = screen.getByRole('button', { name: 'Design Notes' })
    expect(b.getAttribute('title')).toBe('Design Notes')
  })

  it('without it the name IS the content — the defect, pinned', () => {
    render(<TileButton title="Design Notes"><div># Verdant Hollow — Design Notes ## Core loop</div></TileButton>)
    const b = screen.getByRole('button', { name: /Verdant Hollow/ })
    expect(b, 'title does not win over content').toBeTruthy()
  })

  it('the artifact card passes the artifact name', () => {
    // Cycle 153 removed the `active={active}` that used to sit between `onClick` and `title`: it was
    // threaded from a hard-coded `activeSlug={null}`, so it could never be true. The assertion this
    // test exists for — the tile carries an explicit `ariaLabel` instead of 438-695 characters of
    // markdown preview — is unchanged.
    expect(codeOf('pages/artifacts/ArtifactCard.tsx'))
      .toMatch(/<TileButton onClick=\{\(\) => onOpen\(art\)\} title=\{art\.name\} ariaLabel=\{art\.name\}/)
    expect(codeOf('pages/artifacts/ArtifactCard.tsx'), 'and it claims no selection state')
      .not.toMatch(/active=\{active\}/)
  })
})

describe('an icon button can carry a row name without a paragraph tooltip', () => {
  it('IconButton takes a title override, like SquareIconButton already did', () => {
    render(<IconButton icon={Trash2} label="Delete: Loop progress — cycle 4 finished" title="Delete" onClick={vi.fn()} />)
    const b = screen.getByRole('button', { name: 'Delete: Loop progress — cycle 4 finished' })
    expect(b.getAttribute('title'), 'the hover hint stays the bare verb').toBe('Delete')
  })

  it('defaults the tooltip to the label when no override is given', () => {
    render(<IconButton icon={Trash2} label="Delete" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete' }).getAttribute('title')).toBe('Delete')
  })

  it('the override composes with disabledReason rather than replacing it', () => {
    render(<IconButton icon={Trash2} label="Delete: a row" title="Delete" disabled disabledReason="Nothing selected" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete: a row' }).getAttribute('title')).toBe('Delete — Nothing selected')
  })
})

describe("the notification row actions name their row, and stay bounded", () => {
  const code = codeOf('pages/notifications/NotificationsPage.tsx')

  it('all four actions name the row through the shared helper', () => {
    // Cycle 142 moved the composition into `lib/rowSubject` (one rule, one number, two surfaces), so
    // this asserts the call rather than a local copy of the join.
    //
    // Cycle 164 bound it ONCE — the row's own hit target needs the same name, and five call sites
    // recomputing an identical expression is how two of them drift. So the shape asserted here moved
    // from the inline call to the binding plus its uses, which also pins that they cannot diverge.
    expect(code, 'the row subject is computed once')
      .toMatch(/const subject = rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    for (const verb of ['Investigate in chat', 'Mark unread', 'Mark read', 'Delete']) {
      expect(code, `${verb} must name its row`).toMatch(new RegExp(`\`${verb}: \\$\\{subject\\}\``))
    }
    // And the row itself announces the same subject, not its whole 2001-character subtree.
    expect(code, 'the row hit target shares the actions\' subject').toMatch(/<RowHitTarget label=\{subject\} \/>/)
  })

  it('the composition and its cap live in the shared helper, not here', () => {
    expect(code).toMatch(/rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    expect(code, 'a local re-implementation is the drift this closed').not.toMatch(/function rowName/)
    expect(code, 'and its number with it').not.toMatch(/full\.length > 55/)
  })

  it('the tooltips stay the bare verbs', () => {
    expect(code).toMatch(/title="Mark unread"/)
    expect(code).toMatch(/title="Mark read"/)
    expect(code).toMatch(/title="Delete"/)
  })

  it('InvestigateButton keeps its bare-verb default for single-instance use', () => {
    const inv = codeOf('ui/InvestigateButton.tsx')
    expect(inv).toMatch(/label=\{label \?\? 'Investigate in chat'\}/)
    expect(inv).toMatch(/title="Investigate in chat"/)
  })
})


// ── Cycle 156: THE NAME WAS WRITTEN AND THE PRIMITIVE THREW IT AWAY ─────────────────────────────
//
// Sweeping the views behind every surface's view switcher (the lens cycle 154 opened) found
// `#/triggers` → **Week** reporting `button-name` [critical] **twice**, at both themes — the week's
// prev/next arrows. The source looked correct:
//
//     <Button size="sm" variant="ghost" aria-label="Previous week" …><ChevronLeft /></Button>
//
// 🪤 **`Button` never forwarded `aria-label`, and TypeScript cannot say so: a JSX attribute containing a
// HYPHEN is not checked against a component's props type.** So the name was dropped in silence, the
// build stayed green, and the only witness was the accessibility tree. The prop is `ariaLabel` — the one
// cycle 154 added, for exactly this shape.
//
// This is the second time this session that a control's name existed and never reached the user
// (cycle 148: a `LoadError` noun the loading state did not borrow). **A name in the source is not a name
// in the tree.**
//
// The same sweep found one more, in the same class of never-audited view: `#/skills` → **Browse** has a
// raw `<select>` with no label at all — `select-name` [critical], both themes. It is the marketplace
// picker; it now carries `aria-label="Marketplace"`.
//
// 🔑 SIZING THE SELECT QUESTION HONESTLY. A source scan says **11 of 24** raw `<select>`s carry no
// `aria-label` / `aria-labelledby` / `id` — and that count is misleading. axe, which implements the
// naming rules (wrapping label included), fires on **exactly one** across every route and view swept:
// the rest are named by other means or not rendered. The DOM decides; the grep only nominates.
//
// After: `#/triggers` → Week and `#/skills` → Browse both report axe 0 and zero unnamed controls at
// both themes.

// ── The census that keeps it closed ─────────────────────────────────────────────────────────────

/** Every `<Button>` in the tree whose children are ONLY a self-closing icon element. Walks the tag
 *  with brace depth, because `onClick={() => …}` contains a `>` and a `[^>]*` group stops there —
 *  the mistake that reported 1 of the 3 real sites. */
function iconOnlyButtons(): string[] {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })
  const out: string[] = []
  for (const abs of walk(SRC)) {
    const lines = readFileSync(abs, 'utf8').split('\n')
    lines.forEach((line, i) => {
      if (!line.includes('<Button')) return
      const blob = lines.slice(i, i + 6).join('\n')
      const start = blob.indexOf('<Button')
      let depth = 0
      let end = -1
      for (let k = start; k < blob.length; k++) {
        const c = blob[k]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) { end = k; break }
      }
      if (end === -1 || blob[end - 1] === '/') return
      const attrs = blob.slice(start + 7, end)
      const close = blob.indexOf('</Button>', end)
      if (close === -1) return
      const inner = blob.slice(end + 1, close).replace(/\s+/g, ' ').trim()
      if (/ariaLabel|aria-label|title=/.test(attrs)) return
      if (/^<[A-Z]\w+[^>]*\/>$/.test(inner)) out.push(`${abs.slice(SRC.length + 1)}:${i + 1} — ${inner}`)
    })
  }
  return out
}

describe('a Button whose whole body is an icon carries a name', () => {
  it('none is left unnamed anywhere in the tree', () => {
    const offenders = iconOnlyButtons()
    expect(offenders, `these announce as bare "button":\n${offenders.join('\n')}`).toEqual([])
  })

  it('the scan is not vacuous — it still finds the shape when the name is removed', () => {
    // Guards the brace-depth walk itself: if the matcher regresses to `[^>]*`, this synthetic case
    // (an arrow-function handler, like 2 of the 3 real sites) stops being found.
    const blob = '<Button size="sm" onClick={() => go()}><Trash2 size={14} /></Button>'
    const start = blob.indexOf('<Button')
    let depth = 0
    let end = -1
    for (let k = start; k < blob.length; k++) {
      const c = blob[k]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { end = k; break }
    }
    expect(end, 'the walk must pass the > inside the arrow function').toBeGreaterThan(blob.indexOf('go()'))
  })

  it('the three named sites keep their names', () => {
    const read = (rel: string) => readFileSync(join(process.cwd(), 'src', rel), 'utf8')
    expect(read('pages/knowledge/KnowledgeListPage.tsx'), 'the destructive one, through the shared cap')
      .toMatch(/ariaLabel=\{`Delete intent: \$\{rowSubject\(\[it\.goal \|\| it\.id\], 40\)\}`\}/)
    expect(read('pages/settings/MemoryPanel.tsx')).toMatch(/ariaLabel="Reload the audit log"/)
    expect(read('pages/projects/ProjectsSection.tsx')).toMatch(/ariaLabel="Save the project name"/)
  })

  it('Button can carry a name at all, and documents it', () => {
    // The prop is the fix; without the forward, every call site above is inert.
    expect(readFileSync(join(process.cwd(), 'src/ui/Button.tsx'), 'utf8')).toMatch(/aria-label=\{ariaLabel\}/)
    expect(readFileSync(join(process.cwd(), 'src/ui/Button.doc.ts'), 'utf8')).toMatch(/name: 'ariaLabel'/)
  })
})

describe('a hyphenated aria prop on a kit component is a dropped name', () => {
  // TypeScript checks `ariaLabel` and ignores `aria-label`, so this class of mistake compiles, ships,
  // and is invisible until something reads the accessibility tree. These are the kit components that
  // declare camelCase aria props, i.e. the ones where the hyphenated form is silently inert.
  const KIT = ['Button', 'TileButton', 'Segmented', 'HeaderSegmented', 'QuietButton', 'TextInput',
    'TextArea', 'SearchField', 'Slider', 'HeaderControl', 'IconButton']

  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  it('no call site passes one', () => {
    const SRC = join(process.cwd(), 'src')
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
        for (const c of KIT) {
          const m = new RegExp(`<${c}\\b([^>]*)`).exec(line)
          if (m && /\saria-[a-z]+=/.test(m[1])) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1} — <${c} ${/\s(aria-[a-z]+)=/.exec(m[1])?.[1]}>`)
        }
      })
    }
    expect(offenders, `a hyphenated aria prop here is dropped in silence:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the two week arrows carry the forwarded prop instead', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/triggers/WeekGridView.tsx'), 'utf8')
    expect(src).toMatch(/ariaLabel="Previous week"/)
    expect(src).toMatch(/ariaLabel="Next week"/)
    expect(src, 'and not the form the primitive ignores').not.toMatch(/<Button[^>]*aria-label=/)
  })

  it("the marketplace picker names itself", () => {
    // 🪤 The first version of this assertion was `<select value={marketplace}[^>]*aria-label=…` and it
    // FAILED on correct source — `[^>]*` stops at the `>` inside `onChange={(e) => …}`. The same trap
    // this whole cycle is about, in the test written to catch it. Anchor on the attribute instead.
    const src = readFileSync(join(process.cwd(), 'src/pages/skills/SkillsPage.tsx'), 'utf8')
    expect(src).toMatch(/setMarketplace\(e\.target\.value\)\} aria-label="Marketplace"/)
  })
})
