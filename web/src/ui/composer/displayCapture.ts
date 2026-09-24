/**
 * The ONE `getDisplayMedia` acquisition in the app.
 *
 * Two features consume a display stream and they are genuinely different products:
 *
 *   • screen SHARE (MULTIMODAL-IO §5.2, `useScreenShare`) holds a stream open for a
 *     whole session and grabs a budgeted frame at each send — the browser's capture
 *     indicator stays lit for as long as it lives, which is the point of it.
 *   • screen SNIP (CHAT-CRAFT S4, `grabOneFrame`) takes a single frame, stops the
 *     capture immediately, and hands the pixels to a crop overlay. Nothing stays live.
 *
 * What they share is the part the consent story hangs off: asking the browser for a
 * display stream, reading one frame out of it, and stopping every track. A second copy
 * of that would be a second place to get the teardown wrong, so it lives here once and
 * `getDisplayMedia` appears in exactly one file — pinned by
 * `displayCapture.test.ts`'s call-site census.
 *
 * Reading a frame goes through an offscreen `<video>` rather than `ImageCapture`:
 * `grabFrame` is neither in the TS DOM lib nor implemented in Safari, so it would have
 * made both features Chrome-only.
 */

/** Longest edge a frame is downscaled to when a caller asks for a budgeted frame.
 *  Matches the vision-model input budget the platform assumes elsewhere; a 4K frame
 *  sent at full size is mostly wasted tokens. A snip passes 0 (no downscale): its
 *  pixels are about to be cropped and OCR'd, and softening text before OCR reads it
 *  is the one thing a screenshot pipeline must not do. */
export const SHARE_MAX_EDGE = 1568

/** Why an acquisition produced no stream. `cancelled` is a dismissed picker — a
 *  decision, not a failure, so callers stay silent on it. */
export type AcquireFailure = 'unsupported' | 'cancelled' | 'failed'

/** Which provider a screen capture should use. `none` means the entry point is hidden:
 *  no native binary and no browser API (iOS Safari). */
export type CaptureProvider = 'native' | 'browser' | 'none'

/** A crop rectangle in the SOURCE frame's own pixels (not display pixels). */
export interface SnipRect {
  x: number
  y: number
  width: number
  height: number
}

type DisplayMediaCapable = MediaDevices & {
  getDisplayMedia?: (c: MediaStreamConstraints) => Promise<MediaStream>
}

/** Is a browser-side display capture possible at all? Feature-detected, because the
 *  API is simply absent on iOS Safari — the composer entry hides rather than offering
 *  a control that can only fail. */
export function displayCaptureSupported(): boolean {
  const md = navigator.mediaDevices as DisplayMediaCapable | undefined
  return typeof md?.getDisplayMedia === 'function'
}

/**
 * The single decision point behind the composer's "Capture screen area" entry.
 *
 * macOS keeps the native `screencapture -i` path: it is an OS-level snip with a real
 * crosshair and no browser picker in the way, which is strictly better UX than
 * approving a whole-screen share to then crop it in-app. Everywhere else the browser
 * path is the only one that exists. `nativeFailed` re-runs the SAME decision after the
 * native attempt reported an error (no display server, binary refused), which is why
 * the fallback is not a second policy written somewhere else.
 *
 * An UNRESOLVED platform (`''` — `GET /api/system` still in flight) routes to the
 * browser path deliberately: it works wherever the API exists, whereas guessing
 * `native` off-mac would fire a request the server answers with 400.
 */
export function chooseCaptureProvider(
  platform: string,
  supported: boolean,
  nativeFailed = false,
): CaptureProvider {
  if (platform === 'darwin' && !nativeFailed) return 'native'
  return supported ? 'browser' : 'none'
}

/** Ask the browser for a display stream. The user agent owns the picker and the
 *  consent — this never pre-empts it. */
export async function acquireDisplayStream(): Promise<MediaStream | AcquireFailure> {
  const md = navigator.mediaDevices as DisplayMediaCapable | undefined
  if (typeof md?.getDisplayMedia !== 'function') return 'unsupported'
  try {
    return await md.getDisplayMedia({ video: true, audio: false })
  } catch (e) {
    const name = (e as DOMException)?.name
    // A dismissed picker is not an error — the user changed their mind.
    return name === 'AbortError' || name === 'NotAllowedError' ? 'cancelled' : 'failed'
  }
}

/** Stop every track on *stream*. The only way a capture ends. */
export function stopStream(stream: MediaStream | null | undefined): void {
  if (stream) stream.getTracks().forEach((t) => t.stop())
}

/** An offscreen `<video>` playing *stream* — the portable frame source. */
export async function frameSource(stream: MediaStream): Promise<HTMLVideoElement> {
  const video = document.createElement('video')
  video.muted = true
  video.playsInline = true
  video.srcObject = stream
  try {
    await video.play()
  } catch {
    /* a paused element still yields frames once metadata lands */
  }
  return video
}

