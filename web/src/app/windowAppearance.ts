import { DARK_CANVAS, LIGHT_CANVAS } from '../design/canvas'
import { desktopBridge } from '../lib/desktopBridge'
import type { Mode, Preference } from './theme'

/**
 * The page's theme, said to whatever shows the page.
 *
 * Each switch writes the mode on the root in a layout effect (`theme.tsx`), and so does each change
 * of the colours (`appearance.tsx`). Each then asks for a report here, which waits for one
 * microtask, so it runs once per commit and after both effects: the theme provider's is the outer
 * one, so the appearance's runs first and would otherwise read the old mode. The report reads the
 * colour the page now paints, the body's computed background, sets the theme colour to it, which a
 * browser's title bar and a paired gateway's desktop window follow, and tells the desktop shell
 * when it differs from the last one told (`pclawDesktop.appearance.changed`), so the window, the
 * strip above the page and the native chrome follow at once (`desktop/windowAppearance.js`).
 */

/** The canvas tokens (`design/canvas.ts`), for a ground that can't be read. */
export { DARK_CANVAS, LIGHT_CANVAS }

/** A computed colour (`rgb(r, g, b)`, or an opaque `rgba`) or a `#rrggbb` as `#rrggbb`; else ''. */
export function hexOf(colour: string | null | undefined): string {
  const text = (colour ?? '').trim().toLowerCase()
  if (/^#[0-9a-f]{6}$/.test(text)) return text
  const m = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*(1|1\.0+)\s*)?\)$/.exec(text)
  if (!m) return ''
  const parts = m.slice(1, 4).map(Number)
  if (parts.some((n) => n > 255)) return ''
  return `#${parts.map((n) => n.toString(16).padStart(2, '0')).join('')}`
}

let queued = false
let told = ''

/** Forget what was last told, so each test starts as a new page does. Tests only. */
export function resetAppearanceReportForTests(): void {
  queued = false
  told = ''
}

/** Report the page's theme once this commit's effects have all run. */
export function reportAppearance(): void {
  if (queued) return
  queued = true
  queueMicrotask(() => {
    queued = false
    report()
  })
}

function report(): void {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  const mode: Mode = root.dataset.mode === 'light' ? 'light' : 'dark'
  const written = root.dataset.modePreference
  const preference: Preference = written === 'dark' || written === 'light' || written === 'auto' ? written : mode
  const ground = document.body ? hexOf(getComputedStyle(document.body).backgroundColor) : ''
  const canvas = ground || (mode === 'light' ? LIGHT_CANVAS : DARK_CANVAS)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', canvas)
  const now = `${preference} ${mode} ${canvas}`
  const bridge = desktopBridge()?.appearance
  if (!bridge || now === told) return
  told = now
  bridge.changed({ preference, mode, canvas })
}
