import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, sep } from 'node:path'

const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
  const p = join(d, n)
  if (statSync(p).isDirectory()) return walk(p)
  return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
})

// ── Names that shadow a real ui/ primitive ────────────────────────────────────
//
// A local component sharing a name with a shell primitive is one of two things, and the
// difference is the whole judgement:
//
//   COMPOSES the primitive  → fine. A named alias that pins a fixed prop combination is
//                             clearer at the call site than repeating it, and it cannot drift
//                             because the primitive still renders it.
//   REIMPLEMENTS it         → drift. The call site silently loses whatever the primitive
//                             carries (focus/press/hover behaviour, weight, gap, a11y).
//
// 🔴 THE 2026-08-10 CENSUS BELOW WAS HAND-APPLIED AND HAND-KEPT, AND IT LISTED SIX OF EIGHT SITES.
// It concluded "6 shadowed names, 1 real drift". Re-derived mechanically (see the last describe) with
// the rule this file already states — a page file defines a name a `ui/` module EXPORTS — two more
// sites come back, and they failed to be listed for two DIFFERENT reasons:
//
//   FilterMenu (artifacts/ArtifactsSection)  DRIFT, and simply never noticed. A hand-rolled
//     source/collection dropdown beside a sort Segmented — the exact arrangement `ui/FilterMenu`'s
//     own doc says it replaced — while eight other list surfaces rendered the primitive. Converged;
//     that surface gained the active-count badge and inline Clear it never had.
//     🔑 Its local name was `FilterMenu` too, so a grep for the primitive's call sites returned the
//     shadow as a hit. A shadow that shares its primitive's name hides from the search you would use.
//
//   Field (projects/ProjectsSection)         ALREADY JUDGED — as an OPEN OWNER TASTE CALL, but in a
//     different file. `design/rawFormControls.test.tsx` pins that local Field and records the reason:
//     its hint-above-in-sentence-case layout is the owner's to rule on, and swapping it for the shared
//     Field moves 27.9% of the modal's pixels. This cycle converged it, measured the result, and
//     REVERTED — the pin did its job.
//     🔑 So the census was not merely incomplete; its ledger was SPLIT across two files, which is why
//     it could read as complete. The derived list below is keyed by SITE for the same reason: `Field`
//     already carried a verdict (settingsUI's divided settings row), and a name-keyed census lets a
//     second site inherit an unrelated verdict and disappear.
//
// Real tally: 8 shadow sites — 2 fixed drifts, 4 distinctions, 1 composing alias, 1 open taste call.
//
// Census of the six sites found by hand (2026-08-10), and where each landed:
//
//   Button   (settings/UpdatesPanel)          DRIFT — reimplemented. Fixed; see below.
//   Toggle   (tools/ToolsPage)                DISTINCTION — a 3-line alias that renders
//     `<SharedToggle readOnly decorative size="sm" />`. It exists so three tool rows can nest a
//     display-only switch inside a wrapping `<button aria-label>` without a nested interactive
//     or a second unnamed switch in the a11y tree. It COMPOSES the primitive.
//   ContextMenu (files/browse/FileTree)       DISTINCTION — different contract, like ProposalRow.
//     ui/motion/ContextMenu wraps a child and opens at the POINTER on contextmenu/long-press;
//     FileTree's takes explicit {x,y} because the file row opens it from a "⋯" button at a
//     computed anchor. Both clamp to the viewport (the local one says it mirrors the shared
//     clamp, and bug #32 is referenced in the shared one). Converging them means giving the
//     shared component an imperative open-at-coords mode — a new abstraction with one adopter.
//   Spark    (dashboard/widgets/SystemHealth) DISTINCTION — pure name coincidence. ui/Spark is
//     the BRAND MARK (the claw, scheme-gradient painted); SystemHealth's is an SVG SPARKLINE
//     over a sample buffer. Nothing shared but five letters.
//   Markdown (ui/content/registerBuiltins)    DISTINCTION — a `lazy()` alias for the
//     MarkdownPreview renderer chunk, local to the registry's naming scheme. Not a component.
//   MonacoEditor (2 files)                    DISTINCTION — both are literally
//     `lazy(() => import('@monaco-editor/react'))`. The "duplicate" is two lazy handles on the
//     same third-party module, which is how code-splitting works; sharing one handle across
//     two routes would defeat the split.
//
// This test locks those verdicts in both directions — it pins the fix AND pins the distinctions,
// so a later "finish the sweep" pass cannot flatten a composing alias or merge two unrelated
// components that share a name.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('UpdatesPanel uses the shared Button (was a reimplementation)', () => {
  const src = read('pages/settings/UpdatesPanel.tsx')

  it('declares no local Button', () => {
    // The removed copy was a raw <button> with hand-written geometry. It matched the shared
    // primitive on height/radius/background — so it LOOKED converged — while dropping the
    // hover-lift, spring press-in, sheen and the 470 weight. Measured on screen before the
    // fix: weight 400 vs 450 and gap 6px vs 8px against the sibling AuditPanel buttons, which
    // sit two settings pages away in the same tree.
    expect(/function Button\b/.test(src), 'UpdatesPanel should not declare its own Button').toBe(false)
  })

  it('imports the shared Button', () => {
    expect(src).toMatch(/import \{ Button \} from '\.\.\/\.\.\/ui\/Button'/)
  })

  it('uses the primitive\'s own loading prop rather than an inline spinner', () => {
    // The deleted copy's justification comment claimed the shared Button could not do this
    // ("these need a busy spinner inline"). That was STALE: ui/Button has `loading`, which
    // cross-fades the label under a centered spinner while preserving width. The local
    // version swapped the label out entirely, so the button changed width mid-request.
    expect(src).toMatch(/loading=\{checking\}/)
    expect(src).toMatch(/loading=\{applying\}/)
    expect(/Loader2/.test(src), 'the orphaned Loader2 import should be gone').toBe(false)
  })
})

