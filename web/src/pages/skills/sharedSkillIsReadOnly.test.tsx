import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { api, type SkillItem } from '../../lib/api'
import { SkillInspector } from './SkillInspector'

// ── A skill in the folder AI tools share is read, never edited or deleted from here ──────────────
//
// Once the owner lets PersonalClaw read `~/.agents/skills` (Settings → Security → Outside
// PersonalClaw's home), its skills are listed with `source: 'shared'`. That folder is outside the
// home, and the server refuses to change or delete anything in it, so the inspector must not offer
// Edit or Delete for one. It says why instead. A skill in the home keeps both.

function skill(name: string, source: string, type: string): SkillItem {
  return {
    key: name, name, description: `${name} description`, always: false,
    path: `/somewhere/${name}/SKILL.md`, source, type, loaded_by_agents: [], integrity: 'unverified',
  }
}

function mount(item: SkillItem) {
  vi.spyOn(api, 'skillFiles').mockResolvedValue({ name: item.name, files: [] })
  render(<SkillInspector skill={item} onDeleted={() => {}} />)
}

afterEach(() => vi.restoreAllMocks())

describe('a skill in the folder other AI tools share', () => {
  it('offers no Edit or Delete, and says it is only read', () => {
    mount(skill('theirs', 'shared', 'read-only'))
    expect(screen.queryByRole('button', { name: /edit skill\.md/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /delete skill/i })).toBeNull()
    expect(screen.getByText(/outside PersonalClaw.s home, so PersonalClaw only reads it/i)).toBeTruthy()
  })

  it('leaves a skill in the home editable and deletable', () => {
    mount(skill('mine', 'local', 'installed'))
    expect(screen.getByRole('button', { name: /edit skill\.md/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /delete skill/i })).toBeTruthy()
    expect(screen.queryByText(/only reads it/i)).toBeNull()
  })
})
