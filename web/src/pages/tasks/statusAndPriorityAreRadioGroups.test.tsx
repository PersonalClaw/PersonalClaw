import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { TaskForm } from './TaskForm'
import { STATUSES, PRIORITIES } from './taskMeta'

// ── #3472 Status and Priority are radio groups, not a tablist with no tabpanels ────────────
//
// MEASURED in the task-creation form: Status and Priority declared `role="tab"` inside a
// `role="tablist"`, and there were no tabpanels — so a screen reader announced **"tab 2 of 6"**
// for what is semantically one field with six mutually exclusive values. The user was told they
// were navigating a tabbed interface that does not exist, and told nothing about the values
// being alternatives of one another. `tablist` also carries a promise of manual activation and
// of a revealed panel, and neither ever held.
//
// 🔴 THE CENSUS IS WHAT MAKES IT A PRIMITIVE FIX RATHER THAN TWO CALL SITES. Both fields are
// `ui/Segmented`, which had **50 call sites and 0 `role="tabpanel"` anywhere in the app** — the
// only two tabpanels are in `pages/chat/ChatActivityPanel`, which does not use Segmented. So
// every adoption announced the same thing that was not true, including sixteen other genuine
// single-choice FIELDS (schedule When/Runs, trigger type, approval mode, granularity, project
// kind, reclassify, remember-scope, …). Fixing the two call sites would have left those.
//
// 🪤 AND IT BROKE TEST AUTHORING IN A WAY THAT HID THE PROBLEM, which is how it survived fifty
// adoptions. `getByRole('button', { name: 'High' })` TIMES OUT against a `role="tab"`, so the
// next author reaches for `getByRole('tab')` and thereby encodes the wrong semantics as correct.
// Twenty-four such queries existed across fourteen files. §3 is the rail that stops the next one.
//
// 🔑 The keyboard handler did not change. A radiogroup's arrow model is "move AND select", which
// is what `Segmented` already did; a tablist's is manual activation. The keys were already a
// radiogroup's — only the words announced to the user said otherwise.

const draft = { title: 'A task', status: 'blocked', priority: 'high' }

/** The form, with both hosts' shared props. `allTasks` empties the dependency picker. */
const renderForm = () =>
  render(<TaskForm draft={draft} onChange={vi.fn()} allTasks={[]} />)

/** A field's group, resolved the way assistive tech does — by role and accessible name. The
 *  `Field` publishes its visible label and `Segmented` claims it, so this query IS the
 *  announcement under test: if the name stops resolving, the group went anonymous. */
const group = (name: string) => screen.getByRole('radiogroup', { name })

describe('§1 the task form announces Status and Priority as single fields', () => {
  it('Status is a radiogroup named by its visible label', () => {
    renderForm()
    expect(group('Status')).toBeInTheDocument()
  })

  it('Priority is a radiogroup named by its visible label', () => {
    renderForm()
    expect(group('Priority')).toBeInTheDocument()
  })

  it('and neither announces a tab, in a form that has no tabpanels', () => {
    const { container } = renderForm()
    expect(screen.queryAllByRole('tab'), 'a field is not a tab strip').toEqual([])
    expect(container.querySelectorAll('[role="tablist"]')).toHaveLength(0)
    // The floor that makes the two assertions above mean something: there is no panel for a tab
    // to control, which is the whole reason `tablist` was the wrong vocabulary.
    expect(container.querySelectorAll('[role="tabpanel"]')).toHaveLength(0)
  })
})

describe('§2 each option is a radio that says whether it is chosen', () => {
  it('Status offers one radio per status, and exactly one is checked', () => {
    renderForm()
    const radios = within(group('Status')).getAllByRole('radio')
    expect(radios.map((r) => r.textContent?.trim()))
      .toEqual(STATUSES.map((s) => s.label))
    expect(radios.filter((r) => r.getAttribute('aria-checked') === 'true')).toHaveLength(1)
  })

  it('and the checked one is the draft value, not the first option', () => {
    renderForm()
    // `draft.status` is `blocked` — the third status. A control that reported the first option as
    // chosen would satisfy "exactly one is checked" above.
    expect(within(group('Status')).getByRole('radio', { name: 'Blocked' })
      .getAttribute('aria-checked')).toBe('true')
    expect(within(group('Status')).getByRole('radio', { name: 'Not started' })
      .getAttribute('aria-checked')).toBe('false')
  })

  it('Priority likewise', () => {
    renderForm()
    const radios = within(group('Priority')).getAllByRole('radio')
    expect(radios.map((r) => r.textContent?.trim())).toEqual(PRIORITIES.map((p) => p.label))
    expect(within(group('Priority')).getByRole('radio', { name: 'High' })
      .getAttribute('aria-checked')).toBe('true')
  })

  it('only the checked option is a tab stop (the roving tabindex survives)', () => {
    renderForm()
    const radios = within(group('Status')).getAllByRole('radio')
    expect(radios.filter((r) => r.getAttribute('tabindex') === '0')).toHaveLength(1)
    expect(radios.filter((r) => r.getAttribute('tabindex') === '-1')).toHaveLength(radios.length - 1)
  })

  it('picking one reports the new value', () => {
    const onChange = vi.fn()
    render(<TaskForm draft={draft} onChange={onChange} allTasks={[]} />)
    fireEvent.click(within(group('Priority')).getByRole('radio', { name: 'Low' }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ priority: 'low' }))
  })

  it('and an arrow key both moves and selects — the radiogroup model', () => {
    // Not a tablist's manual activation. This is the behaviour that was already correct before
    // the roles were, and asserting it here is what stops a future "make it a proper tablist"
    // change from silently switching the keyboard contract.
    const onChange = vi.fn()
    render(<TaskForm draft={draft} onChange={onChange} allTasks={[]} />)
    fireEvent.keyDown(within(group('Priority')).getByRole('radio', { name: 'High' }),
      { key: 'ArrowRight' })
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ priority: 'medium' }))
  })
})

