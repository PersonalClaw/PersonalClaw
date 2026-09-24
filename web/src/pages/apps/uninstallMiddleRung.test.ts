import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// ── Issue 2541: the removal ladder has a middle rung, and the copy points at it ──────
//
// The Library offered Deactivate | Configure | Update plus Advanced → Force uninstall,
// and the force-uninstall confirmation told the user:
//
//     "To just turn the app off (keeping its files), use Uninstall instead."
//
// There was no Uninstall control anywhere in the panel. So the one screen warning a
// user they were about to destroy their notes sent them to a button that did not
// exist — and the reader most likely to follow that advice is exactly the one trying
// to avoid losing data. It also described Deactivate's behaviour under Uninstall's
// name, which is why "fix the copy" alone was not the fix.
//
// Now there are three rungs, each with its own real control:
//   Deactivate       nothing leaves disk
//   Uninstall        the app's files go, the user's data/ stays  ← the new one
//   Force uninstall  everything goes, data/ included
//
// Pinned at the SOURCE level, like `appToggleOneVocabulary.test.ts` next door: these
// are label strings and a wiring choice living in one file, and mounting the whole
// section to read a few buttons would test the same characters through more machinery.
// Comments are stripped first so prose ABOUT the old copy can neither satisfy nor trip
// the rail — a source-scan counting its own commentary is a repeat trap in this suite.

const SRC = resolve(__dirname, 'AppsSection.tsx')
const API = resolve(__dirname, '../../lib/api.ts')

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

const code = stripComments(readFileSync(SRC, 'utf8'))
const api = stripComments(readFileSync(API, 'utf8'))

describe('the middle removal rung exists as a real control (issue 2541)', () => {
  it('the force-uninstall dialog no longer points at a control that does not exist', () => {
    // The exact dead sentence, and the two shapes it could come back as.
    expect(code).not.toContain('To just turn the app off (keeping its files), use Uninstall instead')
    expect(code).not.toMatch(/turn the app off[^.]*use <?span?[^>]*>?Uninstall/)
  })

  it('names BOTH lesser rungs, each by what it actually does', () => {
    // The dialog has to distinguish them: one keeps the data, the other keeps everything.
    expect(code).toMatch(/To keep your data, use[\s\S]{0,60}Uninstall/)
    expect(code).toMatch(/turn the app off and leave everything on disk, use[\s\S]{0,60}Deactivate/)
  })

  it('renders an Uninstall control in the detail panel, wired to the middle rung', () => {
    // A button whose label is Uninstall...
    expect(code).toMatch(/setConfirmRemove\(true\)[\s\S]{0,120}Uninstall<\/Button>/)
    // ...opening a modal that calls the keep-data endpoint, NOT force.
    expect(code).toContain('RemoveAppModal')
    expect(code).toMatch(/api\.removeApp\(name\)/)
  })

  it('offers the safe rung wherever it offers the destructive one', () => {
    // Both ⋯ surfaces (the Popover menu and the card's context menu) previously made
    // Force uninstall the only way to get rid of an app.
    const uninstallRows = code.match(/label\s*[=:]\s*['"]Uninstall…['"]/g) ?? []
    const forceRows = code.match(/label\s*[=:]\s*['"]Force uninstall…['"]/g) ?? []
    expect(forceRows.length).toBeGreaterThanOrEqual(2)
    expect(uninstallRows.length).toBe(forceRows.length)
  })

  it('force uninstall still calls the destructive endpoint — the rung did not move', () => {
    // The negative half. If this ever stops passing force=true, "Force uninstall" has
    // quietly become the keep-data path and the destructive control is lying.
    expect(code).toMatch(/api\.uninstallApp\(name,\s*true\)/)
    expect(api).toContain("?force=1")
    expect(api).toContain("?remove=1")
  })

  it('keeps the three client calls distinct so a call site cannot land on the wrong rung', () => {
    // deactivate / remove-keep-data / force are three functions or flag shapes, and the
    // destructive one is the only one that takes a boolean.
    expect(api).toMatch(/uninstallApp:\s*\(name: string, force = false\)/)
    expect(api).toMatch(/removeApp:\s*\(name: string\)/)
  })

  it('states the data outcome from the two SEPARATE facts, not one truthiness', () => {
    // `present` (is there a data dir?) and `entries` (does it hold anything?) are
    // different facts: `present:false` means the app keeps no data at all, while
    // `present:true, entries:0` means it has an empty one. The dialog promises a
    // different thing in each case, so both are read.
    expect(code).toMatch(/!facts\.present/)
    expect(code).toMatch(/facts\.entries === 0/)
    expect(api).toContain('AppDataFacts')
  })

  it('says an earlier unconsumed copy will BLOCK the removal, and names the paths (2585)', () => {
    // The backend refuses fail-closed when a park or a stage from an earlier run is still
    // on disk — and `DELETE ?remove=1` reports every refusal as `404 app not installed`.
    // Pressing Uninstall there produces a false message that says nothing about the data
    // it just protected, so the dialog has to state it BEFORE the click. Three parts, each
    // load-bearing: the field is read, the primary button is gated on it, and the paths are
    // rendered (a warning with no path is not recovery information).
    expect(api).toContain('unconsumed?: string[]')
    expect(code).toMatch(/facts\?\.unconsumed \?\? \[\]/)
    expect(code).toMatch(/const blocked = unconsumed\.length > 0/)
    expect(code).toMatch(/disabled=\{blocked\}/)
    // …with a reason, not a bare title: the reason keeps the tab stop, so the keyboard user
    // can land on the button and hear why. See ui/disabledReasonTriage.test.ts, which reds
    // on a user-fixable gate carrying no `disabledReason`.
    expect(code).toMatch(/disabledReason=\{blocked \?/)
    // The paths themselves reach the DOM, not just a count.
    expect(code).toMatch(/unconsumed\.map\(\(p\) => \([\s\S]{0,200}\{p\}/)
    // …and it is an alert, so a screen reader is told rather than shown.
    expect(code).toMatch(/role="alert"/)
  })
})
