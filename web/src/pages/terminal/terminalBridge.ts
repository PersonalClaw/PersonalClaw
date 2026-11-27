/** Bridge between "run in terminal" affordances (a shell block in a chat reply, a
 *  provider's login command) and live PTY sockets. A module-level singleton because the
 *  sender lives deep in the chat tree while the terminal lives on another page — they can't
 *  pass props. Each mounted TerminalView registers a send(text) for its session. The "active"
 *  target is the session the owner can see: the drawer's shown tab, the Terminal page's shown or
 *  focused pane, as each says when it shows one and when it no longer does (`showTerminal`,
 *  `hideTerminal`). The last shown is the target; when it is hidden, the one shown before it is,
 *  and with none shown nothing is. A session that only registers is never the target, so a hidden
 *  tab or a restored session never takes a command meant for the one shown. */

import { notify } from '../../app/appSdk'

type Sender = (text: string) => boolean

const senders = new Map<string, Sender>()
/** The sessions shown now, the one shown or worked in last at the end. */
const shown: string[] = []
const listeners = new Set<() => void>()
const said = () => listeners.forEach((l) => l())
/** The session "Run in terminal" goes to: the last one shown, or none. */
const activeId = (): string | null => shown[shown.length - 1] ?? null

export function registerTerminal(id: string, send: Sender): void {
  senders.set(id, send)
  said()
}

/** Make *id* the session "Run in terminal" goes to: the one shown or worked in, as the drawer and
 *  the Terminal page say when they show one. It need not have registered yet: until it does,
 *  nothing is the target (`hasActiveTerminal`). */
export function showTerminal(id: string): void {
  if (!id || activeId() === id) return
  const at = shown.indexOf(id)
  if (at >= 0) shown.splice(at, 1)
  shown.push(id)
  said()
}

/** *id* is no longer shown: the drawer shows another tab or closed, the Terminal page another
 *  pane or none. The session shown before it is the target again, or none is. */
export function hideTerminal(id: string): void {
  const at = shown.indexOf(id)
  if (at < 0) return
  shown.splice(at, 1)
  said()
}

/** *id* left: closed, exited or unmounted. It stays shown until its surface hides it, so a target
 *  that closes leaves no target rather than handing its commands to another session. */
export function unregisterTerminal(id: string): void {
  senders.delete(id)
  said()
}

/** Is there a live terminal to run into? (drives the "Run in terminal" affordance) */
export function hasActiveTerminal(): boolean {
  const id = activeId()
  return id != null && senders.has(id)
}

/** Send a command to a terminal. Appends a newline so it executes. Targets a SPECIFIC
 *  session when `id` is given (so a caller with its own terminal — e.g. the Code cockpit
 *  — never fires into an unrelated active terminal on another page), else the active one.
 *  Returns false if that terminal isn't live (caller should open one / wait). */
export function runInTerminal(command: string, id?: string): boolean {
  const target = id ?? activeId()
  if (!target) return false
  const send = senders.get(target)
  if (!send) return false
  const text = command.endsWith('\n') ? command : command + '\n'
  return send(text)
}

/** Send a command once the target terminal can actually ACCEPT it. A terminal
 *  registers its sender synchronously at mount — before its WebSocket finishes
 *  connecting — so a one-shot send at registration time silently drops the command
 *  (the sender returns false on a CONNECTING socket). Polls every 100ms until the
 *  send succeeds, up to 15s (busy gateways can take >4s just to register the PTY).
 *  `getId` re-resolves the target each attempt (a cockpit Restart mints a new
 *  session id mid-wait); omitted → the active terminal. Returns a cancel fn. */
export function runInTerminalWhenReady(command: string, getId?: () => string | undefined): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined
  const started = Date.now()
  const attempt = () => {
    if (runInTerminal(command, getId?.())) return
    if (Date.now() - started < 15_000) timer = setTimeout(attempt, 100)
    else {
      // The RETRY above exists because a one-shot send dropped every cold "Run tests" click
      // (see terminalBridge.test.ts). At the 15s boundary that same silent drop came back: the
      // user pressed Run, waited, and got nothing — the only record was this console line, which
      // no user reads. A dropped user action has to say so on screen.
      //
      // `notify` rather than a bespoke surface: it is how the app already reports a failed action
      // (a rejected config PATCH, an install that needs a restart), and the sender lives in a
      // module with no tree of its own to render into.
      notify('Couldn\'t run the command — the terminal never became ready. Open a terminal and try again.', 'error')
      console.warn('Run command dropped — terminal never became ready:', command)
    }
  }
  attempt()
  return () => clearTimeout(timer)
}

/** Subscribe to active-terminal availability changes. */
export function subscribeTerminal(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

/** Request a command be run in the terminal, opening the drawer first if there's
 *  no live session. Decoupled from the App via a window event (the deep code
 *  block can't reach the drawer state directly). If a terminal IS already live,
 *  runs immediately. */
export function requestRunInTerminal(command: string): void {
  if (hasActiveTerminal() && runInTerminal(command)) return
  // no live terminal → ask the App to open the drawer, then run once it's up.
  window.dispatchEvent(new CustomEvent('ne:run-in-terminal', { detail: { command } }))
}
