import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── aria-modal is a PROMISE that focus is owned ─────────────────────────────────
//
// `aria-modal="true"` tells assistive tech that everything outside the dialog is unavailable. If Tab
// still reaches the page behind the scrim, the markup is lying — and a keyboard user tabs into
// controls they cannot see.
//
// Census of every dialog-role surface in web/src:
//
//   ui/Modal.tsx                    aria-modal + useFocusTrap        ✓
//   ui/dialog/DialogShell.tsx       aria-modal + useFocusTrap        ✓
//   ui/SpotlightTour.tsx            aria-modal + useFocusTrap        ✓ (added by ONBOARDING-UX OU-10)
//   ui/SnipOverlay.tsx              aria-modal + useFocusTrap        ✓ (added by CHAT-CRAFT CC-4)
//   ui/UpdateProgressOverlay.tsx    aria-modal, NO trap              ✗ fixed here
//   ui/DegradedChip.tsx             role=dialog, no aria-modal       distinction (a popover)
//   ui/NavRail.tsx                  role=dialog, no aria-modal       distinction (a drawer, `inert`)
//
// The two without `aria-modal` are deliberate: neither claims to own focus, and NavRail marks the
// page behind it `inert` instead. Only a surface that DECLARES aria-modal is held to the trap.
//
// Measured on the live DOM, driving a faked in-flight update through GET /api/status:
//
//     BEFORE   focus on open: BODY (never entered the dialog)
//              Tab escapes:   YES after 1 press → the nav's "Home" button, behind the scrim
//     AFTER    focus on open: BUTTON "Cancel", inDialog=true
//              Tab escapes:   no (trapped)
//
// The fix is a component SPLIT, not just a ref. `useFocusTrap` is a mount/unmount contract — its
// effect has a `[]` dep list and it captures the previously-focused element during its FIRST RENDER.
// Called in `UpdateProgressOverlay` (which mounts once in the app shell and renders nothing until an
// update starts) it would run at app boot, with a null ref. So the sheet became its own component
// that mounts with the dialog.
//
// One thing deliberately NOT treated as a defect: after close, focus lands on `<body>`. For Modal
// that would be wrong (a button opened it, so focus returns there — verified). This overlay appears
// UNPROMPTED from a WS event, so there is no trigger to return to. `useFocusTrap` already guards
// this: it only restores a `prevActive` that is still connected and outside the dialog.

const SRC = join(process.cwd(), 'src')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

