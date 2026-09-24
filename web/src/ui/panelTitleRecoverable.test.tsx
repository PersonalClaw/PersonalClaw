import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { SidePanel } from './SidePanel'

// ── The panel's title clips on a DESKTOP too, which makes it the odd one out ────────────────────
//
// Every other truncation fixed this session was phone-only: nothing clipped at 1440px. This one does,
// because the panel is a fixed-width column — the title gets ~300px whatever the viewport. Measured on
// two real task titles through the URL-backed panel:
//
//   "Summarize the three saved long-reads into the weekly digest"   299 / 574px at 1440  (1.9x)
//                                                                  239 / 574px at  390  (2.4x)
//   "Reconcile the duplicate notes the bulk import created"         297 / 505px at 1440
//
// So it costs EVERY user, not just phone users — and it is one element shared by **24** surfaces that
// mount a `SidePanel`, which is why it was held back from the tasks PR and given its own change.
//
// 🔑 THE `title` IS CONDITIONAL BECAUSE THE PROP IS A `ReactNode`. A JSX title cannot become a DOM
// tooltip; passing one would stringify to "[object Object]" — worse than no tooltip, because it looks
// like data. Every call site today passes a string (`open.title`, `open.name`, `selectedEntity`, …) so
// all of them gain it, and a future JSX title gets nothing rather than something wrong.

const SRC = readFileSync(join(process.cwd(), 'src/ui/SidePanel.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

describe('a panel title is recoverable when it clips', () => {
  it('a string title becomes a tooltip', () => {
    render(
      <SidePanel title="Summarize the three saved long-reads into the weekly digest" onClose={() => {}}>
        <p>body</p>
      </SidePanel>,
    )
    const h = screen.getByRole('heading', { level: 2 })
    expect(h.getAttribute('title')).toBe('Summarize the three saved long-reads into the weekly digest')
  })

  it('a NODE title gets no tooltip rather than "[object Object]"', () => {
    // The whole reason the attribute is conditional. A stringified React element in a tooltip reads as
    // data and would be worse than the clipping it tried to fix.
    render(
      <SidePanel title={<span>Composed <b>title</b></span>} onClose={() => {}}>
        <p>body</p>
      </SidePanel>,
    )
    const h = screen.getByRole('heading', { level: 2 })
    expect(h.getAttribute('title')).toBeNull()
  })

  it('the guard is the type check, not a truthiness test', () => {
    // `title && ...` would pass a ReactNode straight through; only `typeof` distinguishes them.
    expect(CODE).toMatch(/title=\{typeof title === 'string' \? title : undefined\}/)
  })

  it('the heading still truncates, and still names the region', () => {
    // The fix is recovery, not re-layout — and the h2 carries `aria-labelledby` for the panel's region,
    // which cycle-earlier work put there. Both must survive.
    expect(CODE).toMatch(/data-type="title-l" className="text-on-surface truncate"/)
    expect(CODE).toMatch(/aria-labelledby=\{titleId\}/)
  })

  it('this is genuinely the shared panel — many surfaces mount it', () => {
    // The vacuity floor for the leverage claim. If SidePanel ever stops being widely used, the
    // "one edit, every panel" reasoning in the header needs revisiting.
    const consumers = walk(join(process.cwd(), 'src'))
      .filter((abs) => /<SidePanel[\s>]/.test(readFileSync(abs, 'utf8')))
    expect(consumers.length, 'surfaces mounting a SidePanel').toBeGreaterThanOrEqual(15)
  })
})
