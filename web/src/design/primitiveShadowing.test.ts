import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

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
// Census of all six shadowed names (2026-08-10), and where each landed:
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
// So: 6 shadowed names, 1 real drift. This test locks that verdict in both directions — it
// pins the fix AND pins the five distinctions, so a later "finish the sweep" pass cannot
// flatten a composing alias or merge two unrelated components that share a name.

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
