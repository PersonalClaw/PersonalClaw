import { createContext, useContext } from 'react'

/** How many levels below its page's outline a block of headings sits: 0 where a page draws it as its
 *  own, 1 where it is one section of a page that holds several, under that section's `h2` (Settings'
 *  folded pages, #5897). A shared heading written at level 2 — an empty state's title, a failed
 *  read's — is then an `h3`, so a section's own empty state does not rank beside the section. */
const HeadingDepth = createContext(0)
/** The context itself: a page that draws section headings wraps each section in its `Provider`. */
export const headingDepth = HeadingDepth

/** The tag for a heading written at `level`, one level deeper per enclosing section depth. */
export function useHeadingTag(level: 2 | 3): 'h2' | 'h3' | 'h4' {
  return `h${level + useContext(HeadingDepth)}` as 'h2' | 'h3' | 'h4'
}

/** Whether these headings sit inside a section of a page that draws its own section headings. */
export function useInSection(): boolean {
  return useContext(HeadingDepth) > 0
}
