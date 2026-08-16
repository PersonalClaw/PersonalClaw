import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Sunrise, Moon } from 'lucide-react'
import { PresetCard, PresetEmptyState, type PresetDef } from './PresetEmptyState'

// ── The preset-first empty state (PEP-1) ──────────────────────────────────────────────
//
// An empty list is the one moment a newcomer has no model of what the surface makes, and
// the Triggers create flow they were being sent to opens on the whole ontology: four
// trigger kinds, ~15 lifecycle events (7 of which never fire), every action provider. So
// the empty state offers finished presets that SEED that same form.
//
// What this holds is the primitive's half of the contract:
//
//   1. N presets render as N cards, each showing the four things a preset IS (icon,
//      title, cadence line, description).
//   2. Picking one hands back that preset's `prefill` — the payload, not a bare id and
//      not nothing. This is the clause the whole atom turns on: without the prefill
//      reaching the caller there is no pre-filled create flow, only a differently-shaped
//      "New trigger" button.
//   3. A card is a KEYBOARD control: one tab stop, reachable by Tab, activatable by
//      Enter AND Space, with a visible focus ring and an accessible name that says what
//      it will do rather than reciting its own prose.
//   4. A card contains no nested interactive element. A button inside a button is
//      `nested-interactive` (axe, serious): assistive tech is told about one control and
//      then handed two.

interface Prefill { cron: string; label: string }

const PRESETS: PresetDef<Prefill>[] = [
  {
    id: 'morning-briefing',
    icon: Sunrise,
    title: 'Morning briefing',
    summary: 'Every day · 8:00 AM',
    description: 'An agent writes you a short start-of-day briefing.',
    prefill: { cron: '0 8 * * *', label: 'briefing' },
  },
  {
    id: 'nightly-check',
    icon: Moon,
    title: 'Nightly check',
    summary: 'Every day · 11:00 PM',
    description: 'Looks for anything left broken.',
    prefill: { cron: '0 23 * * *', label: 'nightly' },
  },
]

const mount = (onPick = vi.fn(), footer?: React.ReactNode) => {
  const r = render(
    <PresetEmptyState title="No triggers" hint="Start from one of these." presets={PRESETS} onPick={onPick} footer={footer} />,
  )
  return { ...r, onPick }
}

describe('PresetEmptyState', () => {
  it('renders one card per preset, with the four things a preset is', () => {
    mount()
    expect(screen.getAllByRole('button')).toHaveLength(PRESETS.length)
    expect(screen.getByText('Morning briefing')).toBeInTheDocument()
    expect(screen.getByText('Every day · 8:00 AM')).toBeInTheDocument()
    expect(screen.getByText('An agent writes you a short start-of-day briefing.')).toBeInTheDocument()
    // The icon is decorative — the title carries the meaning, so it must not be
    // announced twice.
    expect(document.querySelectorAll('svg[aria-hidden="true"]').length).toBeGreaterThanOrEqual(2)
  })

  it('keeps the headline + hint of an ordinary empty state', () => {
    mount()
    expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument()
    expect(screen.getByText('Start from one of these.')).toBeInTheDocument()
  })

  it('hands the picked preset\'s PREFILL back to the caller — not just its identity', async () => {
    const { onPick } = mount()
    await userEvent.click(screen.getByRole('button', { name: /Morning briefing/ }))
    expect(onPick).toHaveBeenCalledTimes(1)
    // The payload itself, by value. A card that called `onPick()` — or handed back only
    // the id — would leave the create flow with nothing to seed from.
    expect(onPick).toHaveBeenCalledWith({ cron: '0 8 * * *', label: 'briefing' })
  })

  it('names each card by what it will do, not by its own prose', () => {
    mount()
    // TileButton takes its name from its content unless told otherwise, and the content
    // here is three lines of prose. The name is "<title> — <cadence>".
    expect(screen.getByRole('button', { name: 'Morning briefing — Every day · 8:00 AM' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Nightly check — Every day · 11:00 PM' })).toBeInTheDocument()
  })

  it('is reachable by Tab and activatable by Enter and by Space', async () => {
    const { onPick } = mount()
    const cards = screen.getAllByRole('button')
    // One tab stop per card, in document order — no card is mouse-only.
    await userEvent.tab()
    expect(cards[0]).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onPick).toHaveBeenCalledWith(PRESETS[0].prefill)

    await userEvent.tab()
    expect(cards[1]).toHaveFocus()
    await userEvent.keyboard(' ')
    expect(onPick).toHaveBeenLastCalledWith(PRESETS[1].prefill)
    expect(onPick).toHaveBeenCalledTimes(2)
  })

  it('shows a visible focus ring on the focused card', () => {
    mount()
    const card = screen.getAllByRole('button')[0]
    const cls = (card.className || '').split(/\s+/)
    // Asserted as the ring UTILITIES rather than a computed style: a Tailwind ring is a
    // multi-layer box-shadow, so jsdom (which does not apply the stylesheet at all)
    // reports `boxShadow: ""` for a perfectly good ring. The classes ARE the contract
    // here — `focus-visible:` so a mouse click does not paint one, and `ring-inset` so a
    // card at the grid edge cannot clip its own indicator.
    expect(cls).toContain('focus-visible:ring-2')
    expect(cls).toContain('focus-visible:ring-inset')
    expect(cls).toContain('focus-visible:ring-primary/50')
    expect(cls).toContain('focus-visible:outline-none')
  })

  it('puts no interactive element inside a card', () => {
    mount()
    for (const card of screen.getAllByRole('button')) {
      expect(card.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')).toHaveLength(0)
    }
  })

  it('renders the expert blank path in the footer slot, outside the cards', () => {
    const blank = vi.fn()
    mount(vi.fn(), <button type="button" onClick={blank}>Start from scratch</button>)
    const escape = screen.getByRole('button', { name: 'Start from scratch' })
    expect(escape).toBeInTheDocument()
    // Not nested inside a card — the presets must not swallow the blank path.
    for (const card of screen.getAllByRole('button', { name: /—/ }))
      expect(card.contains(escape)).toBe(false)
  })
})

describe('PresetCard', () => {
  it('works standalone — the grid is not part of its contract', async () => {
    const onPick = vi.fn()
    render(
      <PresetCard icon={Sunrise} title="Weekly digest" summary="Every Monday · 9:00 AM"
        description="What moved and what stalled." prefill={{ n: 7 }} onPick={onPick} />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Weekly digest — Every Monday · 9:00 AM' }))
    expect(onPick).toHaveBeenCalledWith({ n: 7 })
  })
})
