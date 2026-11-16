import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { InboxItem } from '../../lib/api'
import { ProposalsLens } from './ProposalsLens'
import { groupKey } from './proposalLens'

// ── The Proposals view names a learning row by its tag ──────────────────────────────────────────
//
// A learning proposal's row read "Installs a skill" whatever it was, a template or a lesson. It
// takes its card's tag now, the registry's words Needs you's card says, links to its panel there,
// and batches only with rows of its own variant.

const FIXTURE = JSON.parse(readFileSync(join(process.cwd(), 'src/test/fixtures/needsYouKinds.json'), 'utf8')) as {
  learning: { variant: string; card: Record<string, unknown> }[]
}
const cardOf = (variant: string) => FIXTURE.learning.find((e) => e.variant === variant)!.card

const row = (id: string, variant: string): InboxItem => ({
  id, channel: '', channel_name: '', message: String(cardOf(variant).headline), sender_id: '', sender_name: '', item_kind: 'proposal', status: 'pending',
  refs: {
    learning_proposal: `lp-${id}`, card: cardOf(variant),
    proposal: { title: String(cardOf(variant).headline), preview: '', preview_kind: 'text', provenance: 'learning', editable: false, apply: { skill_promotion: { pid: `lp-${id}` } } },
  },
} as unknown as InboxItem)

describe('a learning row in the Proposals view', () => {
  it('says its kind by its tag, not "Installs a skill", and opens its panel in Needs you', () => {
    render(<ProposalsLens items={[row('a', 'template.named'), row('b', 'lesson.failed_run')]} onChanged={() => {}} />)
    expect(screen.getByText('· New template')).toBeInTheDocument()
    expect(screen.getByText('· Lesson')).toBeInTheDocument()
    expect(screen.queryByText('· Installs a skill')).toBeNull()
    expect(screen.getAllByRole('link', { name: 'Review it in Needs you' })[0]).toHaveAttribute('href', `#/mission-control?open=${encodeURIComponent('note:a')}`)
  })

  it('batches only with its own variant', () => {
    expect(groupKey(row('a', 'template.named'))).not.toBe(groupKey(row('b', 'lesson.failed_run')))
    expect(groupKey(row('a', 'template.named'))).toBe(groupKey(row('c', 'template.named')))
  })
})
