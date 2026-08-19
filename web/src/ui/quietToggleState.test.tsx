import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Pencil } from 'lucide-react'
import { QuietButton } from './QuietButton'
import { SquareIconButton } from './SquareIconButton'

// ── The last two state-bearing primitives that kept their state to themselves ─────────────────
//
// Cycle 129 taught `HeaderControl`, `FilterChip` and `IconButton` that `active` means `aria-pressed`.
// Finishing the family:
//
//   `SquareIconButton`  `on` (selected/toggled) → drove the coral tint only.  **2 callers pass it**
//   `QuietButton`       no state prop at all.   **6 of its call sites are disclosures**
//
// 🔑 `QuietButton` GETS `ariaExpanded`, THE NAME `Button` ALREADY USES — not a new spelling for the same
// question. Its six disclosure sites each swap their own label ("View"/"Hide", "Compare versions"/"Close
// compare"), which tells a user what the NEXT click does but not whether the panel is open right now.
//
// 🪤 A CALL-SITE CENSUS CANNOT SEE A FIX MADE IN A PRIMITIVE — the inverse of cycle 128's window bug. After
// cycle 129 the census still reported 34 silent toggles, six of which were already announced through
// `HeaderControl`/`FilterChip`. **Resolve the element before counting it silent**, or a primitive fix looks
// like no fix at all.
//
// Driven, parent worktree vs this one (`grep -c ariaExpanded QuietButton.tsx` = 0 there, 3 here):
//
//   #/settings/providers   `aria-pressed` nodes **0 → 18**   ← the `on` half, measured
//
// ⚠️ THE `QuietButton` HALF WAS **NOT DRIVEN** and it is worth saying so rather than implying six browser
// checks. Its six sites live behind interactions this dev home cannot reach: `WorkflowRunDetail`'s four
// panel toggles need a run whose panels render (the available runs are draft/failed/complete and none
// showed them), and the compare control needs the artifact viewer's version pane. Measured what I could —
// 8 QuietButtons render on the artifact detail and 34 on a run detail, none of them these six — and pinned
// the contract below with render tests instead of claiming a drive I did not do.

