import { describe, it, expect, vi } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Plus, Brain } from 'lucide-react'
import { EmptyState } from '../ui/ListScaffold'

// ── ONE empty-state primitive, rolled out to the seven surfaces (OU-6) ──────────────────
//
// 🔑 THE DECISION THIS FILE PINS. OU-6 is written as "web/src/ui/EmptyState.tsx exists", which
// reads as a greenfield primitive. It is not one: `EmptyState` has existed since long before
// this atom, exported from the LIST KIT (`ui/ListScaffold.tsx`) beside its two siblings
// `LoadError` and `ListSkeleton`, and it is already the answer at ~30 call sites. Creating a
// second component at `ui/EmptyState.tsx` would be a dual path — two components answering one
// condition — which this repo forbids, and it would contradict an existing rail that pins the
// co-location ON PURPOSE:
//
//   loadErrorState.test.tsx › "the primitive is exported from the list kit, beside EmptyState"
//     "Co-located on purpose: the two are alternative answers to the same condition, and a
//      surface reaching for one should see the other."
//
// So the atom's FILENAME clause is satisfied by not honouring it, and its PRODUCT clause —
// every one of the seven surfaces explains itself and offers one working action — is what this
// file holds. Recorded as a DEVIATION in the plan's execution log.
//
// The two other empty-shaped components are NOT dual paths; each answers a different condition
// and says so in its own doc:
//   · `PresetEmptyState` (ui/PresetEmptyState.tsx) — the preset-first ON-RAMP for a surface
//     whose create flow front-loads its whole ontology. Offers finished examples that SEED the
//     existing form. Its doc names the distinction: "Distinct from EmptyState (ListScaffold),
//     which states a fact and offers one CTA." Triggers uses it, which is why Triggers is
//     allowed to satisfy the rollout through that primitive instead.
//   · `SlotEmptyState` (pages/dashboard/widgets/kit.tsx) — an inline one-line dashed strip
//     sized for a DASHBOARD WIDGET SLOT, not a page or panel body.
//
// ── AND: the honesty precondition ──────────────────────────────────────────────────────
//
// An empty state that renders when the data merely FAILED TO LOAD is not an empty state, it is
// a confident wrong answer. Auditing the seven for this atom found `#/loops` doing exactly
// that (`.catch(() => [] as GoalLoop[])` in its fetcher → "No loops yet — Start a loop" on a
// 500). That fix joined the existing family rail rather than growing a second one; see
// `ui/loadErrorState.test.tsx`'s ADOPTERS entry for `pages/loops/LoopsListPage.tsx`.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** Every `action={…}` JSX attribute in a file, as the expression BETWEEN its braces —
 *  brace-matched from the opening `{` to its partner.
 *
 *  ⚠️ Written this way after the rail rejected a correct surface. The first version matched
 *  `action={{ … onClick`, i.e. only the object-literal shape, and failed `#/skills` — whose
 *  `action={!q ? { label: 'Browse skills', onClick: onBrowse, … } : undefined}` is the
 *  CONDITIONAL shape, and is the better one: it encodes "no CTA while filtered" in the prop
 *  itself. The rail had encoded an accident of whichever call site was written first. Matching
 *  the whole expression admits both shapes and cannot be padded by a comment or a long label. */
function actionExprs(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/\baction=\{/g)) {
    const start = (m.index ?? 0) + m[0].length
    let i = start
    let depth = 1
    while (i < src.length && depth > 0) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      i++
    }
    out.push(src.slice(start, i - 1))
  }
  return out
}

/** The seven surfaces OU-6 names, each with the file that owns its empty branch.
 *
 *  Two of the seven are not what the plan's prose implies, and the names are kept honest here
 *  rather than quietly re-scoped:
 *    · "Loops" has no nav tile — loops launch from within Projects — but `#/loops` is routable
 *      and `LoopsListPage` is the list a user lands on.
 *    · "Memory" is not a page at all. It is `#/settings/memory`, a multi-tab PANEL, so its
 *      empty branches live in `settings/MemoryPanel.tsx`. `EmptyState`'s own doc covers this:
 *      "The uniform empty state for a list/PANEL". */
