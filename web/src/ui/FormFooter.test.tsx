import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
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

  // ── The action's failure lives IN the bar, beside the action ─────────────────────────────
  // Every adopter used to render its save error just above this bar — the END of the scrolling
  // form. On the task panel that put a refusal 803px below the fold with focus on <body>, so a
  // refused save looked like nothing happened. These pin the three halves of "seen": in the bar,
  // before the actions it describes, and focused.
  it('renders an error inside the sticky bar, before the actions', () => {
    const { container } = render(<FormFooter error="Couldn't save this task: refused."><button>Save</button></FormFooter>)
    const bar = container.firstElementChild as HTMLElement
    const alert = screen.getByRole('alert')
    expect(bar).toContainElement(alert)
    expect(alert.textContent).toContain("Couldn't save this task: refused.")
    expect(alert.compareDocumentPosition(screen.getByText('Save')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    // It takes the bar's full first row, so a long refusal cannot squeeze the buttons.
    expect(classOf(alert)).toContain('basis-full')
    expect(classOf(bar)).toContain('flex-wrap')
  })

  it('moves focus to the error when one appears, and again when it changes', () => {
    const { rerender } = render(<FormFooter error=""><button>Save</button></FormFooter>)
    expect(screen.queryByRole('alert')).toBeNull()
    rerender(<FormFooter error="first refusal"><button>Save</button></FormFooter>)
    expect(document.activeElement).toBe(screen.getByRole('alert'))
    screen.getByText('Save').focus()
    rerender(<FormFooter error="second refusal"><button>Save</button></FormFooter>)
    expect(document.activeElement).toBe(screen.getByRole('alert'))
  })

  it('does not pull focus on a re-render with the same error', () => {
    const { rerender } = render(<FormFooter error="refused"><button>Save</button></FormFooter>)
    screen.getByText('Save').focus()
    rerender(<FormFooter error="refused"><button>Save</button></FormFooter>)
    expect(document.activeElement).toBe(screen.getByText('Save'))
  })
})
