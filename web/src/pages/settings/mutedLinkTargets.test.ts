import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The last three under-24 controls in settings, and the sibling that settled the question ──
//
// Cycle 126 left these three deferred as an OWNER CALL: they are hand-rolled MUTED buttons, and
// `TextLink` renders coral, so converging them looked like a colour ruling. **The code answered it — and
// answered it differently for each half.**
//
// 🔑 `#/settings/voice` ALREADY SHIPS THE ANSWER TWO LINES UP. The same row renders a `TextLink` for the
// same job (navigate to another settings sub-view, `size="xs"`, trailing `ArrowRight`). Measured on the
// parent worktree:
//
//   "Bind the STT model in Models"          181.13×**26.00**   coral  rgb(255,107,91)   ← the primitive
//   "Add or download models in Providers"   224.73×**18.00**   muted  rgb(154,155,156)  ← its hand-rolled twin
//
// Two links, one row, same job, one compliant and one not. That is drift with a local precedent, not a
// taste call — so this half is a CONVERGENCE, and the coral is the row's own established colour.
//
// 🔑 `#/settings/design` IS NOT THE SAME SHAPE. `Edit colors & save a custom theme` is a DISCLOSURE — it
// toggles the colour editor below with a rotating chevron — so making it a coral `TextLink` would be
// wrong twice over: coral means "primary action / alive" here, and a quiet expander is neither a
// navigation nor primary. It gets the geometry and keeps its tone. **The same measurement can have two
// right answers; what decides is what the control DOES.**
//
// Driven, parent worktree vs this one (`grep -c 'Add or download models in Providers <ArrowRight'` = 1
// there, 0 here):
//
//                                      before            after
//   voice link ×2                       224.73×**18.00**  224.73×**26.00**  (now coral, like its sibling)
//   voice row height                    18.00             **18.00**   ← unchanged: the primitive's `-my-1`
//   design disclosure                   257.05×**19.50**  257.05×**27.50**
//   design disclosure margin box        19.50             **19.50**   ← unchanged
//   re-swept #/settings, /design, /voice   7 real          **0 real**
//
// Evidence: the voice crop moves **3.38% dark / 3.44% light** in a 223×10 box — that is the second link's
// text recolouring, nothing else. The design page capture is **0% at both themes with the control in
// frame** (its top is y≈764 in a 900px viewport, checked rather than assumed per cycle 125), because
// `py-1 -my-1` grows the border box and returns every pixel to the layout.

const SRC = join(process.cwd(), 'src')
const voice = readFileSync(join(SRC, 'pages/settings/VoicePanel.tsx'), 'utf8')
const design = readFileSync(join(SRC, 'pages/settings/DesignPanel.tsx'), 'utf8')
const codeOf = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the voice row converges on the link it already had', () => {
  it('the Providers link is a TextLink now', () => {
    // 🔑 RE-POINTED, NOT RELAXED (cycle 615). This asserted the prop string up to its closing `>`, so
    // adding the `ink` prop the canvas ground requires broke a match whose INTENT — the hand-rolled
    // twin became the primitive, with the same job/size/icon — was untouched. It now pins each prop
    // independently AND the ink, so it checks strictly more than the literal ever did.
    const code = codeOf(voice)
    const link = /<TextLink onClick=\{\(\) => go\('providers'\)\}[^>]*>/.exec(code)?.[0] ?? ''
    expect(link, 'the Providers link is a TextLink').toBeTruthy()
    for (const prop of ['icon={ArrowRight}', 'iconPosition="trailing"', 'size="xs"', 'ink="emphasis"'])
      expect(link, `carries ${prop}`).toContain(prop)
  })

  it('no hand-rolled muted twin remains', () => {
    const code = codeOf(voice)
    expect(code, 'the 18px hand-rolled button must be gone')
      .not.toMatch(/inline-flex items-center gap-1 text-\[0\.75rem\] text-on-surface-low hover:text-on-surface hover:underline/)
  })

  it('both links in the row now use the same primitive with the same props', () => {
    // The coherence claim, asserted rather than described: two `TextLink`s, both `xs`, both trailing arrow.
    const code = codeOf(voice)
    // 🪤 FOURTH TIME THIS SESSION: a matcher that scans to the first `>` stops inside
    // `onClick={() => go('providers')}` and matches nothing. Scan to the CLOSING TAG instead — that
    // string cannot occur inside an arrow function.
    const links = [...code.matchAll(/<TextLink[\s\S]{0,300}?<\/TextLink>/g)]
      .filter((m) => /icon=\{ArrowRight\} iconPosition="trailing" size="xs"/.test(m[0]))
    expect(links.length, 'the Models link and the Providers link').toBe(2)
  })
})

describe('the design disclosure keeps its tone and takes the geometry', () => {
  it('grew by padding that is handed straight back', () => {
    expect(codeOf(design)).toMatch(/className="flex items-center gap-s py-1 -my-1 text-on-surface-var text-\[0\.8125rem\]"/)
  })

  it('did NOT become a coral TextLink', () => {
    // Pinned deliberately: a future convergence sweep would "finish the job" and make an expander read as
    // the page's primary action.
    const code = codeOf(design)
    const at = code.indexOf('Edit colors')
    const around = code.slice(Math.max(0, at - 400), at)
    expect(around, 'a disclosure is not a navigation').not.toMatch(/<TextLink/)
    expect(around).toMatch(/text-on-surface-var/)
  })

  it('is still a disclosure — the rotating chevron and the toggle both survive', () => {
    const code = codeOf(design)
    expect(code).toMatch(/setEditingColors\(\(v\) => !v\)/)
    expect(code).toMatch(/rotate-180/)
  })
})
