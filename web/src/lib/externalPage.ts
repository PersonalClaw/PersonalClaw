/** Opening a page that is not the dashboard's when its address comes from an `await`: a remote
 *  tool server's sign-in, whose authorization page the gateway names only once the sign-in has
 *  started.
 *
 *  The two places it can open, and why each is decided before the `await`:
 *
 *  - **In a browser**, a tab of its own. It has to be opened inside the click: one opened after an
 *    `await` is a popup the browser blocks. So a blank tab is opened at once, cut off from this
 *    page (`opener = null`) before it is pointed anywhere, so the page it shows can never reach
 *    back into the dashboard, and it is pointed at the address once the address comes. A browser
 *    that blocks it anyway leaves no tab, and that is reported, not taken for an open page.
 *  - **In the desktop app**, the system's default browser. Its windows hold only the gateway's
 *    pages, so it refuses a blank window, and it opens every other page in the system browser. So
 *    nothing is opened in the click; the address goes to the app once it comes, and the app
 *    answers whether the browser opened (`desktopBridge.openInSystemBrowser`). */
import { hasSystemBrowser, openInSystemBrowser } from './desktopBridge'

/** Where the page opened, or why it did not. */
export type OpenedPage =
  /** A tab of this browser, opened in the click. */
  | { where: 'tab' }
  /** The system's default browser, opened by the desktop app. */
  | { where: 'browser' }
  /** No tab: the browser blocked it, or it was closed before the address came. */
  | { where: 'no-tab' }
  /** The desktop app could not open the system browser, in its own words. */
  | { where: 'no-browser'; reason: string }

export interface ExternalPage {
  /** Open the page at `url`, now that its address is known. Never rejects. */
  open: (url: string) => Promise<OpenedPage>
  /** The address never came: close whatever was opened for it. */
  discard: () => void
}

/** Make ready to open an external page. Call it INSIDE the click, before any `await`. */
export function prepareExternalPage(): ExternalPage {
  if (hasSystemBrowser()) {
    return {
      open: async (url) => {
        const answer = await openInSystemBrowser(url)
        if (answer?.ok) return { where: 'browser' }
        return { where: 'no-browser', reason: answer?.reason?.trim() || 'the desktop app did not say why' }
      },
      discard: () => {},
    }
  }
  const tab = window.open('', '_blank')
  if (tab) tab.opener = null
  return {
    open: async (url) => {
      if (!tab || tab.closed) return { where: 'no-tab' }
      tab.location.href = url
      return { where: 'tab' }
    },
    discard: () => { tab?.close() },
  }
}
