// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'
import { tagsOf } from '../test/jsxTags'

// ── A way back or elsewhere is not the page's primary action ─────────────────────────────────────
//
// The page's primary action (`ui/HeaderActions` `isThePrimary`: `variant="primary"`, or
// `priority="primary"` with no `ariaExpanded`) ends the row after the `…`. The Shell's All projects,
// the plan review's Back and the loop composer's All loops were marked so, to keep them from
// shedding, so a project's Overview at 390 px ended "☆ · … · All projects · New", with a way out
// read as the page's action. A control that leaves the page is not the primary. Each tag is read
// whole (`tagsOf`), so an arrow-function prop before its label or priority does not hide them.

const LEAVES = /^(All [a-z]+|Back)$/

/** The header controls in *src* that leave the page and are marked as its primary action. */
export function waysBackMarkedPrimary(src: string): string[] {
  const hits: string[] = []
  for (const tag of tagsOf(src, ['HeaderControl', 'HeaderMenu'])) {
    const label = /\slabel="([^"]+)"/.exec(tag.text)?.[1]
    if (!label || !LEAVES.test(label)) continue
    const primary = /\svariant="primary"/.test(tag.text)
      || (/\spriority="primary"/.test(tag.text) && !/\sariaExpanded=/.test(tag.text))
    if (primary) hits.push(label)
  }
  return hits
}

describe('no header control that leaves the page is marked the primary', () => {
  it('none of All <things> or Back is the primary, by either form', () => {
    const hits: string[] = []
    for (const file of filesUnder(join(process.cwd(), 'src'), (name) => name.endsWith('.tsx') && !name.includes('.test.'))) {
      for (const label of waysBackMarkedPrimary(readSource(file))) hits.push(`${file.split('/src/')[1]}: ${label}`)
    }
    expect(hits).toEqual([])
  })

  it('reads a tag as the rule says', () => {
    expect(waysBackMarkedPrimary('<HeaderControl label="All projects" priority="primary" />')).toEqual(['All projects'])
    expect(waysBackMarkedPrimary('<HeaderControl label="Back" variant="primary" />')).toEqual(['Back'])
    // An onClick before the rest ended the tag at its `=>`, and the priority after it went unread.
    expect(waysBackMarkedPrimary('<HeaderControl onClick={() => go()} label="All loops" priority="primary" />')).toEqual(['All loops'])
    expect(waysBackMarkedPrimary('<HeaderControl onClick={() => go()} label="New" priority="primary" />')).toEqual([])
    // A disclosure kept by priority is not the primary, nor is a way back with no priority.
    expect(waysBackMarkedPrimary('<HeaderControl label="All tasks" priority="primary" ariaExpanded={open} />')).toEqual([])
    expect(waysBackMarkedPrimary('<HeaderControl label="All projects" onClick={() => go()} />')).toEqual([])
  })
})