describe('the five shadowed names that are NOT drift', () => {
  it('ToolsPage Toggle composes the shared Toggle rather than reimplementing it', () => {
    const src = read('pages/tools/ToolsPage.tsx')
    // If this ever stops rendering SharedToggle, the alias became a real second switch.
    expect(src).toMatch(/<SharedToggle[^>]*readOnly[^>]*decorative/)
  })

  it('FileTree ContextMenu takes explicit coords; the shared one wraps a child', () => {
    // The signatures are the evidence: {x,y,items,onClose} vs {items,children,disabled}.
    expect(read('pages/files/browse/FileTree.tsx')).toMatch(/function ContextMenu\(\{ x, y, items, onClose \}/)
    expect(read('ui/motion/ContextMenu.tsx')).toMatch(/export function ContextMenu\(\{ items, children, disabled \}/)
  })

  it('the two Sparks are unrelated: a brand mark and a sparkline', () => {
    expect(read('ui/Spark.tsx')).toMatch(/<ClawMark/)
    expect(read('pages/dashboard/widgets/SystemHealth.tsx')).toMatch(/function Spark\(\{ samples/)
  })

  it('the MonacoEditor pair are two lazy handles on the same third-party module', () => {
    const lazyMonaco = /lazy\(\(\) => import\('@monaco-editor\/react'\)\)/
    expect(read('ui/content/ContentSurface.tsx')).toMatch(lazyMonaco)
    expect(read('pages/knowledge/GistEditor.tsx')).toMatch(lazyMonaco)
  })
})

describe('the census is DERIVED, so a ninth shadow cannot arrive unnoticed', () => {
  // The rule this file already states in prose, executed: a page file defines a name that a `ui/`
  // module exports. Narrow on purpose — it is the highest-signal form of shadowing, and the one
  // whose verdict actually matters, because the call site silently loses whatever the primitive
  // carries. (Two local helpers that merely share a name with each other, `Row` or `Section` in two
  // page files, are not this: neither is a shell primitive, so there is nothing to lose.)
  const DEF = /(?:^|\n)(?:export )?(?:function ([A-Z]\w+)\(|const ([A-Z]\w+)(?:: [^=]+)? = (?:\(|lazy\(|memo\(|forwardRef))/g
  const EXPORTED = /(?:^|\n)export (?:function ([A-Z]\w+)\(|const ([A-Z]\w+)(?:: [^=]+)? = (?:\(|lazy\(|memo\(|forwardRef))/g

  const files = walk(SRC)
  const namesIn = (subset: string[], re: RegExp) => {
    const found = new Map<string, string[]>()
    for (const abs of subset) {
      for (const m of readFileSync(abs, 'utf8').matchAll(re)) {
        const n = m[1] ?? m[2]
        if (!found.has(n)) found.set(n, [])
        found.get(n)!.push(abs.slice(SRC.length + 1))
      }
    }
    return found
  }
  const uiExports = namesIn(files.filter((f) => f.includes(`${sep}ui${sep}`)), EXPORTED)
  const pageDefs = namesIn(files.filter((f) => !f.includes(`${sep}ui${sep}`)), DEF)

  /** Every shadow SITE, with its verdict. Keyed by `Name @ file`, not by name — keying by name is
   *  precisely how the hand census under-counted: `Field` was judged once (settingsUI's settings row)
   *  and a SECOND site under the same name stayed invisible behind that verdict.
   *
   *  `fixed` means the shadow is GONE, so the site must NOT reappear — that is the assertion which
   *  keeps a converged shadow from quietly coming back. */
  const VERDICTS: Record<string, 'composes' | 'distinct' | 'fixed' | 'owner-taste-call'> = {
    'Button @ pages/settings/UpdatesPanel.tsx': 'fixed',
    'FilterMenu @ pages/artifacts/ArtifactsSection.tsx': 'fixed',
    'Toggle @ pages/tools/ToolsPage.tsx': 'composes',
    'ContextMenu @ pages/files/browse/FileTree.tsx': 'distinct',
    'Spark @ pages/dashboard/widgets/SystemHealth.tsx': 'distinct',
    'Field @ pages/settings/settingsUI.tsx': 'distinct',
    // 🔴 OPEN OWNER TASTE CALL — do not converge. `design/rawFormControls.test.tsx` pins this local
    // Field and records why: its hint-above-in-sentence-case layout is the owner's to rule on, and
    // swapping it for the shared Field moves 27.9% of the modal's pixels. Re-measured this cycle:
    // 13px / none / `wght 600` / on-surface → 12px / uppercase / 0.3px tracking / on-surface-low.
    // That verdict living in a DIFFERENT file from this census is the reason the census read as
    // complete while two sites went unlisted.
    'Field @ pages/projects/ProjectsSection.tsx': 'owner-taste-call',
  }

  it('the derived shadow list holds no unjudged name', () => {
    const shadows = [...pageDefs.keys()].filter((n) => uiExports.has(n)).sort()
    // Vacuity floors: if either matcher stops resolving, every assertion below passes on nothing.
    expect(uiExports.size, 'the ui/ export scan must resolve').toBeGreaterThan(100)
    expect(shadows.length, 'and some shadows must still be found').toBeGreaterThan(0)

    // One entry per SITE, so a second file under an already-judged name cannot inherit its verdict.
    const sites = shadows.flatMap((n) => pageDefs.get(n)!.map((f) => `${n} @ ${f}`))
    const unjudged = sites.filter((k) => !VERDICTS[k])
    expect(unjudged, `judge these before shipping — compose the primitive, or record why this site is distinct:\n${unjudged.join('\n')}`)
      .toEqual([])
  })

  it('a site recorded as fixed has not come back', () => {
    const live = new Set([...pageDefs.keys()].filter((n) => uiExports.has(n))
      .flatMap((n) => pageDefs.get(n)!.map((f) => `${n} @ ${f}`)))
    const regressed = Object.entries(VERDICTS).filter(([k, v]) => v === 'fixed' && live.has(k)).map(([k]) => k)
    expect(regressed, `these were converged and are shadowed again:\n${regressed.join('\n')}`).toEqual([])
  })

  it('the open taste call is still pinned by the file that owns it', () => {
    // 🪤 This cycle converged that Field, measured the result, and reverted — because the decision is
    // already recorded as the owner's. A census that only listed names could not tell me that; the
    // verdict was in another file. Keeping BOTH pins means the next pass finds it in either place.
    const raw = read('design/rawFormControls.test.tsx')
    expect(raw, "the taste call's own pin must still exist").toMatch(/the local Field is a kept layout, not drift/)
    expect(read('pages/projects/ProjectsSection.tsx'), 'and the layout it pins is still there')
      .toMatch(/function Field\(\{ label, hint, children \}/)
  })

  it("ProjectsSection's kept Field still honours the contract it is allowed to keep", () => {
    // The layout is the owner's call; publishing a label id its children can claim is NOT. A local
    // wrapper is only tolerable while it keeps that contract, so assert the contract, not the style.
    const src = read('pages/projects/ProjectsSection.tsx')
    expect(src).toMatch(/import \{[^}]*\bFieldLabelProvider\b[^}]*\} from '\.\.\/\.\.\/ui\/forms'/)
    expect(src).toMatch(/<FieldLabelProvider value=\{labelId\}>/)
  })

  it("settingsUI's Field is a settings ROW, which is why it stays", () => {
    const src = read('pages/settings/settingsUI.tsx')
    expect(src, 'the divided-row shape is the distinction').toMatch(/border-b border-outline-variant\/30 py-3 last:border-0/)
  })
})
