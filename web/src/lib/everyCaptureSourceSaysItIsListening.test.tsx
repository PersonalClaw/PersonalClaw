import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'
import { useCaptureReport } from './pushToTalk'

// ── Every capture source says the microphone is live (#3833) ───────────────────────────────────
//
// The shell shows listening in the tray, in every window and on the gateway, but only for what a
// window reports. The composer's dictation reported its capture; hands-free and the Knowledge
// recorder opened the microphone and said nothing. Each now reports its own source through one
// hook, and the stop on unmount too. A plain browser tab has no shell, and reports nothing.

type Calls = [boolean, string][]

function withShell(): Calls {
  const calls: Calls = []
  ;(window as unknown as { pclawDesktop: unknown }).pclawDesktop = {
    pushToTalk: { setCapturing: vi.fn(async (on: boolean, source: string) => { calls.push([on, source]); return on }) },
  }
  return calls
}

afterEach(() => {
  cleanup()
  delete (window as unknown as { pclawDesktop?: unknown }).pclawDesktop
})

describe('a capture source reports what it holds', () => {
  it('says its source while it captures, and when it stops', () => {
    const calls = withShell()
    const { rerender } = renderHook(({ on }) => useCaptureReport(on, 'recorder'), { initialProps: { on: false } })
    rerender({ on: true })
    rerender({ on: false })
    expect(calls).toEqual([[false, 'recorder'], [true, 'recorder'], [false, 'recorder']])
  })

  it('says it stopped when it goes away mid-capture: an indicator never outlives its stream', () => {
    const calls = withShell()
    const { unmount } = renderHook(() => useCaptureReport(true, 'hands-free'))
    unmount()
    expect(calls.at(-1)).toEqual([false, 'hands-free'])
  })

  it('in a browser tab, with no shell, reports nothing and never throws', () => {
    const { rerender } = renderHook(({ on }) => useCaptureReport(on, 'dictation'), { initialProps: { on: true } })
    rerender({ on: false })
  })
})

/** Each place that opens the microphone, the file that reports its capture, and the sources. */
const CAPTURE_SITES: Record<string, { reportedIn: string; reports: string[] }> = {
  // The composer's recorder serves dictation (through push-to-talk's report) and hands-free.
  'ui/composer/useMicRecorder.ts': { reportedIn: 'ui/Composer.tsx', reports: ['usePushToTalk(', "useCaptureReport(listening && mic.state === 'recording', 'hands-free')"] },
  'pages/knowledge/AudioRecorder.tsx': { reportedIn: 'pages/knowledge/AudioRecorder.tsx', reports: ["'recorder')"] },
}

describe('every place that opens the microphone reports it', () => {
  const SRC = join(process.cwd(), 'src')
  const opens = filesUnder(SRC, (n) => /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n))
    .filter((p) => /getUserMedia\(\s*\{\s*audio/.test(readSource(p)))
    .map((p) => p.slice(SRC.length + 1))
    .sort()

  it('the sites are the ones listed: a new one must say how it reports', () => {
    expect(opens.length, 'the scan must find the microphone it was written for').toBeGreaterThanOrEqual(2)
    expect(opens).toEqual(Object.keys(CAPTURE_SITES).sort())
  })

  it('each reports its capture where it is held', () => {
    for (const [site, { reportedIn, reports }] of Object.entries(CAPTURE_SITES)) {
      const text = readSource(join(SRC, reportedIn))
      for (const call of reports) expect(text, `${site}, reported in ${reportedIn}`).toContain(call)
    }
    // Push-to-talk's own report names dictation.
    expect(readSource(join(SRC, 'lib/pushToTalk.ts'))).toContain("useCaptureReport(capturing, 'dictation', available)")
  })
})