const SURFACES: { name: string; file: string; primitive: 'EmptyState' | 'PresetEmptyState' }[] = [
  { name: 'Loops', file: 'pages/loops/LoopsListPage.tsx', primitive: 'EmptyState' },
  { name: 'Workflows', file: 'pages/workflows/WorkflowsListPage.tsx', primitive: 'EmptyState' },
  { name: 'Knowledge', file: 'pages/knowledge/KnowledgeListPage.tsx', primitive: 'EmptyState' },
  { name: 'Memory', file: 'pages/settings/MemoryPanel.tsx', primitive: 'EmptyState' },
  { name: 'Skills', file: 'pages/skills/SkillsPage.tsx', primitive: 'EmptyState' },
  { name: 'Tasks', file: 'pages/tasks/TasksListPage.tsx', primitive: 'EmptyState' },
  { name: 'Triggers', file: 'pages/triggers/TriggersListPage.tsx', primitive: 'PresetEmptyState' },
]

describe('exactly one general empty-state primitive', () => {
  it('does NOT ship a second one at ui/EmptyState.tsx', () => {
    // The dual path this atom could most easily have created. If a future change genuinely wants
    // the primitive in its own file, that is a MOVE — delete it from the kit in the same commit
    // and update `loadErrorState.test.tsx`'s co-location rail — never an addition beside it.
    expect(
      existsSync(join(SRC, 'ui/EmptyState.tsx')),
      'EmptyState lives in the list kit (ui/ListScaffold.tsx); a second file beside it would be two components for one condition',
    ).toBe(false)
  })

  it('exports it from the list kit, with its siblings', () => {
    const kit = read('ui/ListScaffold.tsx')
    expect(kit).toMatch(/export function EmptyState\b/)
    // The siblings that make the empty/failed/loading distinction expressible at all.
    expect(kit).toMatch(/export function LoadError\b/)
    expect(kit).toMatch(/export function ListSkeleton\b/)
  })

  it('keeps the preset on-ramp a documented SIBLING, not a rival', () => {
    // PresetEmptyState may coexist only while it declares what makes it different. Strip that
    // sentence from its doc and the two become indistinguishable — which is the moment they are
    // a dual path and one of them should be deleted.
    const doc = read('ui/PresetEmptyState.doc.ts')
    expect(doc, 'the on-ramp must state how it differs from EmptyState').toMatch(
      /Distinct from EmptyState/,
    )
  })

  it('documents the primitive where uiDocs.drift looks', () => {
    // `EmptyState` is documented in the KIT's doc file because that is where the component is.
    // A rename/extraction that forgot this would ship an undocumented primitive.
    const doc = read('ui/ListScaffold.doc.ts')
    expect(doc).toMatch(/name: 'EmptyState'/)
  })
})

describe('the seven OU-6 surfaces route their empty case through the primitive', () => {
  for (const { name, file, primitive } of SURFACES) {
    it(`${name} (${file}) renders <${primitive}>`, () => {
      const src = read(file)
      expect(src, `${name} must import the shared primitive from the kit`).toMatch(
        primitive === 'EmptyState'
          ? /import \{[^}]*\bEmptyState\b[^}]*\} from '[^']*ListScaffold'/
          : /import \{[^}]*\bPresetEmptyState\b[^}]*\} from '[^']*PresetEmptyState'/,
      )
      expect(src, `${name} must actually render it`).toMatch(new RegExp(`<${primitive}\\b`))
    })

    it(`${name} offers a working action on the genuinely-empty case`, () => {
      const src = read(file)
      // "One seeded working action each" is the atom's product clause. For six surfaces that is
      // an `action={{ … onClick … }}` on an EmptyState; for Triggers it is the preset grid,
      // whose cards ARE the actions (each seeds the create flow with a prefill).
      if (primitive === 'PresetEmptyState') {
        expect(src, 'the preset grid must be fed a catalog and hand picks back').toMatch(/presets=\{/)
        expect(src).toMatch(/onPick=\{/)
      } else {
        const wired = actionExprs(src).filter((e) => /onClick/.test(e))
        expect(
          wired.length,
          `the empty state on ${name} must offer a next step, not just state a fact`,
        ).toBeGreaterThan(0)
      }
    })
  }

  it('scans the real files (not vacuously green)', () => {
    // Every assertion above is a regex over a file read by relative path. A typo'd path, a moved
    // page, or a renamed primitive would make `read` throw — but a SHRUNKEN list would silently
    // assert less while still reporting green, which is the failure this floor catches.
    expect(SURFACES).toHaveLength(7)
    for (const { file } of SURFACES) {
      expect(existsSync(join(SRC, file)), `${file} must exist to be scanned`).toBe(true)
      expect(read(file).length, `${file} must be non-trivial`).toBeGreaterThan(500)
    }
  })
})

