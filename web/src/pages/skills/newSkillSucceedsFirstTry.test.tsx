import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { noBaselineReason, withFrontmatterName } from './skillMeta'

// ── B7 (day-7 validation, c1b-121): "New skill" failed on the first try, every time ─────────────
//
// The dialog's SKILL.md template carried a literal `name: my-skill` that nothing updated, and the
// server binds that line to the key the Name field sends (`skills/loader.py:validate_skill_md`). So
// the ONLY default path — type a name, press Create — answered, measured live:
//
//   400 "SKILL.md validation failed: SKILL.md frontmatter name must match skill key
//        'q4-release-checklist' (got 'my-skill')"
//
// and it worked only after the user edited the frontmatter by hand. The Name field is the skill's
// one identity (its directory, and the id every skills route addresses), so the template's `name:`
// now FOLLOWS it. These mount the real dialog and read what Create actually sends — a helper that
// produced the right text while the dialog never called it would fail here.

const NAME = 'q4-release-checklist'

/** The exact bytes the dialog must send for NAME when nothing but the Name field was filled in. */
const DEFAULT_BODY = `---
name: ${NAME}
description: One line on when this skill should load.
---

# My skill

Instructions the agent follows when this skill is active.
`

const createSkill = vi.fn<(name: string, content: string) => Promise<{ ok: boolean }>>()

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    skills: () => Promise.resolve([]),
    skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
    learningSummary: () => Promise.resolve(null),
    skillFiles: () => Promise.resolve({ files: [] }),
    createSkill: (name: string, content: string) => createSkill(name, content),
  },
}))

/** The page under a real (in-memory) URL, so `?create=1` closing is an observable transition. */
async function mountDialog() {
  const { SkillsPage } = await import('./SkillsPage')
  function Harness() {
    const [query, setQ] = useState<Record<string, string>>({ create: '1' })
    const setQuery = (patch: Record<string, string | null | undefined>) => setQ((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
      return next
    })
    return <SkillsPage query={query} setQuery={setQuery} />
  }
  render(<Harness />)
  return {
    name: await screen.findByRole('textbox', { name: 'Skill name' }),
    body: screen.getByRole('textbox', { name: 'Skill definition (SKILL.md)' }) as HTMLTextAreaElement,
  }
}

beforeEach(() => {
  createSkill.mockReset()
  createSkill.mockResolvedValue({ ok: true })
  sessionStorage.clear()
})

describe('New skill: filling in only the Name succeeds on the first try', () => {
  it('sends a body whose frontmatter names the skill it is creating', async () => {
    const user = userEvent.setup()
    const { name } = await mountDialog()

    await user.type(name, NAME)
    await user.click(screen.getByRole('button', { name: /Create skill/ }))

    await waitFor(() => expect(createSkill).toHaveBeenCalledTimes(1))
    expect(createSkill).toHaveBeenCalledWith(NAME, DEFAULT_BODY)
    // The literal the dialog used to ship, which the server refused against every name but itself.
    expect(createSkill.mock.calls[0][1]).not.toContain('name: my-skill')
  })

  it('closes the dialog once the create lands — the transition, not just the request', async () => {
    const user = userEvent.setup()
    const { name } = await mountDialog()
    await user.type(name, NAME)
    await user.click(screen.getByRole('button', { name: /Create skill/ }))
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Skill name' })).toBeNull())
  })

  it('shows the name it will send while the user types, so the dialog shows the real bytes', async () => {
    const user = userEvent.setup()
    const { name, body } = await mountDialog()
    // Before anything is typed there is no name to follow — and no stand-in that could be sent.
    expect(body.value).toMatch(/^---\nname:\ndescription:/)
    await user.type(name, 'draft')
    expect(body.value).toMatch(/^---\nname: draft\n/)
    await user.clear(name)
    await user.type(name, NAME)
    expect(body.value.split('\n')[1]).toBe(`name: ${NAME}`)
  })

  it('keeps what the user wrote in the body when the name changes afterwards', async () => {
    const user = userEvent.setup()
    const { name, body } = await mountDialog()
    await user.type(name, 'first')
    await user.clear(body)
    await user.type(body, '---{Enter}name: first{Enter}description: Walk the Q4 checklist.{Enter}---{Enter}{Enter}Steps.')
    await user.clear(name)
    await user.type(name, NAME)
    await user.click(screen.getByRole('button', { name: /Create skill/ }))

    await waitFor(() => expect(createSkill).toHaveBeenCalledTimes(1))
    expect(createSkill).toHaveBeenCalledWith(NAME, `---\nname: ${NAME}\ndescription: Walk the Q4 checklist.\n---\n\nSteps.`)
  })

  it('refuses a name the server would silently shorten, before sending anything', async () => {
    // The server's key sanitizer strips a leading/trailing dash, so `my-skill-` would be stored as
    // `my-skill` and then refused against its own `name: my-skill-` — a first-try failure again.
    const user = userEvent.setup()
    const { name } = await mountDialog()
    await user.type(name, 'my-skill-')
    await user.click(screen.getByRole('button', { name: /Create skill/ }))
    expect(await screen.findByText(/starting and ending with a letter or digit/)).toBeTruthy()
    expect(createSkill).not.toHaveBeenCalled()
  })
})

