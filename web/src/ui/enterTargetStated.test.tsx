import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── If Enter picks something, the surface has to say WHICH something ────────────────────────────
//
// Two palettes bind Enter to "the first match" as a convenience, and both did it invisibly. The
// handlers even document the intent — "Enter picks the first match — the common search → Enter flow"
// and "Enter opens (navigates into) the first matching folder" — but the intent was only ever in the
// source. On screen, nothing distinguished the row Enter would act on, so the only way to learn what
// the key did was to press it and watch something happen.
//
// 🪤 THE FIX IS A STATEMENT, NOT A HIGHLIGHT. Marking row 0 as "active" would promise a cursor these
// lists do not have: there are no arrow keys, and Tab reaches the rows as ordinary buttons — at which
// point Enter picks the FOCUSED row, not the first one, and a highlight on row 0 would be lying. So the
// hint names the FIELD's behaviour, in `CommandPalette`'s footer idiom (the same `CornerDownLeft`).
//
// 🪤 AND ONE CANDIDATE WAS FALSIFIED: `pages/tasks/TaskForm.tsx` also binds Enter, but it SUBMITS the
// form it is part of (`createProject()` / `createList()`) rather than picking an item out of a list.
// That is the standard behaviour of a text field in a form and needs no hint; pinned below so the
// census does not grow to include it.

const SRC = join(process.cwd(), 'src')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

/** Files whose Enter handler acts on the FIRST element of a collection. */
function picksFirstOnEnter(): string[] {
  const out: string[] = []
  for (const abs of walk(SRC)) {
    const lines = strip(readFileSync(abs, 'utf8')).split('\n')
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].includes("key === 'Enter'")) continue
      if (/\[0\]|\.at\(0\)/.test(lines.slice(i, i + 3).join('\n'))) { out.push(abs.replace(SRC + '/', '')); break }
    }
  }
  return out
}

describe('an Enter shortcut states its target', () => {
  it('the census is exactly the two palettes', () => {
    // Keyed on the behaviour, so a third palette adopting the shortcut lands here rather than shipping
    // the same silence.
    expect(picksFirstOnEnter().sort()).toEqual([
      'pages/chat/PromptPalette.tsx',
      'pages/code/WorkspacePicker.tsx',
    ])
  })

  it('each one says what Enter will do, with the same glyph the palette uses', () => {
    for (const rel of picksFirstOnEnter()) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel} must state the target`).toMatch(/<CornerDownLeft size=\{11\} \/> (picks|opens) the first/)
      // Gated on there being a match to pick: the sentence is false on an empty list.
      expect(src, `${rel} must not claim a target when the list is empty`)
        .toMatch(/(?:!!filtered\?\.length|shownDirs\.length > 0) && \(?/)
    }
  })

  it('the wording matches what the handler actually does', () => {
    // 🪤 THE DRIFT THAT MATTERS: a hint is a claim about behaviour, so it has to be checked against the
    // behaviour. If a handler later picks the ACTIVE row instead of the first, this fails rather than
    // leaving a confidently wrong sentence on screen.
    const prompt = strip(readFileSync(join(SRC, 'pages/chat/PromptPalette.tsx'), 'utf8'))
    expect(prompt, 'picks filtered[0]').toMatch(/key === 'Enter'[\s\S]{0,120}pick\(filtered\[0\]\)/)
    expect(prompt).toContain('picks the first match')
    const ws = strip(readFileSync(join(SRC, 'pages/code/WorkspacePicker.tsx'), 'utf8'))
    expect(ws, 'browses shownDirs[0]').toMatch(/key === 'Enter'[\s\S]{0,120}browse\(shownDirs\[0\]\.path\)/)
    expect(ws, 'and says so in the verb that surface uses').toContain('opens the first folder')
  })

  it('a form SUBMIT on Enter is not this defect — the falsified candidate', () => {
    const src = strip(readFileSync(join(SRC, 'pages/tasks/TaskForm.tsx'), 'utf8'))
    expect(src, 'it binds Enter').toMatch(/key === 'Enter'/)
    expect(src, 'but to submit, not to pick out of a list').toMatch(/isProject \? createProject\(\) : createList\(\)/)
    expect(src, 'so it indexes nothing').not.toMatch(/key === 'Enter'[\s\S]{0,120}\[0\]/)
  })
})

// ── and the sentence a user actually reads ──────────────────────────────────────────────────────

vi.mock('../lib/api', () => ({
  api: { prompts: vi.fn(async () => [
    { name: 'plan', title: 'Plan a change', description: 'first', variables: [] },
    { name: 'review', title: 'Review a diff', description: 'second', variables: [] },
  ]) },
}))

describe('PromptPalette renders the hint', () => {
  beforeEach(() => vi.clearAllMocks())

  it('tells the user what Enter picks, once the list has arrived', async () => {
    const { PromptPalette } = await import('../pages/chat/PromptPalette')
    render(<PromptPalette onInsert={() => {}} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Plan a change')).toBeTruthy())
    expect(screen.getByText(/picks the first match/), 'the hint a keyboard user needs').toBeTruthy()
  })

  it('adds the escape hint only once there is a search to clear', async () => {
    const { PromptPalette } = await import('../pages/chat/PromptPalette')
    render(<PromptPalette onInsert={() => {}} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Plan a change')).toBeTruthy())
    expect(screen.queryByText(/esc clears the search/), 'nothing to clear yet').toBeNull()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'plan' } })
    expect(screen.getByText(/esc clears the search/)).toBeTruthy()
  })

  it('says nothing about a first match when nothing matches', async () => {
    const { PromptPalette } = await import('../pages/chat/PromptPalette')
    render(<PromptPalette onInsert={() => {}} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Plan a change')).toBeTruthy())
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzzz' } })
    expect(screen.queryByText(/picks the first match/), 'the claim would be false here').toBeNull()
  })
})