/** Paint the current frame of *video* onto a canvas. `maxEdge > 0` downscales the
 *  longest edge to it; 0 keeps native resolution. Returns null when there is no frame
 *  yet (metadata not in) or no 2D context. */
export function drawFrame(video: HTMLVideoElement, maxEdge = 0): HTMLCanvasElement | null {
  const w = video.videoWidth
  const h = video.videoHeight
  if (!w || !h) return null
  const scale = maxEdge > 0 ? Math.min(1, maxEdge / Math.max(w, h)) : 1
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(w * scale))
  canvas.height = Math.max(1, Math.round(h * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  return canvas
}

/** One captured frame, or why there isn't one. */
export type OneFrame = { frame: HTMLCanvasElement } | { error: AcquireFailure }

/**
 * Grab exactly ONE frame and end the capture.
 *
 * The tracks are stopped in a `finally`, not on the happy path: a draw that throws
 * must not be able to leave a live capture — and the browser's capture indicator lit —
 * behind an overlay the user has already dismissed. "One frame, then nothing" is the
 * whole difference between this and screen sharing, so it is structural here rather
 * than a call-site convention.
 *
 * DESKTOP-CAPABILITIES S2 seam. When the Electron shell ships its capability bridge
 * (`window.pclawDesktop.capabilities`, registry entry `screen_capture`: `probe()` for
 * the OS grant state, `request()` to raise the prompt), THIS function is the swap
 * point — probe the bridge first and, where `screen_capture` is granted, take the
 * shell's consent-gated native picker instead of `acquireDisplayStream`. Everything
 * downstream (crop overlay, PNG blob, upload, attachment chip) is provider-agnostic
 * and does not change, and the composer entry point stays exactly where it is:
 * CHAT-CRAFT owns the entry, DESKTOP-CAPABILITIES owns the bridge.
 */
export async function grabOneFrame(): Promise<OneFrame> {
  const acquired = await acquireDisplayStream()
  if (typeof acquired === 'string') return { error: acquired }
  try {
    const video = await frameSource(acquired)
    const frame = drawFrame(video)
    video.srcObject = null
    return frame ? { frame } : { error: 'failed' }
  } catch {
    return { error: 'failed' }
  } finally {
    stopStream(acquired)
  }
}

/**
 * The two boxes a crop preview needs: where the selection sits over the frame, and how
 * to offset a SECOND copy of the frame inside it so the selected pixels show through at
 * full brightness.
 *
 * Percentages only, because the frame is laid out to fit the viewport rather than at
 * native size — so every number here must survive an arbitrary display scale.
 *
 * 🪤 The offset is a `transform`, NOT margins. A percentage `margin-top` resolves
 * against the containing block's INLINE size (its width), so `marginTop: -300%` on a
 * 90px-tall crop displaces the copy by 300% of its *width* — measured in a real browser:
 * the selection rendered exactly as dark as the area outside it, i.e. the feature looked
 * broken in the one way its own screenshot could not explain. `transform: translate()`
 * percentages resolve against the element's OWN box, which is the frame itself, so the
 * two axes behave the same.
 */
export function cropViewStyle(rect: SnipRect, width: number, height: number): {
  selection: { left: string; top: string; width: string; height: string }
  image: { width: string; height: string; transform: string }
} {
  const w = Math.max(1, width)
  const h = Math.max(1, height)
  const rw = Math.max(1, rect.width)
  const rh = Math.max(1, rect.height)
  const pct = (v: number, total: number) => `${(v / total) * 100}%`
  return {
    selection: { left: pct(rect.x, w), top: pct(rect.y, h), width: pct(rect.width, w), height: pct(rect.height, h) },
    image: {
      width: pct(w, rw),
      height: pct(h, rh),
      transform: `translate(${pct(-rect.x, w)}, ${pct(-rect.y, h)})`,
    },
  }
}

/** Crop *source* to *rect* and encode it as a PNG `File`, ready for the ordinary
 *  upload pipeline. PNG (not JPEG) because the payload is text on a screen and OCR
 *  reads crisp edges; a lossy re-encode is exactly the wrong trade here. */
export async function cropToPngFile(
  source: HTMLCanvasElement,
  rect: SnipRect,
  name?: string,
): Promise<File | null> {
  const w = Math.max(1, Math.round(rect.width))
  const h = Math.max(1, Math.round(rect.height))
  const out = document.createElement('canvas')
  out.width = w
  out.height = h
  const ctx = out.getContext('2d')
  if (!ctx) return null
  ctx.drawImage(source, Math.round(rect.x), Math.round(rect.y), w, h, 0, 0, w, h)
  const blob = await new Promise<Blob | null>((resolve) => {
    try {
      out.toBlob((b) => resolve(b), 'image/png')
    } catch {
      resolve(null)
    }
  })
  if (!blob) return null
  return new File([blob], name || `screen-snip-${Date.now()}.png`, { type: 'image/png' })
}
