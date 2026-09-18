/** Issue 598: a dead pane must leave the bridge.
 *
 *  TerminalView's 'exited' handler showed "Process exited" but left its sender
 *  registered, so hasActiveTerminal() stayed true for a pane with no reconnect
 *  path. App.tsx's flush trusts that answer: a queued "Run in terminal" command
 *  was CLAIMED (pendingRun cleared) and handed to a 15s retry loop pointed at a
 *  dead sender — click against an exited pane, don't open a fresh one in 15s,
 *  and the command vanishes with only a console line.
 *
 *  Two layers: the bridge lifecycle the issue found untested (hasActiveTerminal /
 *  register / unregister / runInTerminal had zero cases), and the wiring fact
 *  that the exited branch unregisters — asserted on comment-stripped source, the
 *  same way sibling wiring rails pin call sites.
 */
import { describe, it, expect, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  hasActiveTerminal,
  registerTerminal,
  unregisterTerminal,
  runInTerminal,
} from './terminalBridge'

afterEach(() => {
  for (const id of ['live', 'dead', 'x1']) unregisterTerminal(id)
})

describe('bridge lifecycle (issue 598)', () => {
  it('registering makes a terminal active; unregistering retires it', () => {
    expect(hasActiveTerminal()).toBe(false)
    registerTerminal('x1', () => true)
    expect(hasActiveTerminal()).toBe(true)
    unregisterTerminal('x1')
    expect(hasActiveTerminal()).toBe(false)
  })

  it('a sender that cannot send does NOT retire the terminal (reconnect-by-design)', () => {
    // A dropped socket over a live PTY reconnects — the registration must survive
    // a false-returning sender. Only an explicit unregister (exit/unmount) retires it.
    registerTerminal('live', () => false)
    expect(hasActiveTerminal()).toBe(true)
    expect(runInTerminal('echo hi')).toBe(false)
    expect(hasActiveTerminal()).toBe(true)
  })

  it('falls back to the most recent surviving terminal when the active one retires', () => {
    const got: string[] = []
    registerTerminal('live', (t) => { got.push(t); return true })
    registerTerminal('dead', () => false)
    unregisterTerminal('dead')
    expect(hasActiveTerminal()).toBe(true)
    expect(runInTerminal('pytest')).toBe(true)
    expect(got).toEqual(['pytest\n'])
  })
})

describe("TerminalView's exited branch unregisters (issue 598 wiring)", () => {
  it('the exited control frame retires the pane from the bridge before onExited', () => {
    const src = readFileSync(join(__dirname, 'TerminalView.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|[^:])\/\/.*$/gm, '$1')
    const exited = src.split('\n').find((l) => l.includes("m.type === 'exited'"))
    expect(exited, "the exited branch exists").toBeTruthy()
    expect(exited).toContain('unregisterTerminal(boundSession)')
    // Order matters: the bridge must be honest before the parent reacts.
    expect(exited!.indexOf('unregisterTerminal')).toBeLessThan(exited!.indexOf('onExited'))
  })
})
