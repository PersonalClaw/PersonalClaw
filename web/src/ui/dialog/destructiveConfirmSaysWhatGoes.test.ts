import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { confirmDelete } from './index'
import { closeDialog, subscribeDialogs } from './dialogStore'
import { rowSubject } from '../../lib/rowSubject'

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
    expect(src, 'converged onto the canonical helper').toContain("confirmDelete('episodic memory'")
    expect(src, 'and no longer hand-rolls the same dialog').not.toMatch(
      /confirm\(\{\s*title: 'Delete this episodic memory\?'/,
    )
    // The siblings that already did the right thing must stay that way.
    expect(src).toContain("confirmDelete('memory'")
    expect(src).toContain("confirmDelete('lesson'")
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

// ── …and it names WHICH item ────────────────────────────────────────────────────────────────────────
//
// `confirmDelete`'s second argument is optional, and the difference it makes is the whole dialog:
//
//     confirmDelete('lesson', 'Prefer …')   →  Delete lesson "Prefer …"?
//     confirmDelete('lesson')               →  Delete this lesson?
//
// "This lesson" is only ever as specific as the click that opened it, and the dialog exists precisely
// because a click can be wrong. Measured across the tree: **21 call sites, and three omitted the name
// while holding one** — `MemoryPanel`'s episodic and lesson branches (whose FACT sibling ten lines up
// has always passed `selected.fact.key`) and `TaskDetail`'s comment delete, called from inside
// `comments.map` with the comment in hand.
//
// 🔑 THE SUBJECT IS PROSE, SO IT GOES THROUGH `rowSubject`. An episodic memory is a sentence, a lesson
// is a rule, a comment is a paragraph — none is a name, and a dialog title cannot carry 400 characters
// of one. `rowSubject(parts, 40)` is the app's own answer and `KnowledgeListPage` already uses it for
// exactly this job. It also degrades correctly: an empty subject returns `''`, which is falsy, so the
// helper falls back to "this <entity>" rather than rendering `Delete lesson ""?`.

describe('a destructive confirm names the item', () => {
  /** The balanced `(…)` argument list starting at `i`. */
  function callAt(src: string, i: number): string {
    let depth = 0
    for (let j = i; j < Math.min(src.length, i + 600); j++) {
      if (src[j] === '(') depth++
      else if (src[j] === ')') {
        depth--
        if (depth === 0) return src.slice(i + 1, j)
      }
    }
    return src.slice(i + 1, i + 600)
  }

  /** Top-level commas only — `rowSubject([a, b], 40)` is ONE argument. */
  function arity(args: string): number {
    if (!args.trim()) return 0
    let depth = 0
    let n = 1
    for (const ch of args) {
      if ('([{'.includes(ch)) depth++
      else if (')]}'.includes(ch)) depth--
      else if (ch === ',' && depth === 0) n++
    }
    return n
  }

  /** 🪤 Comments stripped first: an earlier rail in this repo matched the comment explaining its own
   *  absence, and the prose above this very `describe` contains a one-argument `confirmDelete(`. */
  function code(abs: string): string {
    return readFileSync(abs, 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
  }

  function calls(): Array<{ file: string; args: string }> {
    const out: Array<{ file: string; args: string }> = []
    for (const abs of walk(SRC)) {
      const src = code(abs)
      for (const m of src.matchAll(/\bconfirmDelete\(/g)) {
        out.push({
          file: abs.replace(SRC + '/', ''),
          args: callAt(src, src.indexOf('(', m.index!)),
        })
      }
    }
    return out
  }

  it('NO caller omits the subject — the ratchet', () => {
    // 🪤 Keyed on the population and counted, not spot-checked: the compliant/total shape is what makes
    // a dropped argument visible. A sweep that iterates its own matches and asks "is this one fine?"
    // never visits the one that went away.
    const all = calls()
    expect(all.length, 'the sweep must find the call sites').toBeGreaterThanOrEqual(21)
    const nameless = all.filter((c) => arity(c.args) < 2).map((c) => `${c.file}: confirmDelete(${c.args})`)
    expect(nameless, 'a delete dialog that asks about "this <thing>" identifies nothing').toEqual([])
  })

  it('the three prose subjects converge on rowSubject, not a hand-rolled slice', () => {
    const memory = readFileSync(join(SRC, 'pages', 'settings', 'MemoryPanel.tsx'), 'utf8')
    expect(memory).toContain("confirmDelete('episodic memory', rowSubject([selected.episodic.text], 40))")
    expect(memory).toContain("confirmDelete('lesson', rowSubject([selected.lesson.rule], 40))")
    expect(memory, 'imported, not redefined').toMatch(/import \{ rowSubject \} from '\.\.\/\.\.\/lib\/rowSubject'/)
    const task = readFileSync(join(SRC, 'pages', 'tasks', 'TaskDetail.tsx'), 'utf8')
    expect(task).toContain("confirmDelete('comment', rowSubject([body], 40))")
    // A local `.slice(0, n)` would re-answer a question the helper already answers — and would skip its
    // whitespace collapsing, which matters most here: a comment body carries the newlines it was typed
    // with, and a `\n\n` inside a 40-character budget is spent on nothing.
    for (const c of calls()) {
      expect(c.args, `${c.file} truncates its own subject`).not.toMatch(/\.slice\(\s*0\s*,/)
    }
  })

  it('TaskDetail both takes the comment AND is passed it', () => {
    // 🪤 TWO LINKS, and declaring the parameter is the worthless half. A previous cycle in this repo
    // added an optional label to a row component, shipped it, and changed nothing — every call site
    // still omitted it, so the value was `undefined` at runtime while the signature looked correct.
    // 🪤 …and read through `code()`, because the FIRST draft of the last assertion below failed on the
    // comment four lines above the fix explaining what the old call looked like. Same trap this repo
    // has hit before; the rule is about code, so the scan has to be too.
    const src = code(join(SRC, 'pages', 'tasks', 'TaskDetail.tsx'))
    expect(src, 'the signature takes it').toMatch(/async function remove\(commentId: string, body: string\)/)
    expect(src, 'and the call site supplies it from the row it is rendering').toContain('remove(c.id, c.body)')
    expect(src, 'no caller left on the old one-argument form').not.toMatch(/remove\(c\.id\)/)
  })

  it('the helper still degrades to "this <entity>" on an empty subject', () => {
    // The fallback is why `rowSubject` returning `''` is safe rather than a bug: an episodic memory
    // with blank text asks "Delete this episodic memory?", not `Delete episodic memory ""?`.
    const helper = readFileSync(join(SRC, 'ui', 'dialog', 'index.ts'), 'utf8')
    expect(helper).toContain('const label = name ? `${entity} "${name}"` : `this ${entity}`')
  })
})

describe('the sentence the dialog actually shows', () => {
  /** Open a dialog, read the request the store received, dismiss it. This drives the REAL helper
   *  rather than restating its template — a test that re-implements `Delete ${entity} "${name}"?`
   *  passes forever after the helper changes. */
  async function titleOf(open: () => Promise<boolean>): Promise<{ title: string; body: unknown }> {
    let seen: { id: number; title: string; body: unknown } | undefined
    const stop = subscribeDialogs((ds) => {
      const top = ds[ds.length - 1]
      if (top) seen = { id: top.id, title: top.title, body: top.body }
    })
    const p = open()
    if (!seen) throw new Error('no dialog opened')
    closeDialog(seen.id, false)
    await p
    stop()
    return { title: seen.title, body: seen.body }
  }

  it('names a lesson by its rule, which is what the delete call takes as identity', async () => {
    const rule = 'Prefer an explicit timeout over the default retry when a worker calls the gateway'
    const { title, body } = await titleOf(() => confirmDelete('lesson', rowSubject([rule], 40)))
    expect(title).toBe('Delete lesson "Prefer an explicit timeout over the def…"?')
    expect(body, 'and the helper still supplies the consequence').toBe('This cannot be undone.')
  })

  it('names an episodic memory by its text', async () => {
    const text = 'Asked me to stop summarising the roadmap and just take the next atom'
    const { title } = await titleOf(() => confirmDelete('episodic memory', rowSubject([text], 40)))
    expect(title).toBe('Delete episodic memory "Asked me to stop summarising the roadma…"?')
  })

  it('names a comment by its body, on one line even when it was typed on several', async () => {
    // 🔑 This is the case a `.slice()` gets wrong: the raw body starts with a newline-separated
    // paragraph, and the visible row is one line, so the dialog must be too.
    const commentBody = 'Blocked on the\n\n  ledger extraction landing first — see #1288'
    const { title } = await titleOf(() => confirmDelete('comment', rowSubject([commentBody], 40)))
    expect(title).toBe('Delete comment "Blocked on the ledger extraction landin…"?')
    expect(title, 'no newline reaches the title').not.toMatch(/\n/)
  })

  it('the header can hold the longer title — it wraps, it does not clip', () => {
    // 🔑 The one risk a string assertion cannot see. Naming the item takes the title from
    // "Delete this episodic memory?" (28 chars) to ~66, so the question is what the header does with
    // the overflow. It wraps: the title has no truncation and the card has no height cap, so a
    // two-line title just makes the sheet taller. Asserted rather than reasoned-about in a PR comment,
    // because a later `truncate` added here would silently clip the subject this cycle introduced —
    // and clipping it is worse than never naming it, since the user would not know it was cut.
    const shell = readFileSync(join(SRC, 'ui', 'dialog', 'DialogShell.tsx'), 'utf8')
    const titleLine = shell.split('\n').find((l) => l.includes('data-type="title-l"')) ?? ''
    expect(titleLine, 'the title line must be found').toContain('{title}')
    expect(titleLine, 'no truncation on a title that now carries the subject').not.toMatch(
      /truncate|whitespace-nowrap|line-clamp/,
    )
    expect(shell, 'and its column can shrink to wrap inside the sheet').toContain('min-w-0 flex-1')
    // Same title is the dialog's accessible name, so naming the item fixes the announcement too.
    expect(shell).toContain("aria-label={typeof title === 'string' ? title : undefined}")
  })

  it('falls back to "this <entity>" when there is genuinely nothing to name', async () => {
    const { title } = await titleOf(() => confirmDelete('lesson', rowSubject([''])))
    expect(title).toBe('Delete this lesson?')
  })
})
