import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A destructive dialog must say what happens to what ─────────────────────────────────────────────
//
// The app has a strong canonical form and states it in code: `confirmDelete(entity, name?)` is
// "Convenience for the dominant pattern — a destructive delete confirmation", and it DEFAULTS the body
// to "This cannot be undone." Every hand-rolled danger dialog that supplies its own body says something
// better still, naming the blast radius in the user's words:
//
//     "The shelf goes away. The items on it stay in your library."
//     "Its context directory will be removed and its task lists detached. Workspace files on disk are
//      left untouched."
//     "In-flight steps are stopped. Completed work is kept."
//     "This deletes the folder and all its contents. This cannot be undone."
//
// Measured across the tree: **37 confirm dialogs, 19 of them `danger: true`, and exactly two said
// nothing beyond their title.**
//
//   `settings/MemoryPanel`      hand-rolled a dialog `confirmDelete` already produces, and lost its
//                               body doing so — while the two SIBLING deletes in the same function use
//                               the helper and do say "This cannot be undone".
//   `notifications/…Page`       'Clear all notifications?' on the most total destructive action in the
//                               app: `clear_notifications()` truncates the log on disk, and it ignores
//                               the active filter, so a user looking at 3 of 50 was told neither.
//
// 🪤 THE RULE IS "HAS A BODY", NOT "SAYS CANNOT BE UNDONE". Three earlier drafts of this sweep demanded
// irreversibility language and over-reported: a dismissed inbox item is restorable
// (`inbox/restoreFilteredItem.test.tsx`), and a cancelled run keeps its completed work — those dialogs
// are RIGHT to omit it. The property a destructive dialog owes the user is that it explains the
// consequence at all; which words are true is the author's call.

const SRC = join(process.cwd(), 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

/** The balanced `{…}` object literal starting at `i`. */
function objectAt(src: string, i: number): string {
  let depth = 0
  for (let j = i; j < Math.min(src.length, i + 1600); j++) {
    if (src[j] === '{') depth++
    else if (src[j] === '}') {
      depth--
      if (depth === 0) return src.slice(i, j + 1)
    }
  }
  return src.slice(i, i + 1600)
}

/** Every `confirm({…})` object literal in the tree, with its file. */
function dialogs(): Array<{ file: string; obj: string }> {
  const out: Array<{ file: string; obj: string }> = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    for (const m of src.matchAll(/confirm\(\s*\{/g)) {
      const brace = src.indexOf('{', m.index!)
      out.push({ file: abs.replace(SRC + '/', ''), obj: objectAt(src, brace) })
    }
  }
  return out
}

describe('a destructive confirm explains the consequence', () => {
  it('the canonical helper still supplies a body by default', () => {
    // Everything below leans on this: a `confirmDelete` caller is exempt because the helper covers it.
    const helper = readFileSync(join(SRC, 'ui', 'dialog', 'index.ts'), 'utf8')
    expect(helper).toContain("body: opts?.body ?? 'This cannot be undone.'")
    expect(helper, 'and it is still the danger-tinted path').toContain('danger: true')
  })

  it('EVERY danger dialog carries a body — the ratchet', () => {
    // 🪤 Keyed on the population (every `confirm({…})` with `danger: true`), never on the fixed form.
    const all = dialogs()
    expect(all.length, 'the sweep must find the dialogs').toBeGreaterThanOrEqual(30)
    const danger = all.filter((d) => /danger:\s*true/.test(d.obj))
    expect(danger.length, 'and the danger subset').toBeGreaterThanOrEqual(15)
    const bodyless = danger
      .filter((d) => !/\bbody\s*[:,]/.test(d.obj))
      .map((d) => `${d.file}: ${(/title: ([^\n]*)/.exec(d.obj)?.[1] ?? '?').slice(0, 48)}`)
    expect(bodyless, 'a destructive dialog that only asks "are you sure?" explains nothing').toEqual([])
  })

  it('the episodic-memory delete uses the helper its siblings use', () => {
    const src = readFileSync(join(SRC, 'pages', 'settings', 'MemoryPanel.tsx'), 'utf8')
    expect(src, 'converged onto the canonical helper').toContain("confirmDelete('episodic memory')")
    expect(src, 'and no longer hand-rolls the same dialog').not.toMatch(
      /confirm\(\{\s*title: 'Delete this episodic memory\?'/,
    )
    // The siblings that already did the right thing must stay that way.
    expect(src).toContain("confirmDelete('memory'")
    expect(src).toContain("confirmDelete('lesson')")
  })

  it('clear-all names the TOTAL, not the filtered view', () => {
    // The action truncates the whole log, so counting the filtered list would understate it — a user
    // looking at 3 of 50 would read the filter as a limit on the action.
    const src = readFileSync(join(SRC, 'pages', 'notifications', 'NotificationsPage.tsx'), 'utf8')
    expect(src).toContain('const total = items?.length ?? 0')
    expect(src, 'the count must come from the raw list, not `filtered`').not.toMatch(
      /const total = filtered/,
    )
    expect(src).toContain('`Clear all ${total} notification${total === 1 ? \'\' : \'s\'}?`')
    expect(src, 'and the body must say where they go').toMatch(
      /removed from disk[\s\S]{0,80}cannot be undone/,
    )
    expect(src, 'including the ones the filter is hiding').toContain('hidden by the current filter')
  })

  it('the dialogs that deliberately OMIT irreversibility keep doing so', () => {
    // Pinned, because three drafts of this sweep wanted to add "cannot be undone" to these and would
    // have been wrong: a dismissed inbox item can be restored, and a cancelled run keeps its work.
    const inbox = readFileSync(join(SRC, 'pages', 'inbox', 'InboxPage.tsx'), 'utf8')
    expect(inbox, 'dismiss-all already names its count').toMatch(/Dismiss all \$\{n\} pending item/)
    expect(inbox).not.toMatch(/Dismiss all[\s\S]{0,200}cannot be undone/)
    const run = readFileSync(join(SRC, 'pages', 'workflows', 'WorkflowRunDetail.tsx'), 'utf8')
    expect(run, 'cancel says what survives instead').toContain('Completed work is kept.')
  })
})
