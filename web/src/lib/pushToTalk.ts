import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { desktopBridge } from './desktopBridge'

/** What the shell pushes when the chord fires, or when it wants a capture stopped
 *  (its runaway-capture ceiling). `toggle` deliberately carries no direction: the
 *  RENDERER holds the stream, so only the renderer knows whether a press means start
 *  or stop. A directional message would let the two processes disagree about whether
 *  the microphone is open. */
export interface PushToTalkPush {
  action: 'toggle' | 'stop'
  reason?: string
}

/** The shipped default — mirrors `DEFAULT_PUSH_TO_TALK_CHORD` in
 *  `personalclaw/voice/duplex.py` and `DEFAULT_CHORD` in `desktop/pushToTalk.js`. */
export const DEFAULT_PUSH_TO_TALK_CHORD = 'CommandOrControl+Shift+Space'

const MOD_SYMBOL: Record<string, string> = {
  CommandOrControl: '⌘',
  CmdOrCtrl: '⌘',
  Command: '⌘',
  Cmd: '⌘',
  Control: '⌃',
  Ctrl: '⌃',
  Alt: '⌥',
  Option: '⌥',
  Shift: '⇧',
  Super: '⌘',
  Meta: '⌘',
}

/** Render an accelerator the way a Mac user reads it: `⌘⇧Space`, not
 *  `CommandOrControl+Shift+Space`. Display only — the stored value stays the
 *  accelerator string the shell can actually bind. */
export function formatChord(chord: string): string {
  const parts = chord.split('+').map((p) => p.trim()).filter(Boolean)
  if (!parts.length) return ''
  const key = parts[parts.length - 1]
  const mods = parts.slice(0, -1).map((m) => MOD_SYMBOL[m] ?? m)
  return `${mods.join('')}${key}`
}

/** Turn a keyboard event into an accelerator string, for a "press your shortcut"
 *  control. Returns '' for a press that is only modifiers — the caller keeps
 *  listening rather than recording half a chord. */
