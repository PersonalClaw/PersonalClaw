import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, seedTheme } from './helpers'
import { THEMES, type Theme } from './routes'
import {
  BASELINED_STEPS,
  COMPACT_WIDTHS,
  STEP_NAMES,
  STEPS,
  accessibilityTree,
  findingKey,
  knownFindings,
  reducedMotionFindings,
  runMatrix,
  stepVerdict,
} from './surfaceChecks'

// ── The UI quality check can fail, on every axis, in one run ─────────────────────────────────────
//
// `surfaces.spec.ts` runs `runMatrix` over every surface and reports each step's findings under the
// step's own name, softly, so one surface reports every check that failed on it. Every step returns
// `[]` on a healthy surface, which reads exactly like a step that has gone blind. So this plants one
// defect of each kind on ONE surface, runs the SAME `runMatrix` once with a step that collects
// instead of asserting, and requires each defect under the step that owns it.
//
// The planted nodes live outside `#root`, so the app's re-renders on resize and zoom leave them in
// place. They are the FIRST children of <body>, so the keyboard walk reaches them before its cap,
// and each carries a `probe-` id or name the findings are matched by.

const PLANT = `(() => {
  const style = document.createElement('style')
  style.id = 'probe-style'
  style.textContent = [
    '@keyframes probe-spin { to { transform: rotate(360deg) } }',
    // A faint focus ring: drawn only on focus, so it announces focus, at about 1.1:1 on white.
    '#probe-faint:focus-visible { outline: 2px solid #eeeeee; outline-offset: 2px }',
  ].join('\\n')
  document.head.appendChild(style)
  const host = document.createElement('div')
  host.id = 'probe-host'
  host.style.cssText = 'position:absolute;left:0;top:0;z-index:2147483647;width:300px;background:#ffffff;color:#000000'
  host.innerHTML = [
    // 1. axe: a button with no accessible name (button-name, critical)
    '<button id="probe-nameless" style="width:48px;height:48px"></button>',
    // 2. a mouse-only control: a pointer cursor with no tag, role or tab stop
    '<div style="cursor:pointer;width:120px;height:48px">probe-mouse-only</div>',
    // 3. a focus stop that changes nothing when focused
    '<button style="outline:none;width:160px;height:48px">probe-unannounced</button>',
    // 4. a looping animation that escapes the reduced-motion rule
    '<div id="probe-spinner" style="width:48px;height:48px;animation:probe-spin 2s linear infinite;animation-duration:2s !important">probe-spinner</div>',
    // 5. 400 px wide: past the edge at 320 and 390
    '<div id="probe-wide-400" style="width:400px;height:8px"></div>',
    // 6. 360 px wide: past the edge at 320 only
    '<div id="probe-wide-360" style="width:360px;height:8px"></div>',
    // 7. a 40 px compact target
    '<button style="width:40px;height:40px">probe-target-40</button>',
    // 8. text that fits at 100% and clips at 200%
    '<div id="probe-clip-at-zoom" style="height:20px;overflow:hidden;font-size:1rem;line-height:1.2rem">probe-clip-at-zoom</div>',
    // 9. a focus order that runs backwards: DOM order a, b; drawn b, a
    '<div style="display:flex;flex-direction:row-reverse;width:300px">',
    '<button style="width:120px;height:48px">probe-order-a</button>',
    '<button style="width:120px;height:48px">probe-order-b</button>',
    '</div>',
    // 10. an accessibility-tree change: a heading the baseline does not have
    '<h2>probe-aria-heading</h2>',
    // and a focus ring with too little contrast to see
    '<button id="probe-faint" style="width:160px;height:48px">probe-faint-ring</button>',
  ].join('')
  document.body.prepend(host)
})()`

async function loadClean(page: Page, theme: Theme): Promise<void> {
  await seedTheme(page, theme)
  await gotoRoute(page, 'dashboard')
}

