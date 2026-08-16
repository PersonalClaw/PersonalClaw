import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// `ContentSurface`'s toolbar carries the view toggle plus every edit-mode action, and it is used by
// the artifact viewer, the file viewer, the workflows outbox and two loop surfaces. At a phone width
// it could not give: measured on the artifact viewer at 390px, `#root` was 411px wide with NO
// scrollable ancestor, so 21px were CLIPPED and the last action ("Snapshot") was unreachable.
//
// Wrapping was already understood to be right here — it was gated on `compact`, i.e. on DENSITY,
// when the thing that decides it is AVAILABLE SPACE, which is exactly what flex-wrap reads. So the
// fix is to stop gating it, and both rows need it:
//   the toolbar row      — so the action cluster has a second line to drop to
//   the `ml-auto` cluster — it is 399px wide alone at a 390px viewport, so a line of its own is
//                           still not enough; it has to wrap internally too
//
// 🪤 COMMENTS ARE STRIPPED BEFORE SCANNING. This block names `flex-wrap` and `compact`, so a scan of
// the raw file would match its own prose and pass no matter what the JSX says.
const FILE = join(process.cwd(), 'src', 'ui', 'content', 'ContentSurface.tsx')

/** Source with line, block and JSX comments removed. */
function code(): string {
  return readFileSync(FILE, 'utf8')
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')
}

/** Opening tags, anchored on NAMED classes rather than on tag order — counting `<div>`s backwards
 *  does not track nesting and silently resolves the wrong element. */
function tags(src: string): { toolbar: string; cluster: string } {
  const toolbar = src.match(/<div className="[^"]*\bborder-b border-outline\/40 px-m py-1\.5[^"]*">/)
  const cluster = src.match(/<div className="[^"]*\bml-auto\b[^"]*\bgap-1\b[^"]*">/)
  expect(toolbar?.[0], 'the toolbar row opening tag was not found — this rail is measuring nothing').toBeTruthy()
  expect(cluster?.[0], 'the ml-auto action cluster was not found — this rail is measuring nothing').toBeTruthy()
  return { toolbar: toolbar![0], cluster: cluster![0] }
}

describe('ContentSurface toolbar survives a phone width', () => {
  it('the toolbar row wraps', () => {
    const { toolbar } = tags(code())
    expect(toolbar, `the toolbar row must wrap: ${toolbar}`).toMatch(/\bflex-wrap\b/)
  })

  it('the wrap is not gated on `compact` — density is not available space', () => {
    const src = code()
    expect(src, 'flex-wrap must not be conditional on compact')
      .not.toMatch(/compact\s*\?\s*'flex-wrap'/)
  })

  it('the ml-auto action cluster wraps too, and stays right-aligned when it does', () => {
    const { cluster } = tags(code())
    expect(cluster, `the action cluster must wrap: ${cluster}`).toMatch(/\bflex-wrap\b/)
    expect(cluster, `wrapped lines must stay flush right: ${cluster}`).toMatch(/\bjustify-end\b/)
  })

  // Vacuity floor: every assertion above is a source scan, so prove the scanned text is real JSX
  // and that comment-stripping actually ran.
  it('the scanned source is real (guard against a vacuous pass)', () => {
    const src = code()
    expect(src).toContain('<ToggleBtn')
    expect(src).toContain('showToolbar &&')
    expect(src).not.toContain(['available', 'space'].join(' ').toUpperCase())
  })
})
