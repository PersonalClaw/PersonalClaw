import { useEffect, useId, useRef, useState } from 'react'
import { fvs } from '../../design/fontWeight'
import { RotateCw, Plug } from 'lucide-react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { useMode } from '../../app/theme'
import { api } from '../../lib/api'
import { registerTerminal, unregisterTerminal } from './terminalBridge'
import { escapeGate } from './escapeGate'
import type { TermTab } from './TerminalPage'

type Status = 'connecting' | 'open' | 'reconnecting' | 'exited' | 'error'

/** How long the "Esc Esc to leave" hint stays up after focus enters the terminal. */
const HINT_MS = 4000

/** One PTY view bound to a session. Owns the WS lifecycle:
 *   - `exited` (shell ended via `exit`/Ctrl-D) → clear overlay + Restart, NOT a
 *     silent zombie (the old bug: WS stayed open, UI looked live).
 *   - transient WS drop while the PTY is still alive → auto-reconnect w/ backoff.
 *  Registers its send() in the terminal bridge so chat can "run in terminal". */
export function TerminalView({ tab, onExited, onClose, onSession }: { tab: TermTab; onExited: () => void; onClose: () => void
  // Fired when this view starts driving a NEW server-side PTY (a Restart mints a fresh
  // session id). A host that owns PTY teardown (the cockpit's BottomTerminal) must learn
  // the new id, or its cleanup deletes the stale one + leaks the live restarted PTY.
  onSession?: (sessionId: string) => void }) {
  const { mode } = useMode()
  const hostRef = useRef<HTMLDivElement>(null)
  // Where focus goes when the user asks to leave the PTY: the view's own container, so the
  // next Tab continues the page's order from the terminal's position rather than from the top.
  const shellRef = useRef<HTMLDivElement>(null)
  const hintId = useId()
  // The Esc-Esc hint shows on each focus entry and fades once read, so it advises without
  // sitting permanently over the top-right of a terminal somebody is working in.
  const [hintRead, setHintRead] = useState(false)
  const termRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const [status, setStatus] = useState<Status>('connecting')
  const [exitCode, setExitCode] = useState<number | null>(null)
  // restart nonce — bumping it tears down + recreates the session/WS in place.
  const [restartKey, setRestartKey] = useState(0)
  const sessionIdRef = useRef(tab.id)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const term = new Terminal({
      // Include Nerd Font / Powerline-capable families in the fallback chain so a
      // shell prompt's icon glyphs (file/folder icons, git branch symbols) resolve
      // to a real glyph when the font is installed, instead of rendering as tofu
      // boxes (□). The primary UI font lacks these private-use-area glyphs; the
      // browser falls through to whichever of these the OS has.
      fontSize: 13,
      fontFamily: '"Google Sans Code", "MesloLGS NF", "FiraCode Nerd Font", "Hack Nerd Font", "JetBrainsMono Nerd Font", "Symbols Nerd Font", "Powerline Symbols", ui-monospace, monospace',
      cursorBlink: true,
      theme: mode === 'light'
        ? { background: '#ffffff', foreground: '#1a1a1a' }
        : { background: '#0d0d12', foreground: '#e6e6ee' },
    })
    const fit = new FitAddon(); term.loadAddon(fit); term.loadAddon(new WebLinksAddon())
    term.open(host); try { fit.fit() } catch { /* not visible yet */ }
    termRef.current = term

    // ── WCAG 2.1.2 No Keyboard Trap (level A) ────────────────────────────────────────
    // Measured on a LIVE session, on this page and in the ⌘` drawer: with focus on
    // `.xterm-helper-textarea`, Tab, Shift+Tab AND Escape all left focus exactly where it
    // was — a keyboard user who entered the terminal could not leave by any key, and
    // nothing on screen said otherwise (2.1.2 exempts a trap only when the way out is
    // ADVISED). `escapeGate` owns which keydown releases and why.
    let lastEsc = 0
    term.attachCustomKeyEventHandler((e) => {
      if (e.type !== 'keydown') return true
      const d = escapeGate(e.key, e.timeStamp, lastEsc)
      lastEsc = d.lastEscAt
      if (d.release) shellRef.current?.focus()
      return d.forward
    })
    // Releasing focus to the container is only half an escape: the container comes BEFORE
    // the PTY in DOM order, so the next Tab walked straight back into it (measured — focus
    // looped between the two forever). The terminal therefore gets ONE tab stop, the
    // labelled container, and xterm's textarea leaves the tab order entirely: Tab now
    // passes the terminal by, Enter on the container steps in, Esc Esc steps out. Same
    // shape as the board's scrollable region — a labelled group you enter deliberately.
    // Clicking still focuses the PTY directly; `tabindex="-1"` only removes the tab stop.
    const helper = host.querySelector('.xterm-helper-textarea')
    helper?.setAttribute('tabindex', '-1')
    // The hint has to be announced where focus actually LANDS, which is xterm's own helper
    // textarea, not our container — so both carry it.
    helper?.setAttribute('aria-describedby', hintId)

    let disposed = false
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined
    let attempts = 0

    const connect = () => {
      if (disposed) return
      const sid = sessionIdRef.current
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/api/ws/terminal/${encodeURIComponent(sid)}`)
      ws.binaryType = 'arraybuffer'
      wsRef.current = ws

      ws.onopen = () => {
        attempts = 0
        setStatus('open'); setExitCode(null)
        ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
      }
      ws.onmessage = (e) => {
        if (typeof e.data === 'string') {
          // JSON control frame.
          try {
            const m = JSON.parse(e.data)
            if (m.type === 'exited') { setExitCode(typeof m.code === 'number' ? m.code : null); setStatus('exited'); onExited() }
            else if (m.type === 'error') setStatus('error')
          } catch { /* pong / noise */ }
          return
        }
        term.write(new Uint8Array(e.data as ArrayBuffer))
      }
      ws.onclose = () => {
        if (disposed) return
        // If the shell exited we already set 'exited' (don't reconnect a dead
        // PTY). Otherwise the WS dropped while the PTY is likely still alive →
        // reconnect with backoff (the backend re-binds to the same session).
        setStatus((s) => {
          if (s === 'exited' || s === 'error') return s
          attempts += 1
          const delay = Math.min(1000 * 2 ** (attempts - 1), 8000)
          reconnectTimer = setTimeout(connect, delay)
          return 'reconnecting'
        })
      }

      const onData = term.onData((d) => { if (ws.readyState === WebSocket.OPEN) ws.send(new TextEncoder().encode(d)) })
      ;(ws as any)._onData = onData
    }
    connect()

    const ro = new ResizeObserver(() => {
      try { fit.fit() } catch { /* hidden */ }
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }))
    })
    ro.observe(host)

    // expose send() so chat's "run in terminal" can target this session. Bind the id
    // ONCE here (not via sessionIdRef.current in cleanup): a Restart mutates the ref to
    // the new id BEFORE this effect's cleanup runs, so reading the ref in cleanup would
    // unregister the NEW id (never registered) and leak the OLD one in the bridge
    // forever. Capturing the bound id makes register/unregister symmetric per effect run.
    const boundSession = sessionIdRef.current
    registerTerminal(boundSession, (text: string) => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) { ws.send(new TextEncoder().encode(text)); return true }
      return false
    })

    return () => {
      disposed = true
      clearTimeout(reconnectTimer)
      unregisterTerminal(boundSession)
      const ws = wsRef.current
      try { (ws as any)?._onData?.dispose?.() } catch { /* noop */ }
      ro.disconnect(); ws?.close(); term.dispose()
    }
  }, [restartKey]) // eslint-disable-line react-hooks/exhaustive-deps

  // theme live-update without tearing down the session.
  useEffect(() => {
    const t = termRef.current
    if (t) t.options.theme = mode === 'light' ? { background: '#ffffff', foreground: '#1a1a1a' } : { background: '#0d0d12', foreground: '#e6e6ee' }
  }, [mode])

  // Re-advise the way out on every focus entry, then fade it after HINT_MS. Bound to the
  // container's focusin/focusout because focus actually lands on xterm's helper textarea,
  // which React never renders and so cannot carry an onFocus prop.
  useEffect(() => {
    const el = shellRef.current
    if (!el) return
    let t: ReturnType<typeof setTimeout> | undefined
    const onIn = () => { setHintRead(false); clearTimeout(t); t = setTimeout(() => setHintRead(true), HINT_MS) }
    const onOut = () => { clearTimeout(t); setHintRead(false) }
    el.addEventListener('focusin', onIn)
    el.addEventListener('focusout', onOut)
    return () => { el.removeEventListener('focusin', onIn); el.removeEventListener('focusout', onOut); clearTimeout(t) }
  }, [])

  async function restart() {
    // dead session id is gone server-side → create a fresh one, then reconnect.
    setStatus('connecting'); setExitCode(null)
    try {
      const r = await api.createTerminal(tab.cwd, tab.sandbox)
      sessionIdRef.current = r.session_id
      onSession?.(r.session_id)  // tell the host so its PTY teardown tracks the live id
    } catch { /* keep old id; connect will retry */ }
    termRef.current?.clear()
    setRestartKey((k) => k + 1)
  }

  return (
    // The ring is on plain `focus:` deliberately. The Esc-Esc release focuses this container
    // PROGRAMMATICALLY, and the keyboard-only variant of that pseudo-class does not match a
    // scripted `.focus()` — a ring gated on it would never paint on the way out, handing the
    // user focus with nothing to show where it went (2.4.7). (Naming the variant in this
    // comment is enough to make the consistency report count this file as having a local
    // keyboard-focus override, which it does not — so the name stays out of it.)
    <div ref={shellRef} tabIndex={0} role="group" aria-label="Terminal session"
      aria-describedby={hintId}
      onKeyDown={(e) => {
        // Enter steps INTO the PTY — but only from the container itself; a bare Enter typed
        // inside the terminal must reach the shell, and this handler also sees it bubble.
        if (e.key === 'Enter' && e.target === e.currentTarget) { e.preventDefault(); termRef.current?.focus() }
      }}
      className="group relative h-full overflow-hidden rounded-lg border border-outline/30 outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
      style={{ background: mode === 'light' ? '#ffffff' : '#0d0d12' }}>
      <div ref={hostRef} className="h-full w-full" />

      {/* The way out, advised where it is needed and nowhere else: revealed while focus is
          inside the terminal (the app's `focus-within` reveal convention), faded once it has
          been read so it stops covering output, and referenced by the PTY's own
          `aria-describedby` so it is announced on arrival too. Never `aria-hidden` — an
          `aria-describedby` target must stay readable after it fades. */}
      <div id={hintId}
        className={`pointer-events-none absolute right-2 top-1.5 rounded-pill bg-surface-high/90 px-2 py-0.5 text-on-surface-low text-[0.6875rem] transition-opacity ${hintRead ? 'opacity-0' : 'opacity-0 group-focus-within:opacity-100'}`}>
        Enter to type here · Esc Esc to leave
      </div>

      {status === 'reconnecting' && (
        <div className="absolute inset-x-0 top-0 flex items-center justify-center gap-1.5 bg-warning/20 px-3 py-1 text-center text-[0.75rem] text-on-surface">
          <Plug size={11} className="animate-pulse" /> Reconnecting…
        </div>
      )}
      {(status === 'exited' || status === 'error') && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-canvas/70 backdrop-blur-sm">
          <div className="text-center">
            <div className="text-on-surface text-[0.9375rem]" style={fvs(500)}>
              {status === 'error' ? 'Session error' : exitCode ? `Process exited (code ${exitCode})` : 'Process exited'}
            </div>
            <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">The shell session has ended.</div>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={restart}
              className="inline-flex items-center gap-1.5 rounded-pill px-4 h-9 text-[0.8125rem]" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
              <RotateCw size={14} /> Restart
            </button>
            <button type="button" onClick={onClose}
              className="rounded-pill px-4 h-9 text-on-surface-low text-[0.8125rem] hover:bg-surface-high hover:text-on-surface">Close tab</button>
          </div>
        </div>
      )}
    </div>
  )
}