// Every check runs in both themes in the matrix, so each one is shown to fail in both.
for (const theme of THEMES) test.describe(`the UI quality check reports every planted defect, each under its own step (${theme})`, { tag: '@ui-quality' }, () => {
  // One test runs the whole matrix, as each surface test does.
  test.describe.configure({ timeout: 180_000 })

  test(`one run reports all ten defects on one surface (${theme})`, async ({ page }) => {
    await loadClean(page, theme)
    // The tree as it stands before the plant is this run's baseline, so the heading is the change.
    const before = await accessibilityTree(page)
    await page.evaluate(PLANT)

    const found = new Map<string, string[]>()
    const held = new Map<string, boolean>()
    await runMatrix(
      page,
      { where: `#/dashboard (${theme}, planted)`, theme, kind: 'route', route: 'dashboard' },
      // Every width's target baseline is empty and no step has known findings, so every planted
      // defect is a finding.
      {
        tree: before,
        targets: Object.fromEntries(COMPACT_WIDTHS.map((w) => [String(w), []])),
        known: Object.fromEntries(BASELINED_STEPS.map((s) => [s, []])),
        adopt: false,
      },
      async (name, findings, baselined) => {
        found.set(name, await findings())
        held.set(name, baselined)
      },
    )

    // Every step ran, and none was skipped on the way.
    expect([...found.keys()]).toEqual([...STEP_NAMES])

    // A refresh rewrites what a step held to a baseline found, and still fails on the three held to
    // zero: their planted defects fail it as they fail a compare (`stepVerdict`).
    const zero = [STEPS.detectors, STEPS.motion, STEPS.keyboard]
    expect([...held].filter(([, b]) => !b).map(([name]) => name)).toEqual(zero)
    for (const name of zero) expect(stepVerdict(found.get(name) ?? [], false, true), `${name} on a refresh`).toBe('failed')
    expect(stepVerdict(found.get(STEPS.tree) ?? [], true, true), 'the tree on a refresh').toBe('rewritten')
    expect(stepVerdict(found.get(STEPS.tree) ?? [], true, false), 'the tree on a compare').toBe('failed')

    const under = (step: string, probe: string) =>
      expect((found.get(step) ?? []).join('\n'), `${step} did not report ${probe}`).toContain(probe)

    under(STEPS.axe, 'button-name')
    under(STEPS.detectors, 'probe-mouse-only')
    under(STEPS.keyboard, 'probe-unannounced')
    under(STEPS.motion, 'probe-spin')
    under('overflow at 390px', 'probe-wide-400')
    under('overflow at 320px', 'probe-wide-360')
    expect((found.get('overflow at 390px') ?? []).join('\n'), 'a 360 px element fits at 390').not.toContain('probe-wide-360')
    // Target keys fold digits, so a baseline survives a count changing: the probe is `probe-target-#`.
    for (const w of COMPACT_WIDTHS) under(`44 px targets at ${w}px`, 'probe-target-#')
    under(STEPS.zoom, 'probe-clip-at-zoom')
    under(STEPS.order, 'probe-order-b')
    under(STEPS.tree, 'probe-aria-heading')
    under(STEPS.contrast, 'probe-faint-ring')
  })

  test(`a small target scrolled out of view is still counted at every compact width (${theme})`, async ({ page }) => {
    // The keyboard walk scrolls whatever holds the stop it is on, and how far it gets is timing, so
    // a control it scrolled past sat above the viewport at the widths and dropped out of the 44 px
    // count on one run and not the next. Planted: a 40 px button at the top of a scroller, scrolled
    // past before the matrix runs. It takes no tab stop, so the walk never scrolls it back.
    await loadClean(page, theme)
    await page.evaluate(() => {
      const box = document.createElement('div')
      box.id = 'probe-scroller'
      box.style.cssText = 'position:absolute;left:0;top:0;z-index:2147483647;width:200px;height:120px;overflow:auto;background:#ffffff'
      box.innerHTML = '<button tabindex="-1" style="width:40px;height:40px">probe-scrolled-40</button><div style="height:800px"></div>'
      document.body.prepend(box)
      box.scrollTop = 600
    })
    const found = new Map<string, string[]>()
    await runMatrix(
      page,
      { where: `#/dashboard (${theme}, scrolled)`, theme, kind: 'route', route: 'dashboard' },
      { tree: null, targets: Object.fromEntries(COMPACT_WIDTHS.map((w) => [String(w), []])), known: {}, adopt: true },
      async (name, findings) => {
        found.set(name, await findings())
      },
    )
    for (const w of COMPACT_WIDTHS) {
      expect((found.get(`44 px targets at ${w}px`) ?? []).join('\n'), `the scrolled-past button at ${w} px`).toContain('probe-scrolled-#')
    }
  })

  // An opener's visit is of its opened window, so the widths are the window's. The tidy-up after the
  // keyboard walk pressed Escape, which closed the window itself, and the widths then read it part
  // way out or the page behind it, by timing: Chat preferences' window, leaving on a spring, was
  // under 44 px at 768 on one run and gone on the next. Planted: a window that Escape closes and
  // that leaves 300 ms later, as an animated exit does, holding a 40 px button the walk never
  // reaches. On a route's row the Escape still closes it.
  const plantWindow = (page: Page) =>
    page.evaluate(() => {
      const win = document.createElement('div')
      win.id = 'probe-window'
      win.setAttribute('role', 'dialog')
      win.setAttribute('aria-label', 'probe window')
      win.style.cssText = 'position:fixed;right:0;bottom:0;z-index:2147483647;width:120px;background:#ffffff'
      win.innerHTML = '<button tabindex="-1" style="width:40px;height:40px">probe-window-40</button>'
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setTimeout(() => win.remove(), 300)
      })
      document.body.append(win)
    })
  const targetsAt = async (page: Page, ctx: Parameters<typeof runMatrix>[1]) => {
    const found = new Map<string, string[]>()
    await runMatrix(
      page,
      ctx,
      { tree: null, targets: Object.fromEntries(COMPACT_WIDTHS.map((w) => [String(w), []])), known: {}, adopt: true },
      async (name, findings) => {
        found.set(name, await findings())
      },
    )
    return (w: number) => (found.get(`44 px targets at ${w}px`) ?? []).join('\n')
  }

  test(`an opener's window is measured open at every compact width, never as it closes (${theme})`, async ({ page }) => {
    await loadClean(page, theme)
    await plantWindow(page)
    const at = await targetsAt(page, { where: `probe window [opened] (${theme})`, theme, kind: 'opener', route: 'dashboard', opener: 'probe window' })
    for (const w of COMPACT_WIDTHS) expect(at(w), `the opened window at ${w} px`).toContain('probe-window-#')
  })

  test(`on a route's row the walk's Escape still closes what is open before the widths (${theme})`, async ({ page }) => {
    await loadClean(page, theme)
    await plantWindow(page)
    const at = await targetsAt(page, { where: `#/dashboard (${theme}, a window open)`, theme, kind: 'route', route: 'dashboard' })
    for (const w of COMPACT_WIDTHS) expect(at(w), `the closed window at ${w} px`).not.toContain('probe-window-#')
  })

  // A width is read once its controls hold still. The widths waited a fixed 150 ms after each
  // resize, so a panel that springs to the new width (the chat's session inspector docks at 320 px
  // on a spring) was read part way there on a slow runner and settled on a fast one. Planted: after
  // each resize a 40 px control slides for 1.2 s, then rests at 48 px, as a spring does. Read mid
  // slide it is under 44 px; at rest it is not. A control that never rests fails the row.
  const plantSlider = (page: Page, forever: boolean) =>
    page.evaluate((loop) => {
      const box = document.createElement('div')
      box.style.cssText = 'position:fixed;left:0;bottom:0;z-index:2147483647;width:300px;height:48px;background:#ffffff'
      const b = document.createElement('button')
      b.tabIndex = -1
      b.textContent = loop ? 'probe-restless' : 'probe-sliding'
      b.style.cssText = 'position:absolute;top:0;left:0;width:48px;height:48px'
      box.append(b)
      let start = 0
      const frame = (now: number) => {
        const t = now - start
        if (loop || t < 1200) {
          b.style.width = '40px'
          b.style.left = `${Math.round((t / 6) % 200)}px`
          requestAnimationFrame(frame)
        } else {
          b.style.width = '48px'
          b.style.left = '200px'
        }
      }
      window.addEventListener('resize', () => {
        start = performance.now()
        requestAnimationFrame(frame)
      })
      document.body.append(box)
    }, forever)

  test(`a width is read once its controls hold still, never while a panel still springs to it (${theme})`, async ({ page }) => {
    await loadClean(page, theme)
    await plantSlider(page, false)
    const at = await targetsAt(page, { where: `#/dashboard (${theme}, sliding)`, theme, kind: 'route', route: 'dashboard' })
    for (const w of COMPACT_WIDTHS) expect(at(w), `the control at rest at ${w} px`).not.toContain('probe-sliding')
  })

  test(`a row whose controls never hold still fails, naming the row and what moved (${theme})`, async ({ page }) => {
    await loadClean(page, theme)
    await plantSlider(page, true)
    const where = `#/dashboard (${theme}, restless)`
    const failed = await targetsAt(page, { where, theme, kind: 'route', route: 'dashboard' }).then(
      () => null,
      (err: unknown) => (err instanceof Error ? err.message : String(err)),
    )
    expect(failed, 'a row whose controls never hold still fails').not.toBeNull()
    expect(failed).toContain(`${where} at 1440px: the controls did not hold still`)
    expect(failed).toContain('probe-restless')
  })

  test(`a baselined finding passes, a new one fails, and a fixed one fails until its entry goes (${theme})`, async () => {
    const raw = ['focus indicator at 1.11:1 against its backdrop: <button> "New chat"']
    const base = raw.map(findingKey)
    expect(knownFindings(raw, base)).toEqual([])
    expect(knownFindings(['focus indicator at 1.13:1 against its backdrop: <button> "New chat"'], base), 'a ratio that moves is the same finding').toEqual([])
    expect(knownFindings([...raw, 'focus indicator at 1.0:1 against its backdrop: <button> "Send"'], base)).toHaveLength(1)
    expect(knownFindings([], base).join('\n')).toContain('no longer found, delete it from the baseline')
    expect(knownFindings(raw, undefined).join('\n')).toContain('no baseline is committed')
    expect(knownFindings(raw, undefined, true)).toEqual([])
  })

  test(`the reduced-motion step fails when the preference is not emulated (${theme})`, async ({ page }) => {
    await loadClean(page, theme)
    const off = await reducedMotionFindings(page, { theme, emulate: false })
    expect(off.join('\n')).toContain('prefers-reduced-motion is NOT emulated')
    const on = await reducedMotionFindings(page, { theme, emulate: true })
    expect(on.join('\n')).not.toContain('prefers-reduced-motion is NOT emulated')
  })
})
