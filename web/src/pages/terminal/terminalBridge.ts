/** Bridge between "run in terminal" affordances (e.g. a chat code block / tool
 *  card) and live PTY sockets. A module-level singleton because the sender lives
 *  deep in the chat tree while the terminal lives on another page — they can't
 *  pass props. Each mounted TerminalView registers a send(text) for its session;
 *  the most-recently-registered live one is the "active" target. */

type Sender = (text: string) => boolean

const senders = new Map<string, Sender>()
let activeId: string | null = null
const listeners = new Set<() => void>()

export function registerTerminal(id: string, send: Sender): void {
  senders.set(id, send)
  activeId = id
  listeners.forEach((l) => l())
}

export function unregisterTerminal(id: string): void {
  senders.delete(id)
  if (activeId === id) activeId = senders.size ? [...senders.keys()][senders.size - 1] : null
  listeners.forEach((l) => l())
}

/** Is there a live terminal to run into? (drives the "Run in terminal" affordance) */
export function hasActiveTerminal(): boolean {
  return activeId != null && senders.has(activeId)
}

/** Send a command to a terminal. Appends a newline so it executes. Targets a SPECIFIC
 *  session when `id` is given (so a caller with its own terminal — e.g. the Code cockpit
 *  — never fires into an unrelated active terminal on another page), else the active one.
 *  Returns false if that terminal isn't live (caller should open one / wait). */
export function runInTerminal(command: string, id?: string): boolean {
  const target = id ?? activeId
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
    else console.warn('Run command dropped — terminal never became ready:', command)
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
