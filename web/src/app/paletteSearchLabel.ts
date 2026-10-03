/** What the ⌘K palette's search field is called: its accessible name, and with an ellipsis its
 *  placeholder (`CommandPalette`).
 *
 *  A module of its own, with nothing to import, so the keyboard walkthrough
 *  (`web/e2e/walkthrough.spec.ts`) can name this field by the string the palette renders. Its
 *  allowance for the field held a copy of the label, and when the field was renamed to say it
 *  searches content too, the copy matched nothing and the walkthrough failed on a field it had
 *  already accounted for. */
export const PALETTE_SEARCH_LABEL = 'Search pages, actions and content'
