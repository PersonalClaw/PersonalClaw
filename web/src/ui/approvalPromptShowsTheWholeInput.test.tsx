/** The compact permission card names what it runs in words, and shows all of it on demand.
 *
 *  The code cockpit's card read `bash({"command": "find /home/user/src/feedsmith -name \"C)` and
 *  nothing on it expanded: the arguments were sliced at 60 characters, mid-word, so she allowed a
 *  command she could not read. The line is now cut at a word (`clipWords`, the server's
 *  `textfmt.clip_words` rule), and when it is cut the card offers the whole input in a block she
 *  can open. An input that fits is shown whole and offers nothing more.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Check, X } from 'lucide-react'
import { ApprovalPrompt } from './ApprovalPrompt'

afterEach(cleanup)

const FIND = '{"command": "find /home/user/src/feedsmith -name \\"CHANGELOG*\\" -not -path \\"./.venv/*\\""}'

function card(args: string) {
  return render(
    <ApprovalPrompt tool="bash" args={args} choices={[
      { key: 'allow', label: 'Allow', icon: Check, onClick: () => {} },
      { key: 'deny', label: 'Deny', icon: X, tone: 'danger', onClick: () => {} },
    ]} />,
  )
}

describe('the compact permission card', () => {
  it('🔴 cuts a long input at a word, never inside one', () => {
    card(FIND)
    const line = screen.getByText((_, el) => el?.tagName === 'DIV' && /^bash\(.*\)$/.test(el.textContent || ''))
    const text = line.textContent || ''
    expect(text.endsWith('…)')).toBe(true)
    const shown = text.slice('bash('.length, -'…)'.length)
    // What is shown is a run of whole words of the input: the next character is a space.
    expect(FIND.startsWith(`${shown} `)).toBe(true)
  })

  it('🔴 shows the whole input when she asks for it', async () => {
    card(FIND)
    const more = screen.getByRole('button', { name: 'Show all of what bash would run' })
    expect(more.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('group', { name: 'Tool arguments' })).toBeNull()
    await userEvent.click(more)
    expect(more.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByRole('group', { name: 'Tool arguments' }).textContent).toBe(FIND)
  })

  it('shows a short input whole and offers nothing more', () => {
    card('{"command": "ls -F"}')
    expect(screen.getByText('bash({"command": "ls -F"})')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Show all of what/ })).toBeNull()
  })
})
