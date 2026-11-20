import { useEffect, useRef } from 'react'
import { sendFrame, useChatSocket, type WsMessage } from '../lib/useChatSocket'

/** An item's id on a page, as a `personalclaw://` link's segment is held (`desktop/deepLinks.js`)
 *  and as the bridge checks it before it sends (`inbound/bridge.py`): ASCII only, on both sides. */
const PAGE_ID = /^[A-Za-z0-9._:@~-]{1,128}$/

/** How long after your last click or key press a page is still under your hand: a move inside it
 *  would land that click on the new page. */
export const UNDER_YOUR_HAND_MS = 2000

/** How long a page the bridge just moved ignores a click: a hand that clicked just as it moved
 *  meant the page that was there, and some pages put a Confirm button where it lands. */
export const SETTLING_MS = 700

/** The longest client name a notice says. */
const NAME_CAP = 80

/** The path a bridge's `navigate` frame names (`loops/abc12345`), built from its two fields, or
 *  `null` when it is not one of the dashboard's own pages. The page must be one `routable` knows,
 *  and the id only of the characters a link's segment may carry. No route string is parsed, so a
 *  `..`, a `#` or a `javascript:` has nowhere to go. */
export function bridgePath(kind: unknown, id: unknown, routable: (page: string) => boolean): string | null {
  if (typeof kind !== 'string' || !routable(kind)) return null
  if (id === undefined || id === null || id === '') return kind
  if (typeof id !== 'string' || id === '.' || id === '..' || !PAGE_ID.test(id)) return null
  return `${kind}/${encodeURIComponent(id)}`
}

const NOT_TYPED_IN = new Set(['button', 'checkbox', 'radio', 'submit', 'reset', 'range', 'color', 'file'])

/** Whether you are typing in this page: it has focus, and so does a field you type in. */
function typingHere(): boolean {
  const el = document.activeElement as HTMLElement | null
  if (!el || !document.hasFocus()) return false
  if (el.isContentEditable || el.tagName === 'TEXTAREA') return true
  return el.tagName === 'INPUT' && !NOT_TYPED_IN.has((el as HTMLInputElement).type)
}

/** A page's name as a sentence says it: `mission-control` is "Mission control". */
function pageName(path: string): string {
  const page = path.split('/')[0].replace(/-/g, ' ')
  return page.charAt(0).toUpperCase() + page.slice(1)
}

function toast(message: string, link?: string) {
  window.dispatchEvent(new CustomEvent('ne:toast', {
    detail: link ? { level: 'info', message, href: link, hrefLabel: 'Open it' } : { level: 'info', message },
  }))
}

/** The dashboard's reader of the control bridge's `navigate` frame: `open_cockpit`, an action a
 *  local agent may call when you gave its client that action.
 *
 *  The gateway sends the frame to one tab, the one on this computer that took focus last, so each
 *  tab says when it takes focus (a `focused` frame, again whenever its socket opens). The frame
 *  carries the page, the id and the client's name; the page is rebuilt here, and moves only to
 *  one of the dashboard's own pages. A tab you clicked or typed in within the last
 *  {@link UNDER_YOUR_HAND_MS}, or are typing in now, does not move under your hand: it says which
 *  client asked for which page, with a link to open it. A tab that moves says which client moved
 *  it, and ignores a click for {@link SETTLING_MS}, so a hand at rest that clicks just then does
 *  not press a button on the new page. Anything else moves nothing, and is only logged. */
export function useBridgeNavigation(navigate: (path: string) => void, routable: (page: string) => boolean) {
  const go = useRef(navigate)
  go.current = navigate
  const known = useRef(routable)
  known.current = routable
  const lastInput = useRef(-Infinity)
  const movedAt = useRef(-Infinity)

  useEffect(() => {
    const focused = () => sendFrame({ type: 'focused' })
    const touched = () => { lastInput.current = performance.now() }
    // Caught on the way down, before the page's own handlers: a click just after a move is not
    // a click on the page it moved to.
    const settling = (e: MouseEvent) => {
      if (performance.now() - movedAt.current >= SETTLING_MS) return
      e.preventDefault()
      e.stopPropagation()
    }
    window.addEventListener('focus', focused)
    window.addEventListener('pointerdown', touched, true)
    window.addEventListener('keydown', touched, true)
    window.addEventListener('click', settling, true)
    return () => {
      window.removeEventListener('focus', focused)
      window.removeEventListener('pointerdown', touched, true)
      window.removeEventListener('keydown', touched, true)
      window.removeEventListener('click', settling, true)
    }
  }, [])

  useChatSocket((m: WsMessage) => {
    if (m.type !== 'navigate' || m.data?.source !== 'control_bridge') return
    const path = bridgePath(m.data.kind, m.data.id, known.current)
    if (path === null) {
      console.warn('An external agent asked to open a page the dashboard does not have:', m.data.kind, m.data.id)
      return
    }
    const who = (typeof m.data.client === 'string' && m.data.client.trim().slice(0, NAME_CAP)) || 'A local agent'
    const handy = performance.now() - lastInput.current < UNDER_YOUR_HAND_MS
    if (document.visibilityState === 'visible' && (handy || typingHere())) {
      toast(`${who} asked to open ${pageName(path)}.`, `#/${path}`)
      return
    }
    movedAt.current = performance.now()
    go.current(path)
    toast(`Opened by ${who}.`)
  }, null, (connected) => { if (connected && document.hasFocus()) sendFrame({ type: 'focused' }) })
}
