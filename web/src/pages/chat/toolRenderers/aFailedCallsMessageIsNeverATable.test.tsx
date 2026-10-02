import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { renderToolOutput } from './registry'
import type { ToolSegment } from '../chatTypes'

// A call the shell denylist refused answers with two sentences, the refusal and its hint, and
// each has one comma. No backend declares a type for a failed call's output, so the card sniffed
// one, and two lines with one comma each read as a two-column CSV: the refusal was shown as a
// table, cut apart at its commas, with its first half as a column header.

const REFUSAL =
  'Error: Blocked: the command matches `pcfixture-cloudctl`, a pattern added to the shell ' +
  'denylist under Settings → Security. It was not run.\n' +
  'Hint: The owner chose this rule. Do not rephrase the command to get around it: take a ' +
  'different approach, or tell them what you need to run and why.'

const segment = (output: string, ok?: boolean): ToolSegment => ({
  kind: 'tool', id: 't1', tool: 'bash', input: '{"command": "pcfixture-cloudctl status"}', output, done: true, ok,
})

describe("a failed call's output", () => {
  it('is its message, whole, and never a table', () => {
    render(<>{renderToolOutput(segment(REFUSAL, false))}</>)
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText(/a pattern added to the shell denylist under Settings → Security\. It was not run\./))
      .toHaveTextContent('Blocked: the command matches `pcfixture-cloudctl`, a pattern added')
  })

  it('still shows a table when a call that ran answered with CSV', () => {
    render(<>{renderToolOutput(segment('name,size\nnotes.md,12\nplan.md,40'))}</>)
    expect(screen.getByRole('table')).toHaveTextContent('notes.md')
  })
})
