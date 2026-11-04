import { screen, waitFor } from '@testing-library/react'

/** The page header's filter (`ui/HeaderFilter`). Its name is what it says is set ("Show: Chats ·
 *  Active"), which changes with the page, so a test finds it by its mark instead. */
export function headerFilter(): HTMLElement {
  const found = screen.queryAllByRole('button').find((b) => b.hasAttribute('data-header-filter'))
  if (!found) throw new Error('no header filter on the page')
  return found
}

/** The header filter, once the page has drawn it. */
export async function findHeaderFilter(): Promise<HTMLElement> {
  let found: HTMLElement | undefined
  await waitFor(() => { found = headerFilter() })
  return found!
}