describe('QuietButton announces expansion when it is a disclosure', () => {
  it('emits the state when asked', () => {
    render(<QuietButton onClick={vi.fn()} ariaExpanded>View</QuietButton>)
    expect(screen.getByRole('button', { name: 'View' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('emits false when closed, so the state is unambiguous', () => {
    render(<QuietButton onClick={vi.fn()} ariaExpanded={false}>View</QuietButton>)
    expect(screen.getByRole('button', { name: 'View' }).getAttribute('aria-expanded')).toBe('false')
  })

  it('says nothing for a plain quiet action', () => {
    // Download / Source file are not disclosures; `aria-expanded="false"` there would be a false promise.
    render(<QuietButton onClick={vi.fn()}>Download</QuietButton>)
    expect(screen.getByRole('button', { name: 'Download' }).hasAttribute('aria-expanded')).toBe(false)
  })

  it('keeps its title and its quiet geometry', () => {
    render(<QuietButton onClick={vi.fn()} title="Download the findings log">Download</QuietButton>)
    const el = screen.getByRole('button', { name: 'Download' })
    expect(el.getAttribute('title')).toBe('Download the findings log')
    expect(el.className).toMatch(/h-7/)
  })
})

describe('SquareIconButton announces the tint it was already showing', () => {
  it('is pressed when on', () => {
    render(<SquareIconButton icon={Pencil} label="Edit" on onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Edit' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('omits the attribute for a plain icon action', () => {
    render(<SquareIconButton icon={Pencil} label="Delete" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete' }).hasAttribute('aria-pressed')).toBe(false)
  })

  it('keeps the disabled contract cycle 119 gave it', () => {
    render(<SquareIconButton icon={Pencil} label="Edit" disabled disabledReason="Test the connection first" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Edit' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    expect(el.getAttribute('title')).toBe('Edit — Test the connection first')
  })
})

describe('the call sites, classified per site', () => {
  const SRC = join(process.cwd(), 'src')
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  const DISCLOSURES: [string, string][] = [
    ['pages/ChatPage.tsx', 'open'],
    ['pages/artifacts/ArtifactViewer.tsx', 'comparing'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'workspaceOpen'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'outboxOpen'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'introspectOpen'],
    ['pages/workflows/WorkflowRunDetail.tsx', 'steerOpen'],
  ]

  for (const [rel, state] of DISCLOSURES) {
    it(`${rel.split('/').pop()} passes ariaExpanded={${state}}`, () => {
      expect(read(rel)).toContain(`ariaExpanded={${state}}`)
    })
  }

  // 🔁 SUPERSEDED 2026-08-19 (ux-717), deliberately and with the argument on the record — this
  // assertion used to read "the two `on` Edit buttons keep passing it".
  //
  // It pinned the MECHANISM (`on=`), not a ruling that those buttons reveal nothing. They do reveal:
  // `ModelBackends` renders `{editing && <EditInstanceForm/>}` and `MultiInstanceCard` renders
  // `{editing && props.length > 0 && (…)}`. By this file's own criterion — `aria-expanded` is a promise
  // that something is revealed, which is why `DiagnosticsPanel`'s mode toggles were REFUSED it — an Edit
  // that opens a form is a disclosure. So the pin moves to `ariaExpanded`, and the two remain pinned:
  // the point of the original assertion (these two must keep announcing SOMETHING) is preserved.
  it('the two Edit buttons announce expansion, because they open a form', () => {
    expect(read('pages/settings/ModelBackends.tsx'))
      .toMatch(/<SquareIconButton label="Edit"[^\n]*ariaExpanded=\{editing\}/)
    expect(read('pages/settings/MultiInstanceCard.tsx'))
      .toMatch(/ariaExpanded=\{editing && props\.length > 0\}/)
    // And neither may go back to claiming pressedness.
    for (const rel of ['pages/settings/ModelBackends.tsx', 'pages/settings/MultiInstanceCard.tsx']) {
      expect(read(rel), `${rel}: an Edit that opens a form is not a toggle`)
        .not.toMatch(/<SquareIconButton label="Edit"[^\n]*\bon=\{editing\}/)
    }
  })

  it('a show/hide-secret button stays silent, because its NAME carries the state', () => {
    // The same ruling as `DiagnosticsPanel`'s pause in cycle 128: when the accessible name flips
    // ("Show" ⇄ "Hide"), the state is already announced and a second channel adds nothing.
    for (const rel of ['pages/settings/ModelBackends.tsx', 'pages/settings/ProviderConfigForm.tsx']) {
      const src = read(rel)
      const at = src.search(/<SquareIconButton label=\{show(Secret)? \? 'Hide' : 'Show'\}/)
      expect(at, `${rel} must still have the name-flipping secret toggle`).toBeGreaterThan(-1)
      expect(src.slice(at, at + 200), 'a name that flips does not need `on` as well').not.toMatch(/\bon=\{/)
    }
  })
})

// ── 2026-08-19 (ux-717): `on` was the only question this primitive could ask, and 6 of 10 callers ──
// ── were asking a different one ────────────────────────────────────────────────────────────────────
//
// The cycle above gave `SquareIconButton` `on` → `aria-pressed` and measured `#/settings/providers`
// gaining 18 `aria-pressed` nodes. It classified `QuietButton`'s callers (six disclosures) but not this
// primitive's — and a census of all ten `on=` call sites says six of them REVEAL ADJACENT CONTENT:
//
//   DISCLOSURE  ProviderCard "Configure"            → {open && hasConfig && <ProviderConfigForm/>}
//   DISCLOSURE  ModelBackends "Manage/View models"  → {showModels && (…)}
//   DISCLOSURE  ModelBackends "Edit"                → {editing && <EditInstanceForm/>}
//   DISCLOSURE  MultiInstanceCard "Edit"            → {editing && props.length > 0 && (…)}
//   DISCLOSURE  WidgetFrame iteration rail          → {railOpen && !streaming && (…)}
//   DISCLOSURE  ChatPage "Organize chat"            → a Popover menu trigger
//   toggle      ChatPage "Pin chat"        (s.pinned)      — a state
//   toggle      ContentSurface "word wrap" (wrap)          — a mode
//   toggle      WidgetFrame Bookmark       (saved)         — a state
//   toggle      WidgetFrame Pin            (pinned)        — a state
//
// Measured on `#/settings/providers`, before → after: **aria-pressed 11 → 0, aria-expanded 0 → 15**, and
// **zero controls claiming both**. The Configure button reads `aria-expanded` false → true while its coral
// tint is unchanged (`rgb(154,155,156)` → `rgb(255,107,91)`), which is the point: the announcement was
// wrong, the appearance was right.
//
// 🔑 THIS IS NOT A REVERSAL OF THE `on` WORK, AND THE NUMBER MOVING IS THE EVIDENCE. `aria-pressed` on a
// disclosure is not a weaker claim, it is a different one — the same distinction this file already draws
// for `QuietButton`, and the one `disclosureAnnounced` enforces when it REFUSES `aria-expanded` to
// `DiagnosticsPanel`'s mode toggles. Four true toggles keep `on`.
//
// 🔑 `ariaExpanded`, NOT A THIRD SPELLING. `Button` and `QuietButton` already carry that prop name.
// Passing it SUPPRESSES `aria-pressed` in the primitive, because a control announcing both announces one
// of them falsely.

describe('SquareIconButton asks the right question of the right caller', () => {
  it('a disclosure announces expansion and NOT pressedness', () => {
    render(<SquareIconButton icon={Pencil} label="Configure" ariaExpanded onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Configure' })
    expect(el.getAttribute('aria-expanded')).toBe('true')
    expect(el.hasAttribute('aria-pressed'), 'both states is one state too many').toBe(false)
  })

  it('closed is announced too, so the state is never ambiguous', () => {
    render(<SquareIconButton icon={Pencil} label="Configure" ariaExpanded={false} onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Configure' }).getAttribute('aria-expanded')).toBe('false')
  })

  it('BOTH props at once: the disclosure wins and pressedness is suppressed', () => {
    // 🪤 THIS TEST EXISTS BECAUSE ITS ABSENCE WAS INVISIBLE. Mutating the primitive to
    // `aria-pressed={on}` (emitting both) left every other test green: no call site passes both, and the
    // single-prop render tests each exercise only one branch. A guard whose strictness is never
    // exercised is a guard that is only claimed — so the counter-example is synthetic, like the
    // two-spellings test in `disclosureAnnounced`.
    render(<SquareIconButton icon={Pencil} label="Both" on ariaExpanded onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Both' })
    expect(el.getAttribute('aria-expanded')).toBe('true')
    expect(el.hasAttribute('aria-pressed'), 'a control claiming both states claims one falsely').toBe(false)
  })

  it('a true toggle keeps aria-pressed, and claims no expansion', () => {
    render(<SquareIconButton icon={Pencil} label="Pin" on onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Pin' })
    expect(el.getAttribute('aria-pressed')).toBe('true')
    expect(el.hasAttribute('aria-expanded'), 'a pin reveals nothing').toBe(false)
  })

  it('the coral tint follows EITHER — the fix moves no pixels', () => {
    const { container: a } = render(<SquareIconButton icon={Pencil} label="A" on onClick={vi.fn()} />)
    const { container: b } = render(<SquareIconButton icon={Pencil} label="B" ariaExpanded onClick={vi.fn()} />)
    const cls = (c: HTMLElement) => c.querySelector('button')!.className.replace(/\s+/g, ' ')
    expect(cls(a)).toContain('text-primary')
    expect(cls(b), 'an expanded disclosure is lit exactly like a pressed toggle').toContain('text-primary')
    expect(a.querySelector('button')!.getAttribute('style'))
      .toBe(b.querySelector('button')!.getAttribute('style'))
  })

  it('a plain action claims neither', () => {
    render(<SquareIconButton icon={Pencil} label="Delete" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Delete' })
    expect(el.hasAttribute('aria-pressed')).toBe(false)
    expect(el.hasAttribute('aria-expanded')).toBe(false)
  })
})

describe('the SquareIconButton state family is classified, all ten of it', () => {
  const SRC = join(process.cwd(), 'src')
  const walkTsx = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkTsx(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  /** Brace-aware opening tags — `onClick={() => f(a)}` contains a `>`. */
  function stateBearing() {
    const out: { rel: string; kind: 'disclosure' | 'toggle' }[] = []
    for (const abs of walkTsx(SRC)) {
      const src = readFileSync(abs, 'utf8')
      for (const m of src.matchAll(/<SquareIconButton\b/g)) {
        let depth = 0, end = -1
        for (let i = m.index! + m[0].length; i < src.length; i++) {
          const c = src[i]
          if (c === '{') depth++
          else if (c === '}') depth--
          else if (c === '>' && depth === 0) { end = i; break }
        }
        if (end < 0) continue
        const tag = src.slice(m.index!, end + 1)
        const on = /\bon=\{/.test(tag), ex = /ariaExpanded=/.test(tag)
        if (!on && !ex) continue
        expect(on && ex, `${abs}: a control may not claim both states`).toBe(false)
        out.push({ rel: abs.slice(SRC.length + 1), kind: ex ? 'disclosure' : 'toggle' })
      }
    }
    return out
  }

  it('is 6 disclosures and 4 toggles — and nothing unclassified', () => {
    const all = stateBearing()
    expect(all.length, 'the state-bearing population').toBe(10)
    expect(all.filter((x) => x.kind === 'disclosure').length, 'disclosures').toBe(6)
    expect(all.filter((x) => x.kind === 'toggle').length, 'toggles').toBe(4)
  })

  it('the four toggles are the ones that reveal nothing', () => {
    // Named, so "finish the sweep" cannot convert a pin into a disclosure. Each is a STATE: pinned,
    // saved, word-wrap on. `aria-expanded` on any of them would promise content that does not exist.
    const toggles = stateBearing().filter((x) => x.kind === 'toggle').map((x) => x.rel).sort()
    expect(toggles).toEqual([
      'pages/ChatPage.tsx',
      'ui/content/ContentSurface.tsx',
      'ui/widget/WidgetFrame.tsx',
      'ui/widget/WidgetFrame.tsx',
    ])
  })

  it('every disclosure is bound to the flag that gates its content', () => {
    const pairs: [string, RegExp][] = [
      ['pages/settings/ProviderCard.tsx', /ariaExpanded=\{open\}/],
      ['pages/settings/ModelBackends.tsx', /ariaExpanded=\{showModels\}/],
      ['pages/settings/ModelBackends.tsx', /ariaExpanded=\{editing\}/],
      // 🪤 NOT merely `editing`: this card's form is gated on `editing && props.length > 0`, so with an
      // empty schema the button reveals nothing and must not claim otherwise.
      ['pages/settings/MultiInstanceCard.tsx', /ariaExpanded=\{editing && props\.length > 0\}/],
      ['ui/widget/WidgetFrame.tsx', /ariaExpanded=\{railOpen\}/],
      ['pages/ChatPage.tsx', /ariaExpanded=\{open\}/],
    ]
    for (const [rel, re] of pairs) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must bind ${re}`).toMatch(re)
    }
  })
})
