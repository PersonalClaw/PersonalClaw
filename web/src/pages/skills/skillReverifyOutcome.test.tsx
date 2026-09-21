import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, type SkillIntegrity, type SkillItem } from '../../lib/api'
import { SkillInspector } from './SkillInspector'

const source = readFileSync(join(process.cwd(), 'src/pages/skills/SkillInspector.tsx'), 'utf8')

function skill(name: string, integrity: SkillItem['integrity'] = 'intact'): SkillItem {
  return {
    key: name,
    name,
    description: `${name} description`,
    always: false,
    path: `/skills/${name}`,
    source: 'local',
    type: 'installed',
    loaded_by_agents: [],
    integrity,
  }
}

function mount(item: SkillItem) {
  vi.spyOn(api, 'skillFiles').mockResolvedValue({ name: item.name, files: [] })
  render(<SkillInspector skill={item} onDeleted={() => {}} />)
}

const cleanResult: SkillIntegrity = {
  name: 'clean-skill',
  integrity: 'intact',
  ok: true,
  unlocked: false,
  mutated: [],
  missing: [],
  added: [],
  summary: 'No drift detected',
}

afterEach(() => vi.restoreAllMocks())

describe('SkillInspector re-verification outcome', () => {
  it('starts idle, with no outcome line beside Re-verify', () => {
    const verify = vi.spyOn(api, 'verifySkill')
    mount(skill('idle-skill'))

    expect(source).not.toContain('useState<SkillIntegrity | null>(null)')
    expect(source).toContain("useState<ReverifyOutcome>({ kind: 'idle' })")
    expect(verify).not.toHaveBeenCalled()
    expect(screen.queryByText(/^checked /i)).toBeNull()
  })

  it('shows the server message on failure without replacing install-time integrity', async () => {
    vi.spyOn(api, 'verifySkill').mockRejectedValue(new Error('baseline file is unreadable'))
    mount(skill('failed-skill', 'intact'))

    fireEvent.click(screen.getByRole('button', { name: /Re-verify/i }))

    await waitFor(() => expect(screen.getByText('baseline file is unreadable')).toBeTruthy())
    expect(screen.getByText('Verified — matches install baseline')).toBeTruthy()
  })

  it('shows when a clean check completed and deliberately omits the duplicate summary', async () => {
    const checkedAt = Date.parse('2026-09-21T01:23:00Z')
    vi.spyOn(Date, 'now').mockReturnValue(checkedAt)
    vi.spyOn(api, 'verifySkill').mockResolvedValue(cleanResult)
    mount(skill('clean-skill', 'tampered'))

    fireEvent.click(screen.getByRole('button', { name: /Re-verify/i }))

    const time = new Date(checkedAt).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    await waitFor(() => expect(screen.getByText(`checked ${time}`)).toBeTruthy())
    expect(screen.getByText('Verified — matches install baseline')).toBeTruthy()
    expect(screen.queryByText(cleanResult.summary)).toBeNull()
  })
})
