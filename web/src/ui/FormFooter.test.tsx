import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { FormFooter } from './FormFooter'

// ── Sticky edit-mode action bar contract (design-system consistency S2/T2.2) ──
// This wrapper was rendered byte-identically inline by seven *Detail edit forms
// (Task, Schedule, Lifecycle, Workflow, Agent, Prompt, Snippet). The primitive is
// the single source; this test locks the four traits that make it a *sticky pinned
// footer* — stays put on scroll (sticky bottom-0), bleeds to the pane edges (-mx-l),
// sits above content on a translucent surface with a hairline top border, and
// right-aligns its buttons — so an edit that drops any of them reddens here.

function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}

describe('FormFooter', () => {
  it('is the sticky, edge-bleeding, top-bordered, right-aligned action bar', () => {
    const { container } = render(<FormFooter><button>Save</button></FormFooter>)
    const have = classOf(container.firstElementChild as HTMLElement)
    for (const t of ['sticky', 'bottom-0', '-mx-l', 'px-l', 'py-3', 'bg-surface/95',
      'border-t', 'border-outline-variant/40', 'flex', 'justify-end', 'gap-s']) {
      expect(have, `missing "${t}"`).toContain(t)
    }
  })

  it('renders its children (the caller-owned Cancel/Save buttons)', () => {
    const { getByText } = render(
      <FormFooter><button>Cancel</button><button>Save</button></FormFooter>,
    )
    expect(getByText('Cancel')).toBeInTheDocument()
    expect(getByText('Save')).toBeInTheDocument()
  })

  it('merges an extra className without dropping the base chrome', () => {
    const { container } = render(<FormFooter className="mt-2"><span /></FormFooter>)
    const have = classOf(container.firstElementChild as HTMLElement)
    expect(have).toContain('mt-2')
    expect(have).toContain('sticky')
    expect(have).toContain('justify-end')
  })
})
