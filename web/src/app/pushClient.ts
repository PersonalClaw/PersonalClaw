// ── Browser side of content-free push (MOBILE-COMPANION MC-5) ────────────────
//
// The three steps a W3C push subscription needs, and nothing else: is it
// supported, ask permission, subscribe and hand the result to the gateway.
//
// Kept out of the component so each step is testable without a DOM event, and
// out of `sw.ts` because none of it runs in the worker — the page subscribes, the
// worker only receives.

import { api } from '../lib/api'

/** Where this browser's push identity lives.
 *
 *  A push subscription belongs to a BROWSER PROFILE, not to a person and not to a
 *  paired device: the same phone running both the installed PWA and Safari holds
 *  two subscriptions, and one paired-device id could not name them apart. So the
 *  id is minted here, stored locally, and stable across reloads. It is not a
 *  credential — the session cookie authenticates the subscribe call; this only
 *  says which row to overwrite when the browser re-subscribes. */
const DEVICE_ID_KEY = 'personalclaw:push:device_id'

/** A stable per-profile id, minted on first use. */
export function pushDeviceId(): string {
  try {
    const existing = localStorage.getItem(DEVICE_ID_KEY)
    if (existing) return existing
    const minted = `web-${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(DEVICE_ID_KEY, minted)
    return minted
  } catch {
    // Private mode / storage disabled. A volatile id still subscribes correctly;
    // it just means the next reload registers a fresh row rather than replacing
    // this one, which the 404/410 prune on the backend eventually cleans up.
    return `web-${Math.random().toString(36).slice(2, 10)}`
  }
}

/** True when this browser can hold a push subscription at all.
 *
 *  Three separate capabilities, all required, and iOS Safari has historically
 *  shipped them apart (Notification without PushManager) — so a single check on
 *  `serviceWorker` would offer a button that throws when pressed. */
export function pushSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof window !== 'undefined' &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/** VAPID keys travel as base64url; `applicationServerKey` wants raw bytes.
 *
 *  Returns the backing `ArrayBuffer`, not the view: TS 5.7 narrowed
 *  `BufferSource` to `ArrayBufferView<ArrayBuffer>`, which a plain `Uint8Array`
 *  (typed over `ArrayBufferLike`) no longer satisfies. The buffer is exactly the
 *  65 decoded bytes because the view was allocated at that length. */
export function decodeVapidKey(base64Url: string): ArrayBuffer {
  const padded = base64Url + '='.repeat((4 - (base64Url.length % 4)) % 4)
  const binary = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

export type EnableResult =
  | { ok: true; deviceId: string }
  | { ok: false; reason: 'unsupported' | 'denied' | 'no-key' | 'failed'; detail?: string }

/** Subscribe this browser and register the subscription with the gateway.
 *
 *  Must be called from a user gesture: `requestPermission()` is gesture-gated in
 *  every current browser, and a call outside one resolves 'default' forever with
 *  no prompt and no error — which reads as "the button does nothing". */
export async function enablePush(vapidPublicKey: string): Promise<EnableResult> {
  if (!pushSupported()) return { ok: false, reason: 'unsupported' }
  if (!vapidPublicKey) return { ok: false, reason: 'no-key' }
  try {
    const permission = await Notification.requestPermission()
    if (permission !== 'granted') return { ok: false, reason: 'denied' }
    const registration = await navigator.serviceWorker.ready
    const subscription = await registration.pushManager.subscribe({
      // Required true by Chrome, and honest: every push this backend sends
      // results in a visible notification (`sw.ts` always calls
      // showNotification, including on a malformed payload).
      userVisibleOnly: true,
      applicationServerKey: decodeVapidKey(vapidPublicKey),
    })
    const deviceId = pushDeviceId()
    await api.pushSubscribe(deviceId, subscription.toJSON())
    return { ok: true, deviceId }
  } catch (err) {
    return { ok: false, reason: 'failed', detail: (err as Error)?.message || '' }
  }
}

/** Drop this browser's subscription, on both sides.
 *
 *  Unsubscribes locally FIRST: if the gateway call fails, a stale row on the
 *  server is a ping into the void, while a live local subscription the server
 *  forgot would keep the browser believing push is on. */
export async function disablePush(): Promise<boolean> {
  try {
    const registration = await navigator.serviceWorker.ready
    const subscription = await registration.pushManager.getSubscription()
    if (subscription) await subscription.unsubscribe()
  } catch {
    // Nothing to unsubscribe locally; still tell the gateway to forget the row.
  }
  try {
    await api.pushUnsubscribe(pushDeviceId())
    return true
  } catch {
    return false
  }
}
