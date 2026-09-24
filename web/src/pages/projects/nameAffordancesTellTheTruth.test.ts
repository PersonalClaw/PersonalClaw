import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The projects hub must not describe the auto-naming mechanism wrongly ──────────────────────
//
// Two sibling defects in ONE component, both about the same real mechanism (`name_locked` = "the
// user owns this name, so the LLM stops auto-renaming"):
//
//   #449  a padlock labelled "Name locked" sat beside a title that renames fine. The flag locks
//         nothing — `update_project` guards only built-ins and duplicates — and create plus both
//         rename confirms all WRITE `name_locked: true`, so the icon was on by default for every
//         project a user made. DELETED rather than relabelled: the state is about agent behaviour,
//         there is no user-facing control to clear it, and an indicator true for nearly every row
//         carries no signal.
//
//   #451  the name placeholder offered "or let the system name it later" while the Create button,
//         `create()` and the server all refuse a blank name. The COPY gave way, because the other
//         direction (blank names legal) needs a new create path through the loop's auto-name flow.
//
// Comments are stripped before every assertion — this file's own prose, and the explanatory
// comments left at both fix sites, name the deleted strings on purpose.

const SRC = readFileSync(join(process.cwd(), 'src', 'pages', 'projects', 'ProjectsSection.tsx'), 'utf8')
const strip = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const CODE = strip(SRC)

describe('#449 the inert padlock is gone', () => {
  it('nothing READS name_locked any more', () => {
    // The flag is write-only in the UI now. A read is what produced the false affordance.
    expect(CODE).not.toMatch(/\bproject\.name_locked\b/)
    expect(CODE).not.toMatch(/name_locked\s*&&/)
  })

  it('no "Name locked" label and no Lock glyph survive', () => {
    expect(CODE).not.toContain('Name locked')
    expect(CODE).not.toMatch(/<Lock\b/)
    // The import went with the only use — a stale one would fail lint, but pin it here too
    // so a later pass cannot quietly re-add the icon with the import already in place.
    expect(CODE).not.toMatch(/\bLock,/)
  })

  it('the deliberate WRITES are untouched — the control', () => {
    // If these vanished the assertions above would pass vacuously while the mechanism broke.
    // The writes are correct: a name the user typed should stop the LLM auto-renaming.
    expect(CODE).toContain('name_locked: true')
    expect(CODE.match(/name_locked:\s*true/g) ?? []).toHaveLength(3)  // create + Enter + Save
  })
})

describe('#451 the name field no longer promises a path it refuses', () => {
  it('the placeholder does not offer auto-naming', () => {
    expect(CODE).not.toMatch(/name it later/i)
    expect(CODE).not.toMatch(/let the system name/i)
  })

  it('it still names the field', () => {
    expect(CODE).toContain('placeholder="Project name…"')
  })

  it('the validator the copy now agrees with is still there — the control', () => {
    // Both halves of the refusal. Were either dropped, the placeholder would be understating
    // rather than matching, and this rail would be asserting nothing.
    expect(CODE).toContain('disabled={!name.trim() || busy}')
    expect(CODE).toMatch(/if\s*\(!name\s*\|\|\s*busy\)\s*return/)
  })
})
