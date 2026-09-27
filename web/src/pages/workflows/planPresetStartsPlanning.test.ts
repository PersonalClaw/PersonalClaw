/** "Plan a project" starts the planning workflow (F-61).
 *
 *  The card was the `general` KIND's card, and that kind resolves to `general-project` — "Iterate
 *  on any task in judged steps until it is genuinely done", the general loop. So a user who picked
 *  "Plan a project" got a loop working on their brief rather than a plan, while the bundled
 *  `project-planning` ("Turn a goal into a plan…") was offered nowhere. */
import { describe, expect, it } from 'vitest'
import { workflowPresets } from './workflowPresets'
import type { WorkflowDefSummary } from '../../lib/api'

const def = (name: string, description: string): WorkflowDefSummary =>
  ({ name, description, source: 'bundled', version: 1, tags: [], provider: 'bundled' })

const BUNDLED = [
  def('code-project', 'Ship a code change.'),
  def('deep-research', 'Research a topic.'),
  def('design-project', 'Design something.'),
  def('goal-pursuit-open-ended', 'Pursue a goal.'),
  def('general-project', 'Iterate on any task in judged steps until it is genuinely done.'),
  def('project-planning', 'Turn a goal into a plan.'),
]

describe('the preset cards', () => {
  it('🔑 "Plan a project" starts project-planning, not the general loop', () => {
    const plan = workflowPresets(BUNDLED).find((p) => p.title === 'Plan a project')
    expect(plan?.prefill).toBe('project-planning')
    expect(plan?.description).toBe('Turn a goal into a plan.') // the template's own words
  })

  it('the general loop keeps its card, under a title that says what it does', () => {
    const general = workflowPresets(BUNDLED).find((p) => p.prefill === 'general-project')
    expect(general?.title).toBe('Loop until done')
  })

  it('no card is offered for a planning template this install does not ship', () => {
    const presets = workflowPresets(BUNDLED.filter((d) => d.name !== 'project-planning'))
    expect(presets.find((p) => p.title === 'Plan a project')).toBeUndefined()
  })
})