export function chordFromEvent(e: {
  key: string
  code: string
  metaKey: boolean
  ctrlKey: boolean
  altKey: boolean
  shiftKey: boolean
}): string {
  const mods: string[] = []
  if (e.metaKey) mods.push('Command')
  if (e.ctrlKey) mods.push('Control')
  if (e.altKey) mods.push('Alt')
  if (e.shiftKey) mods.push('Shift')

  let key = ''
  if (e.code.startsWith('Key')) key = e.code.slice(3)
  else if (e.code.startsWith('Digit')) key = e.code.slice(5)
  else if (/^F\d{1,2}$/.test(e.code)) key = e.code
  else if (e.code === 'Space') key = 'Space'
  else if (e.key.length === 1) key = e.key.toUpperCase()
  else if (['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'Home', 'End'].includes(e.key)) key = e.key
  else if (e.key.startsWith('Arrow')) key = e.key.slice(5)

  // Modifier-only press, or a key we do not name: not a chord yet.
  if (!key || ['Meta', 'Control', 'Alt', 'Shift'].includes(e.key)) return ''
  // A bare key is refused by the shell (it would be taken from every app), so do not
  // even offer it as a recorded chord.
  if (!mods.length) return ''
  return [...mods, key].join('+')
}

/** Ask the shell for microphone permission before opening a stream (the "TCC via
 *  bridge grant" leg). Returns null when everything is fine, or a sentence to show the
 *  user when it is not.
 *
 *  In a browser tab there is no bridge and this is a no-op: the browser's own
 *  permission prompt is the gate there, and `getUserMedia` raises it. */
export async function ensureMicGrant(): Promise<string | null> {
  const bridge = desktopBridge()
  if (!bridge) return null
  try {
    const state = await bridge.capabilities.probe('audio_capture')
    if (state.granted === 'granted') return null
    if (state.granted === 'denied' || state.granted === 'restricted') {
      // macOS will not prompt a second time, so "try again" would be a lie. Name the
      // one place that can actually change the answer.
      return 'Microphone access is turned off for PersonalClaw. Turn it on in System Settings › Privacy & Security › Microphone, then try again.'
    }
    if (!state.requestable) {
      return state.reason || 'The microphone is not available on this machine.'
    }
    const grant = await bridge.capabilities.request('audio_capture')
    if (grant.granted) return null
    return grant.reason || 'Microphone access was declined, so there is nothing to record.'
  } catch {
    // A bridge that does not answer must not block dictation: fall through and let
    // getUserMedia produce the real error.
    return null
  }
}

/**
 * Push-to-talk wiring for a composer (DC-3 T3.1/T3.2).
 *
 * The hook owns the SEAM, never the stream. `capturing` is passed in from whatever
 * actually holds the microphone, and every decision is made from that value rather than
 * from a flag this hook keeps — which is what makes "captures only while held/toggled"
 * checkable: there is no second copy of the capture state to drift.
 *
 * Three jobs:
 *
 *  1. **Subscribe** to chord presses and turn them into start/stop against the real
 *     capture state.
 *  2. **Report** the real capture state back to the shell, because that report is what
 *     lights the menu-bar indicator. The report also runs on unmount, so navigating away
 *     mid-capture cannot leave an indicator glowing over a stream that is gone.
 *  3. **Bind** the configured chord, once, and hand back the failure if the chord is
 *     already owned by another app.
 *
 * In a browser tab every one of those is inert (no bridge, no config fetch, no binding)
 * and the hook returns `available: false`.
 */
export function usePushToTalk({
  capturing,
  onStart,
  onStop,
  enabled = true,
}: {
  capturing: boolean
  onStart: () => void
  onStop: () => void
  enabled?: boolean
}) {
  const bridge = desktopBridge()
  const available = !!bridge && enabled
  const [chord, setChord] = useState('')
  const [bindError, setBindError] = useState('')

  // Read at fire time, not captured: a press must act on the CURRENT capture state and
  // call the current handlers, or a toggle decides against a stale render.
  const stateRef = useRef({ capturing, onStart, onStop })
  stateRef.current = { capturing, onStart, onStop }

  // 1 + 3. Subscribe to presses, and bind the configured chord.
  useEffect(() => {
    if (!available || !bridge) return
    let live = true
    const off = bridge.pushToTalk?.on?.((push) => {
      const s = stateRef.current
      if (!push || typeof push !== 'object') return
      if (push.action === 'stop') {
        // The shell's ceiling, or any other "stop now": only meaningful if we are
        // actually capturing.
        if (s.capturing) s.onStop()
        return
      }
      if (push.action !== 'toggle') return
      if (s.capturing) s.onStop()
      else s.onStart()
    })

    void (async () => {
      // The chord lives in config, so only the shell pays for this read.
      let want = DEFAULT_PUSH_TO_TALK_CHORD
      try {
        const cfg = await api.personalclawConfig()
        const v = (cfg?.voice as Record<string, unknown> | undefined)?.push_to_talk_chord
        if (typeof v === 'string' && v.trim()) want = v.trim()
      } catch {
        /* fall back to the default rather than leaving push-to-talk unbound */
      }
      if (!live) return
      try {
        const r = await bridge.pushToTalk?.bind?.(want)
        if (!live) return
        if (r?.ok) { setChord(r.chord); setBindError('') }
        else { setChord(''); setBindError(r?.reason || 'The push-to-talk shortcut could not be set.') }
      } catch {
        if (live) setBindError('The push-to-talk shortcut could not be set.')
      }
    })()

    return () => { live = false; off?.() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available])

  // 2. Report the microphone's real state to the shell — this is what the always-on
  // indicator is drawn from.
  useEffect(() => {
    if (!available || !bridge) return
    void bridge.pushToTalk?.setCapturing?.(capturing)
  }, [available, bridge, capturing])

  // …including on unmount. An indicator that outlived its stream would be worse than no
  // indicator, and this is the one teardown a component-scoped effect can guarantee.
  useEffect(() => {
    if (!available || !bridge) return
    return () => { void bridge.pushToTalk?.setCapturing?.(false) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available])

  return { available, chord, bindError }
}

/** Bind a chord immediately and report the result — what the Settings control calls when
 *  the user picks a new shortcut, so a conflict is visible at the moment they choose it
 *  rather than at the next launch. */
export async function bindChord(chord: string): Promise<{ ok: boolean; conflict: boolean; reason: string }> {
  const bridge = desktopBridge()
  if (!bridge?.pushToTalk?.bind) {
    return { ok: false, conflict: false, reason: 'The desktop app is not connected, so there is no global shortcut to bind.' }
  }
  try {
    const r = await bridge.pushToTalk.bind(chord)
    return { ok: !!r?.ok, conflict: !!r?.conflict, reason: r?.reason ?? '' }
  } catch (e) {
    return { ok: false, conflict: false, reason: e instanceof Error ? e.message : 'The desktop app did not answer.' }
  }
}

/** The `usePushToTalk` return shape, for hosts that pass it down. */
export type PushToTalk = ReturnType<typeof usePushToTalk>
