import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Side-stripe doctrine rail (Tone-Not-Line, web/DESIGN.md §"The Tone-Not-Line Rule") ──
// "Depth and grouping come from the surface ramp; borders are 1px hairlines or nothing.
// Never a colored side-stripe." The Don't list bans `border-left` > 1px in color. Two
// violations shipped anyway — the Markdown blockquote's 3px coral stripe (ui/Markdown.tsx)
// and the Code Cockpit toast's dynamic 3px tone stripe (both fixed in the commit that adds
// this rail; the toast already carries its tone in the icon and the Respond button, so the
// stripe was redundant tone-as-line). This is a ZERO-tolerance scan, not a ratchet: the
// live count is 0, so any new multi-pixel `border-l-[Npx]` utility or inline `borderLeft:`
// style turns CI red. 1px `border-l` hairlines (panel dividers, tree indent guides) are
// the sanctioned form and do not match.
//
// Runs in the existing CI `web` vitest job (source-text scan, no browser).

const ROOT = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(ts|tsx)$/.test(name) && !/\.(test|spec)\./.test(name)) out.push(p)
  }
  return out
}

function isComment(line: string): boolean {
  const t = line.trimStart()
  return t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')
}

function findSideStripes(): string[] {
  const offenders: string[] = []
  for (const file of walk(ROOT)) {
    const lines = readFileSync(file, 'utf8').split('\n')
    lines.forEach((line, i) => {
      if (isComment(line)) return
      // A multi-pixel border-left utility (border-l-[2px] and up) — the colored
      // side-stripe shape. The 1px `border-l` hairline never matches this.
      if (/border-l-\[[2-9]\d*px\]/.test(line)) offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1} ${line.trim()}`)
      // A multi-pixel inline borderLeft style — the dynamic-tone-stripe shape. The 1px
      // hairline is sanctioned in any mechanism (CodeMirror theme objects cannot use the
      // border-l utility), so only widths above 1px match.
      if (/\bborderLeft\s*:\s*['"`]?\s*[2-9]\d*px/.test(line)) offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1} ${line.trim()}`)
    })
  }
  return offenders
}

describe('side-stripe doctrine (Tone-Not-Line: no colored side-stripes, ever)', () => {
  it('no multi-pixel border-left utility or inline borderLeft style ships in src', () => {
    const offenders = findSideStripes()
    expect(
      offenders,
      `Colored side-stripe(s) detected — web/DESIGN.md's Tone-Not-Line rule bans border-left ` +
        `wider than the 1px hairline. Carry the tone in a chip, dot, or icon instead ` +
        `(see the Code Cockpit toast: icon + Respond button already carry it).\n` +
        offenders.join('\n'),
    ).toEqual([])
  })
})
