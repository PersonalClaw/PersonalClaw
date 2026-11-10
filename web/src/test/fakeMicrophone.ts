import { vi } from 'vitest'

/** jsdom has neither getUserMedia nor MediaRecorder: both faked at the boundary, for the hands-free
 *  tests. A recorder is faithful in the one behaviour the loop leans on: `stop()` delivers a final
 *  `ondataavailable` before `onstop`. Each piece is ended by hand (the hook's own 4 s timer does it
 *  in the app). */

export interface FakeTrack {
  kind: 'audio'
  readyState: 'live' | 'ended'
  stop: ReturnType<typeof vi.fn>
  addEventListener: ReturnType<typeof vi.fn>
  /** The listeners added for each event, so a test can end the track as a browser does. */
  listeners: Record<string, (() => void)[]>
}

export class FakeRecorder {
  static made: FakeRecorder[] = []
  state: 'inactive' | 'recording' = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  constructor(public stream: { getTracks: () => FakeTrack[] }) {
    FakeRecorder.made.push(this)
  }
  /** As a browser does, a recorder cannot start on a stream whose tracks have all ended. */
  start() {
    if (this.stream.getTracks().every((t) => t.readyState === 'ended')) {
      throw Object.assign(new Error('The stream has no live track'), { name: 'NotSupportedError' })
    }
    this.state = 'recording'
  }
  /** `onstop` runs as an event handler does: a throw in it never reaches the caller of stop(). */
  stop() {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['speech'], { type: 'audio/webm' }) })
    try { this.onstop?.() } catch { /* an event handler's error, reported, not thrown */ }
  }
}

/** Install the fakes. With `deny`, every getUserMedia call refuses as a blocked microphone does;
 *  with `opening`, each call waits for that promise first, as a permission prompt holds it. */
export function installFakeMicrophone(opts: { deny?: boolean; opening?: Promise<unknown> } = {}) {
  FakeRecorder.made = []
  const tracks: FakeTrack[] = []
  const getUserMedia = vi.fn(async () => {
    if (opts.opening) await opts.opening
    if (opts.deny) throw Object.assign(new Error('Permission denied'), { name: 'NotAllowedError' })
    const track: FakeTrack = {
      kind: 'audio',
      readyState: 'live',
      stop: vi.fn(() => { track.readyState = 'ended' }),
      listeners: {},
      addEventListener: vi.fn((type: string, fn: () => void) => { (track.listeners[type] ??= []).push(fn) }),
    }
    tracks.push(track)
    return { getTracks: () => [track], getAudioTracks: () => [track] }
  })
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
  ;(globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder
  return {
    getUserMedia,
    tracks,
    get recorders() { return FakeRecorder.made },
    /** The piece recording now, if any. */
    live: () => FakeRecorder.made.find((r) => r.state === 'recording'),
    /** The microphone goes away (a headset unplugged, the grant revoked): its track ends. */
    endTrack: (n = 0) => {
      const track = tracks[n]
      track.readyState = 'ended'
      for (const fn of track.listeners.ended ?? []) fn()
    },
  }
}

/** A promise with its resolve and reject in hand, for a transcription that answers when told. */
export function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
