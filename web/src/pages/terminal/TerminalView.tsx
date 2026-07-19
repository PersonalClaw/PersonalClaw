import { useEffect, useRef, useState } from 'react'
import { RotateCw, Plug } from 'lucide-react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { useMode } from '../../app/theme'
import { api } from '../../lib/api'
import { registerTerminal, unregisterTerminal } from './terminalBridge'
import type { TermTab } from './TerminalPage'

type Status = 'connecting' | 'open' | 'reconnecting' | 'exited' | 'error'

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

  async function restart() {
    // dead session id is gone server-side → create a fresh one, then reconnect.
    setStatus('connecting'); setExitCode(null)
    try {
      const r = await api.createTerminal(tab.cwd)
      sessionIdRef.current = r.session_id
      onSession?.(r.session_id)  // tell the host so its PTY teardown tracks the live id
    } catch { /* keep old id; connect will retry */ }
    termRef.current?.clear()
    setRestartKey((k) => k + 1)
  }

  return (
    <div className="relative h-full overflow-hidden rounded-lg border border-outline/30" style={{ background: mode === 'light' ? '#ffffff' : '#0d0d12' }}>
      <div ref={hostRef} className="h-full w-full" />

      {status === 'reconnecting' && (
        <div className="absolute inset-x-0 top-0 flex items-center justify-center gap-1.5 bg-warning/20 px-3 py-1 text-center text-[0.7rem] text-on-surface">
          <Plug size={11} className="animate-pulse" /> Reconnecting…
        </div>
      )}
      {(status === 'exited' || status === 'error') && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-canvas/70 backdrop-blur-sm">
          <div className="text-center">
            <div className="text-on-surface text-[0.9375rem]" style={{ fontVariationSettings: '"wght" 500' }}>
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
