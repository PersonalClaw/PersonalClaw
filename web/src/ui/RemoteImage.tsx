import { memo, useEffect, useRef, useState } from 'react'
import { ImageIcon } from 'lucide-react'
import { api, isTransientFailure } from '../lib/api'
import { readableErrText } from '../lib/errText'
import { Button } from './Button'

/** The host an image address names, as the URL parser gives it (the ASCII form of an
 *  international name), or `''` when it is no absolute address. */
function imageHost(src: string): string {
  try { return new URL(src).host } catch { return '' }
}

type State =
  | { at: 'asking' }
  | { at: 'loading' }
  | { at: 'shown'; url: string }
  | { at: 'refused'; why: string; again: boolean }

/** An image from another site, in text the dashboard did not write: a placeholder naming its host
 *  until the owner presses Load. Drawing it fetches nothing. Load asks the gateway to fetch the
 *  image through its network settings (`api.loadRemoteImage`), and shows the bytes from a `blob:`
 *  address, let go when the image leaves the page. A Load still in flight then is let go too. A
 *  refusal says why; one a later press could clear (the gateway was busy, the site was down)
 *  keeps the Load button. The page's own policy loads no image from another host, so this is the
 *  one way such an image is ever shown. */
export const RemoteImage = memo(function RemoteImage({ src, alt }: { src: string; alt: string }) {
  const host = imageHost(src)
  const [state, setState] = useState<State>({ at: 'asking' })
  // Which Load an answer belongs to: bumped when the address changes and when the image leaves
  // the page, so an answer that arrives after either is dropped, and makes no blob address.
  const asked = useRef(0)
  // The Load in flight, let go at the same two moments.
  const inFlight = useRef<AbortController | null>(null)
  const shown = state.at === 'shown' ? state.url : ''
  useEffect(() => (shown ? () => URL.revokeObjectURL(shown) : undefined), [shown])
  // A different address is a different image: ask again.
  useEffect(() => {
    asked.current += 1
    setState({ at: 'asking' })
    return () => {
      asked.current += 1
      inFlight.current?.abort()
      inFlight.current = null
    }
  }, [src])
  if (state.at === 'shown') {
    return (
      <img src={state.url} alt={alt}
        className="my-2 max-h-[28rem] max-w-full rounded-lg border border-outline-variant/40 object-contain" />
    )
  }
  const load = () => {
    const mine = asked.current
    const controller = new AbortController()
    inFlight.current = controller
    setState({ at: 'loading' })
    api.loadRemoteImage(src, controller.signal).then(
      (blob) => {
        if (asked.current !== mine) return
        setState({ at: 'shown', url: URL.createObjectURL(blob) })
      },
      (err: unknown) => {
        if (asked.current !== mine) return
        setState({
          at: 'refused',
          why: readableErrText(err) || "The gateway didn't answer, so nothing was shown.",
          again: isTransientFailure(err),
        })
      },
    )
  }
  const button = (
    <Button variant="secondary" size="xs" onClick={load} loading={state.at === 'loading'}
      ariaLabel={host ? `Load image from ${host}` : 'Load image'} title={src}>
      Load image
    </Button>
  )
  return (
    <span className="my-2 inline-flex max-w-full flex-wrap items-center gap-s rounded-lg border border-outline-variant/40 px-m py-s align-middle">
      <ImageIcon size={16} aria-hidden className="shrink-0 text-on-surface-low" />
      <span className="min-w-0 text-on-surface">{alt || 'Image'}</span>
      <span data-type="caption" className="min-w-0 break-all text-on-surface-low">{host ? `from ${host}` : ''}</span>
      {state.at === 'refused' && <span role="status" data-type="caption" className="min-w-0 text-on-surface-low">{state.why}</span>}
      {(state.at !== 'refused' || state.again) && button}
    </span>
  )
})
