// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── A way back or elsewhere is not the page's primary action ─────────────────────────────────────
//
// A `priority="primary"` header control with no `ariaExpanded` is the page's primary action
// (`ui/HeaderActions` `isThePrimary`), and it ends the row after the `…`. The Shell's All projects,
// the plan review's Back and the loop composer's All loops were marked so, to keep them from
// shedding, so a project's Overview at 390 px ended "☆ · … · All projects · New", with a way out
// read as the page's action. A control that leaves the page takes no priority.

const LEAVES = /^(All [a-z]+|Back)$/

describe('no header control that leaves the page is marked the primary', () => {
  it('none of All <things> or Back carries priority="primary"', () => {
    const hits: string[] = []
    for (const file of filesUnder(join(process.cwd(), 'src'), (name) => name.endsWith('.tsx') && !name.includes('.test.'))) {
      for (const m of readSource(file).matchAll(/<HeaderControl\b[^>]*?label="([^"]+)"[^>]*?>/g)) {
        if (LEAVES.test(m[1]) && /priority="primary"/.test(m[0])) hits.push(`${file.split('/src/')[1]}: ${m[1]}`)
      }
    }
    expect(hits).toEqual([])
  })
})
