import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { AddItemButton } from './AddItemButton'

// ── "Add another row" affordance contract (design-system consistency S2) ──────
// Five list editors (WorkflowForm's Add-step + Add-a-workflow, PromptForm,
// SnippetForm, PromptEditFields) rendered this exact quiet add-button inline. The
// primitive is the single source; this test locks the traits that make it the
// understated surface-container add affordance (NOT the pill CTA Button) — the
// medium-radius container fill, 36px height, ink-var label — so an edit that
// drifts any of them reddens here. It also proves `self-start` (the trait three
// of the five sites carried) rides through className without dropping base chrome.

function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('AddItemButton', () => {
  it('is the medium-radius, container-filled, ink-var quiet add affordance', () => {
    const { getByRole } = render(<AddItemButton>Add step</AddItemButton>)
    const have = classOf(getByRole('button'))
    for (const t of ['inline-flex', 'items-center', 'gap-1.5', 'rounded-md',
      'bg-surface-container', 'px-m', 'h-9', 'text-on-surface-var',
      'text-[0.8125rem]', 'hover:bg-surface-high', 'transition-colors']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('renders its children (the caller-owned leading glyph + label)', () => {
    const { getByText } = render(<AddItemButton>Add a workflow</AddItemButton>)
    expect(getByText('Add a workflow')).toBeInTheDocument()
  })

  it('merges an extra className (e.g. self-start) without dropping base chrome', () => {
    const { getByRole } = render(<AddItemButton className="self-start">x</AddItemButton>)
    const have = classOf(getByRole('button'))
    expect(have).toContain('self-start')
    expect(have).toContain('bg-surface-container')
    expect(have).toContain('h-9')
  })
})
