/** The work surface's share of the window, in percent, read in the page: the shell's main area,
 *  minus the top band (the corner controls and the page header), minus the composer's height when
 *  the screen has one, minus the status line while it shows, minus a docked right pane, over the
 *  viewport. It is the measure `docs/screenshots/baseline.mjs` makes for the baseline report, with
 *  the status line (#3823) counted as the chrome it is: focus mode hides the line while the gateway
 *  answers, and then the work reaches the window's bottom.
 *
 *  Self-contained, so `page.evaluate(measureWorkShare)` can carry it into the page; a unit test
 *  calls it directly (`src/design/theWorkShareCountsTheStatusLine.test.ts`). */
export function measureWorkShare(): number {
  const vw = innerWidth
  const vh = innerHeight
  const box = (el: Element | null | undefined) => {
    if (!el) return null
    const r = el.getBoundingClientRect()
    const x = Math.max(0, r.left), y = Math.max(0, r.top)
    const w = Math.min(vw, r.right) - x, h = Math.min(vh, r.bottom) - y
    return w > 0 && h > 0 ? { x, y, w, h } : null
  }
  const visible = (el: Element) => !!box(el) && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
  const main = [...document.querySelectorAll('main')].find((m) => !m.parentElement?.closest('main') && visible(m))
  const mainBox = box(main) ?? { x: 0, y: 0, w: vw, h: vh }
  const corners = main ? [...main.children].filter((c) => c.matches('.absolute.top-0') && visible(c)) : []
  const header = main ? [...main.querySelectorAll('header')].find((h) => visible(h) && h.getBoundingClientRect().top < 80) : null
  const composer = [...document.querySelectorAll('[data-composer-stage]')].find(visible) ?? null
  const statusLine = [...document.querySelectorAll('footer[aria-label="Status"]')].find(visible) ?? null
  const pane = main ? [...main.querySelectorAll('[role="region"][aria-labelledby]')].find((r) => {
    const b = box(r)
    return visible(r) && b && Math.abs(b.x + b.w - (mainBox.x + mainBox.w)) <= 2 && b.h > vh * 0.5
  }) : null
  const headerBox = box(header)
  const cornerBottom = Math.max(0, ...corners.map((c) => { const b = box(c)!; return b.y + b.h }))
  const topBand = Math.max(cornerBottom, headerBox ? headerBox.y + headerBox.h : 0)
  const composerBox = box(composer)
  const paneBox = box(pane)
  const lineBox = box(statusLine)
  const bottom = lineBox ? lineBox.y : vh
  const workW = (paneBox ? paneBox.x : mainBox.x + mainBox.w) - mainBox.x
  const workH = bottom - topBand - (composerBox ? composerBox.h : 0)
  return Math.round((Math.max(0, workW) * Math.max(0, workH)) / (vw * vh) * 1000) / 10
}
