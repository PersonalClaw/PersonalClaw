import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import { Markdown } from './Markdown'

// A reply names a file in the user's home from `~` (`~/Notes/Garden/Weekly/2026-W40.md`): the
// agent is asked to, because spelled out in full it rewrote her home's long path from memory and
// cited a folder that was not hers. So a `~` path must open as written — the gateway reads `~` as
// the home it runs with. Before, the file-link patterns had no room for `~/`: a `~` path in code
// was no link at all, and one in prose became a link to `/Notes/…`, with the `~` left outside it.
function linksIn(text: string) {
  const opened: string[] = []
  const { getAllByRole, queryAllByRole } = render(
    <Markdown onFileClick={(path) => opened.push(path)}>{text}</Markdown>,
  )
  return { opened, getAllByRole, queryAllByRole }
}

describe('a path from ~ in a reply opens as written', () => {
  it('in code', () => {
    const { opened, getAllByRole } = linksIn('I read `~/Notes/Garden/Weekly/2026-W40.md`.')
    const [link] = getAllByRole('button', { name: '~/Notes/Garden/Weekly/2026-W40.md' })
    fireEvent.click(link)
    expect(opened).toEqual(['~/Notes/Garden/Weekly/2026-W40.md'])
  })

  it('in prose, the ~ included', () => {
    const { opened, getAllByRole } = linksIn('I read ~/Notes/Garden/Weekly/2026-W40.md and ~/todo.md.')
    const links = getAllByRole('button')
    expect(links.map((l) => l.textContent)).toEqual(['~/Notes/Garden/Weekly/2026-W40.md', '~/todo.md'])
    links.forEach((l) => fireEvent.click(l))
    expect(opened).toEqual(['~/Notes/Garden/Weekly/2026-W40.md', '~/todo.md'])
  })

  it('a path outside the home still opens as written', () => {
    const { opened, getAllByRole } = linksIn('See /srv/app/main.py and `/srv/app/README.md`.')
    getAllByRole('button').forEach((l) => fireEvent.click(l))
    expect(opened).toEqual(['/srv/app/main.py', '/srv/app/README.md'])
  })

  it('`~name` is a name, not a path from the home', () => {
    const onFileClick = vi.fn()
    const { queryAllByRole } = render(<Markdown onFileClick={onFileClick}>{'Ask `~ada/notes.md` for it.'}</Markdown>)
    expect(queryAllByRole('button')).toEqual([])
  })
})