describe('the update overlay honours the contract it declares', () => {
  const src = read('ui/UpdateProgressOverlay.tsx')

  it('the alertdialog carries the focus trap', () => {
    expect(src).toMatch(/import \{ useFocusTrap \} from '\.\/useFocusTrap'/)
    expect(src).toMatch(/ref=\{trapRef\} role="alertdialog" aria-modal="true"/)
  })

  it('the trap lives in a child that mounts WITH the dialog, not in the shell', () => {
    // The hook's effect is `[]` and its capture happens on first render, so calling it in the
    // always-mounted shell would run it at app boot against a null ref. This is the part a
    // "just add the ref" fix would get wrong while still looking correct.
    expect(src).toMatch(/function UpdateSheet\(\{ progress, cancel \}/)
    expect(src).toMatch(/const trapRef = useFocusTrap<HTMLDivElement>\(\)/)
    // The shell renders the sheet conditionally and does NOT call the hook itself.
    const shell = src.slice(src.indexOf('export function UpdateProgressOverlay()'), src.indexOf('function UpdateSheet'))
    expect(shell).toMatch(/\{progress && <UpdateSheet progress=\{progress\} cancel=\{cancel\} \/>\}/)
    expect(/useFocusTrap/.test(shell), 'the always-mounted shell must not call the hook').toBe(false)
  })
})

describe('the rail: aria-modal implies a focus trap', () => {
  const files = walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

  it('every aria-modal surface uses useFocusTrap', () => {
    const offenders = files
      .filter((f) => /aria-modal="true"/.test(f.src))
      .filter((f) => !/useFocusTrap/.test(f.src))
      .map((f) => f.rel)
    expect(
      offenders,
      `aria-modal="true" promises focus is owned; without a trap Tab reaches the page behind the ` +
        `scrim:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the aria-modal surfaces', () => {
    // Two cycles ago a rail matched NOTHING and reported a clean sweep, because
    // `expect(offenders).toEqual([])` cannot tell "nothing is broken" from "my matcher is broken".
    const modal = files.filter((f) => /aria-modal="true"/.test(f.src)).map((f) => f.rel).sort()
    expect(modal).toEqual([
      'ui/Modal.tsx',
      // The snip overlay (CHAT-CRAFT CC-4). It covers the page to take a region selection, so
      // it owes containment for the same reason as the rest of this list.
      'ui/SnipOverlay.tsx',
      // The product tour's step card (ONBOARDING-UX OU-10). It dims the page it sits over,
      // so it owes containment for exactly the reason this file exists — and it carries the
      // trap. It re-takes focus on every stop too, because it walks onto surfaces that
      // autofocus their own fields (Settings' search), which would otherwise leave the trap
      // holding nothing while the markup still claimed aria-modal.
      'ui/SpotlightTour.tsx',
      'ui/UpdateProgressOverlay.tsx',
      'ui/dialog/DialogShell.tsx',
    ])
    // And the check must still FLAG the shape: a surface declaring aria-modal with no trap.
    const sample = { rel: 'x.tsx', src: '<div role="dialog" aria-modal="true" />' }
    expect(/aria-modal="true"/.test(sample.src) && !/useFocusTrap/.test(sample.src)).toBe(true)
  })

  it('the two non-modal dialog roles are a recorded distinction', () => {
    // A popover and a drawer: neither CLAIMS to own focus, so neither owes a trap. Pinned so a
    // later pass does not "finish the sweep" by adding aria-modal to them — that would create the
    // very defect this rail guards, and NavRail already marks the page behind it `inert`.
    for (const rel of ['ui/DegradedChip.tsx', 'ui/NavRail.tsx']) {
      const src = read(rel)
      expect(src, `${rel} should still be a dialog role`).toMatch(/role="dialog"/)
      expect(/aria-modal/.test(src), `${rel} must NOT claim aria-modal`).toBe(false)
    }
    expect(read('ui/NavRail.tsx')).toMatch(/inert/)
  })
})

// ── The other half of the family: overlays that CANNOT declare aria-modal ─────────────────────
//
// The rail above keys off `aria-modal="true"`, so it can only ever see the three ui/ primitives.
// Every overlay OUTSIDE ui/ is invisible to it — and it has to be, because `primitiveAdoption` holds
// page-level ad-hoc dialogs at a baseline of **0** (`ui/Modal` is canonical), so a page overlay
// legally cannot declare the attribute this rail measures. The result was a blind spot with real
// defects in it: two hand-rolled modal dialogs over live content, both dismissible three ways, and
// neither holding focus. Measured before the fix:
//
//   pages/chat/SessionSkillsReview   scrim ✓  Escape ✓  scrim-click ✓  trap ✗  → Tab reached the composer
//   pages/knowledge/KnowledgeDetail  scrim ✓  Escape ✓  ✕ + backdrop ✓  trap ✗  → Tab reached the list
//   pages/chat/ChatFilePanel         opaque   Escape ✓  ✕ + collapse ✓  trap ✗  → Tab reached the chat
//
// 🔑 SO THE CONTRACT IS OWED BY BEHAVIOUR, NOT BY DECLARATION. "It covers live content and dismisses
// like a dialog" is what obliges containment; the attribute is just how a ui/ primitive says so.
//
// 🪤 AND THE POPULATION CANNOT BE READ OFF A CLASS STRING. `fixed inset-0` finds five files; three
// were defects, one already had the trap, and one is not an overlay at all. The previous pass classified by that string alone and reported
// `app/Onboarding.tsx` as the worst offender in the family — "no Escape, no trap, first screen a new
// user sees". **That was wrong.** `app/App.tsx` returns `<Onboarding />` INSTEAD of the shell, so
// nothing is mounted behind it: there is no page to trap focus away from, and Escape would dismiss a
// setup flow whose deliberate exit is "Set up later". A full-screen route wearing an overlay's class
// string. Each exemption below therefore asserts the STRUCTURAL fact that earns it, so the exemption
// breaks the moment the structure does.

describe('the rail: a hand-rolled modal over live content owes containment', () => {
  const overlays = walk(SRC)
    .map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))
    .filter((f) => !f.rel.startsWith('ui/') && /fixed inset-0/.test(f.src))

  it('finds the population — the census is not vacuous', () => {
    // pages/chat/SessionSkillsReview left this list by adopting ui/Modal — the
    // census shrinks when a hand-rolled overlay moves onto the primitive, which
    // is the direction this rail exists to push.
    expect(overlays.map((f) => f.rel).sort()).toEqual([
      'app/CommandPalette.tsx',
      'app/Onboarding.tsx',
      'pages/chat/ChatFilePanel.tsx',
      'pages/knowledge/KnowledgeDetail.tsx',
    ])
  })

  it('every overlay that covers live content wires useFocusTrap', () => {
    // THE RATCHET. `EXEMPT` is not "known broken" — each entry is a shape that does not cover live
    // content, and the test below proves the shape still holds.
    const EXEMPT = ['app/Onboarding.tsx']
    const offenders = overlays
      .filter((f) => !EXEMPT.includes(f.rel))
      .filter((f) => !/useFocusTrap/.test(f.src))
      .map((f) => f.rel)
    expect(
      offenders,
      `these cover a mounted page and dismiss like a dialog, so Tab must not leave them:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('Onboarding is exempt because it REPLACES the shell, and that is asserted', () => {
    // The structural fact: App renders it instead of the app, not on top of it. If this line ever
    // becomes a conditional overlay beside the shell, this test fails and the exemption is void.
    expect(read('app/App.tsx'), 'Onboarding must still be rendered INSTEAD of the shell')
      .toMatch(/return <Onboarding \/>/)
    // And it must not have grown a dialog's dismissal semantics in the meantime, which would make it
    // one of these after all.
    expect(/aria-modal/.test(read('app/Onboarding.tsx'))).toBe(false)
  })

  it("ChatFilePanel's expanded mode is covered too, and it is NOT a dialog", () => {
    // 🪤 I first exempted this as "a view state with no dismiss semantics — an owner call". The rail
    // failed and was right: line 41 already binds Escape (collapse when expanded, else close), so the
    // mode is keyboard-dismissible AND opaque over a mounted chat. Two of three dialog traits, and
    // the user-facing defect is identical — Tab onto controls that are not visible. A scrim's absence
    // does not change what focus can reach. So it is fixed, with containment owed by what it covers.
    //
    // It still must not CLAIM to be a dialog: page-level ad-hoc dialogs are held at 0.
    const src = read('pages/chat/ChatFilePanel.tsx')
    expect(src, 'still an expand/collapse view state').toMatch(/if \(expanded\)/)
    expect(src, 'Escape must keep collapsing the expansion').toMatch(/if \(expanded\) setExpanded\(false\)/)
    expect(/aria-modal|role="dialog"/.test(src), 'must not claim a dialog role').toBe(false)
  })

  it('the trap only engages where it is mounted WITH the overlay', () => {
    // Both fixes live in a component that is conditionally rendered, which is what makes the hook
    // work at all: its effect reads `ref.current`, so an always-mounted host toggling an internal
    // `open` flag gets a silently inert trap. Asserted as the structure that guarantees it.
    expect(read('pages/chat/SessionSkillsReview.tsx')).toMatch(/\{open && \(\s*<SessionSkillsModal/)
    expect(read('pages/knowledge/KnowledgeDetail.tsx')).toMatch(/\{fullscreen && <FullscreenModal/)
    // The third is the clearest case: the overlay had to be EXTRACTED from the panel to get a working
    // trap, because the panel mounts collapsed and the hook's effect never re-runs on `expanded`.
    expect(read('pages/chat/ChatFilePanel.tsx')).toMatch(/<ExpandedOverlay>\{body\}<\/ExpandedOverlay>/)
  })
})