// ── §3 the tree-wide rail ──────────────────────────────────────────────────────────────────

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) { out.push(...walk(abs)); continue }
    out.push(abs)
  }
  return out
}

/** Comments blanked, newlines kept. Three separate rails in this change went red on their own
 *  prose before this was added — `Segmented` now DOCUMENTS the roles it gave up, so any scan
 *  that reads raw source counts the sentence explaining the fix as an instance of the defect. */
const codeOf = (abs: string) =>
  readFileSync(abs, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^[ \t]*\/\/.*$/gm, '')

describe('§3 no single-choice field borrows tab semantics again', () => {
  it('the shared primitive is a radiogroup', () => {
    const seg = codeOf(join(SRC, 'ui/Segmented.tsx'))
    expect(seg).toMatch(/role="radiogroup"/)
    expect(seg).toMatch(/role="radio"/)
    expect(seg).toMatch(/aria-checked=\{on\}/)
    expect(seg, 'a field must not announce a tab').not.toMatch(/role="tab(list)?"/)
    expect(seg, 'nor a tab selection state').not.toMatch(/aria-selected/)
  })

  it('and every tab role left in the tree belongs to a strip that reveals a pane', () => {
    // The sweep the issue's scope note asks for. These six are hand-rolled view switchers, each
    // of which swaps the pane beside it; `Segmented` is deliberately not among them.
    const declaring = walk(SRC)
      .filter((abs) => /\.tsx$/.test(abs) && !/\.(test|doc)\.tsx$/.test(abs))
      .filter((abs) => /role="tab"/.test(codeOf(abs)))
      .map((abs) => abs.slice(SRC.length + 1))
      .sort()
    expect(declaring).toEqual([
      'pages/chat/ChatActivityPanel.tsx',
      'pages/files/FilesSection.tsx',
      'pages/loops/LoopCockpitPage.tsx',
      'pages/settings/MemoryPanel.tsx',
      'pages/terminal/TerminalDrawer.tsx',
      'pages/terminal/TerminalPage.tsx',
    ])
  })

  it('and no test queries a Segmented option as a tab', () => {
    // 🪤 THE RAIL THAT MATTERS MOST, because the wrong markup taught the wrong query. A
    // `getByRole('tab')` against a Segmented is how the defect reproduced itself across fourteen
    // files, and a green suite full of them is what made fifty adoptions look fine.
    //
    // 🪤 BOTH QUERY FORMS, because the first draft only matched `getByRole` and read **13** where
    // the truth was 14: `pages/workflows/refinerTabs.test.tsx` reaches for the same wrong semantics
    // through `container.querySelectorAll('[role="tab"]')`. A census of a bad habit has to cover
    // every idiom the habit is expressed in, or it certifies the ones it cannot see.
    const offenders = walk(SRC)
      .filter((abs) => /\.test\.tsx?$/.test(abs))
      .filter((abs) => {
        const code = codeOf(abs)
        const queries = /Role\((['"])tab(list)?\1/.test(code)
          || /\[role=\\?["']tab(list)?\\?["']\]/.test(code)
        if (!queries) return false
        // The six hand-rolled strips have their own tests, which legitimately query tabs.
        return !/ChatActivityPanel|FilesSection|LoopCockpitPage|MemoryPanel|TerminalPage|TerminalDrawer|tabListKeys/
          .test(code + abs)
      })
      .map((abs) => abs.slice(SRC.length + 1))
      .sort()
    expect(offenders, 'these query a tab role outside the six real tab strips').toEqual([])
  })
})