describe('an empty state is a launchpad, not a dead end', () => {
  // Behaviour, not source: the properties the seven inherit BY using the primitive. If these
  // regress in the kit, all seven regress at once, which is the point of there being one.
  it('names itself with a heading, so the surface is not silently blank', () => {
    render(<EmptyState icon={Brain} title="No memories yet" hint="Add one, or let a chat record them." />)
    expect(screen.getByRole('heading', { name: 'No memories yet' })).toBeInTheDocument()
    expect(screen.getByText('Add one, or let a chat record them.')).toBeInTheDocument()
  })

  it('exposes the action as a real keyboard-reachable button', async () => {
    const onClick = vi.fn()
    render(<EmptyState icon={Brain} title="No memories yet" action={{ label: 'Add a fact', onClick, icon: Plus }} />)
    const btn = screen.getByRole('button', { name: /Add a fact/ })
    // Reachable by Tab and activatable from the keyboard — not a click-only div.
    await userEvent.tab()
    expect(btn).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('does not opt its action out of the app-wide focus ring', () => {
    // ⚠️ MECHANISM, not vibes. The first draft of this test asserted a `focus-visible:ring-*`
    // utility on the button and failed — correctly. `Button` carries no such utility, because
    // this app's keyboard ring is a GLOBAL rule in `design/tokens.css`:
    //     :focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px }
    // written precisely so controls do not each need one. jsdom applies no stylesheet, so the
    // painted ring is not observable here at all; what IS observable — and what actually
    // regresses — is a control opting OUT with `outline-none` and supplying no replacement,
    // which is the exact failure the global rule's own comment says it exists to prevent.
    const ring = read('design/tokens.css')
    expect(ring, 'the app-wide keyboard ring must exist').toMatch(
      /:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--color-primary\)/,
    )
    render(<EmptyState title="t" action={{ label: 'Go', onClick: () => {} }} />)
    const cls = (screen.getByRole('button', { name: 'Go' }).className || '').split(/\s+/)
    const optsOut = cls.some((c) => c === 'outline-none' || c === 'focus:outline-none')
    const replaces = cls.some((c) => c.startsWith('focus-visible:ring') || c.startsWith('focus-visible:outline'))
    expect(optsOut && !replaces, 'the CTA suppressed the global ring without replacing it').toBe(false)
  })

  it('is NOT a live region — "you have none" is a normal answer', () => {
    // The contrast with `LoadError`, which IS role=alert. An empty state that interrupted on
    // every render would announce absence as though it were bad news.
    const { container } = render(<EmptyState title="No loops yet" />)
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.querySelector('[role="status"]')).toBeNull()
  })

  it('renders no button at all when there is no next step', () => {
    // The filtered-to-nothing case relies on this: `action` omitted must mean no CTA, so a
    // narrowed list cannot accidentally pitch a create flow.
    const { container } = render(<EmptyState title="No matching loops" hint="Try another filter." />)
    expect(container.querySelector('button')).toBeNull()
  })
})