describe('withFrontmatterName', () => {
  it('rewrites the name line inside the frontmatter and nothing outside it', () => {
    const src = '---\nname: old\ndescription: d\n---\n\nname: not frontmatter\n'
    expect(withFrontmatterName(src, 'new')).toBe('---\nname: new\ndescription: d\n---\n\nname: not frontmatter\n')
  })

  it('rewrites EVERY top-level name line, because the server keeps the last one', () => {
    expect(withFrontmatterName('---\nname: a\nname: b\ndescription: d\n---\n', 'k'))
      .toBe('---\nname: k\nname: k\ndescription: d\n---\n')
  })

  it('adds the line when the frontmatter has none, and leaves an indented key alone', () => {
    expect(withFrontmatterName('---\ndescription: d\nmeta:\n  name: nested\n---\nbody', 'k'))
      .toBe('---\nname: k\ndescription: d\nmeta:\n  name: nested\n---\nbody')
  })

  it('keeps CRLF line endings', () => {
    expect(withFrontmatterName('---\r\nname: a\r\ndescription: d\r\n---\r\n', 'k'))
      .toBe('---\r\nname: k\r\ndescription: d\r\n---\r\n')
  })

  it('leaves a body with no closed frontmatter untouched — the server names that problem', () => {
    expect(withFrontmatterName('# just markdown\n', 'k')).toBe('# just markdown\n')
    expect(withFrontmatterName('---\nname: a\nno closing fence', 'k')).toBe('---\nname: a\nno closing fence')
  })

  it('writes an empty name line for an empty Name, never a stand-in', () => {
    expect(withFrontmatterName('---\nname: x\ndescription: d\n---\n', '  ')).toBe('---\nname:\ndescription: d\n---\n')
  })
})

describe('noBaselineReason says where a skill came from when anything recorded it', () => {
  it('a skill created with New skill reads as created in the dashboard', () => {
    expect(noBaselineReason({ source: 'local', provenance: 'dashboard' })).toBe('created in the dashboard')
  })

  it('names the other recorded origins instead of guessing', () => {
    expect(noBaselineReason({ source: 'local', provenance: 'taught' })).toBe('taught in a session')
    expect(noBaselineReason({ source: 'local', provenance: 'auto' })).toBe('extracted from session activity')
    expect(noBaselineReason({ source: 'bundled', provenance: '' })).toBe('bundled with PersonalClaw')
  })

  it('keeps the either-or only where nothing is recorded (the vacuity control)', () => {
    expect(noBaselineReason({ source: 'local', provenance: '' })).toBe('bundled or hand-placed')
    expect(noBaselineReason({ source: 'local' })).toBe('bundled or hand-placed')
  })
})

describe('the inspector tells the truth about a dashboard-created skill', () => {
  it('reads "created in the dashboard", not "bundled or hand-placed"', async () => {
    const { SkillInspector } = await import('./SkillInspector')
    render(<SkillInspector
      skill={{ key: NAME, name: NAME, description: 'd', always: false, source: 'local', provenance: 'dashboard', type: 'installed', loaded_by_agents: [], integrity: 'unverified' }}
      onDeleted={() => {}} />)
    expect(screen.getByText(/Unverified — no install baseline \(created in the dashboard\)/)).toBeTruthy()
    expect(screen.queryByText(/bundled or hand-placed/)).toBeNull()
  })
})
