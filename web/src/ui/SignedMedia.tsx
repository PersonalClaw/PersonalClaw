/** Media and links the browser loads by itself, with their gateway URL signed (`lib/signedUrl.ts`).
 *
 *  Each is the plain element with `src`, `data` or `href` passed through `useSignedSource`. On a
 *  gateway that doesn't pair its sign-ins that is the URL as given, from the first render, so the
 *  element is exactly what it was. On one that does, the element waits for the signature rather
 *  than loading a URL the gateway would refuse, and a load that fails is signed again once before
 *  the caller's own `onError` hears of it. */
import { forwardRef, useImperativeHandle, useRef, type AnchorHTMLAttributes, type ComponentProps, type AudioHTMLAttributes, type ImgHTMLAttributes, type ObjectHTMLAttributes, type SyntheticEvent, type VideoHTMLAttributes } from 'react'
import { useKeepsPlaying, useSignedSource, useSignedUrl, type SignedSource } from '../lib/signedUrl'
import { TextLink } from './TextLink'

/** An `onError` that signs the source again first, and passes the failure on only when that gave
 *  the element nothing new to try. With no retry to run (nothing signs here, the URL isn't the
 *  gateway's, the gateway declined to sign it, or the one retry is used up) the caller hears at
 *  once, inside the event. After a retry it hears later, so it gets an event whose
 *  `currentTarget` is still the element: React empties the original's once the dispatch ends. */
function signedAgainFirst<E>(signed: SignedSource, onError: ((event: SyntheticEvent<E>) => void) | undefined) {
  return (event: SyntheticEvent<E>) => {
    if (!signed.canRetry()) {
      onError?.(event)
      return
    }
    const later = Object.create(event, { currentTarget: { value: event.currentTarget } }) as SyntheticEvent<E>
    signed.retry().then(
      (swapped) => { if (!swapped) onError?.(later) },
      () => { onError?.(later) },
    )
  }
}

export function SignedImg({ src, alt = '', onError, ...rest }: ImgHTMLAttributes<HTMLImageElement> & { src: string }) {
  const signed = useSignedSource(src)
  // eslint-disable-next-line jsx-a11y/alt-text -- the caller's `alt`, passed through
  return signed.src ? <img src={signed.src} alt={alt} onError={signedAgainFirst(signed, onError)} {...rest} /> : null
}

export const SignedVideo = forwardRef<HTMLVideoElement, VideoHTMLAttributes<HTMLVideoElement> & { src: string }>(
  function SignedVideo({ src, onError, ...rest }, outer) {
    const ref = useRef<HTMLVideoElement | null>(null)
    useImperativeHandle(outer, () => ref.current as HTMLVideoElement)
    const signed = useSignedSource(src)
    useKeepsPlaying(ref, signed.src)
    // eslint-disable-next-line jsx-a11y/media-has-caption -- the caller decides its tracks
    return signed.src ? <video ref={ref} src={signed.src} onError={signedAgainFirst(signed, onError)} {...rest} /> : null
  },
)

export const SignedAudio = forwardRef<HTMLAudioElement, AudioHTMLAttributes<HTMLAudioElement> & { src: string }>(
  function SignedAudio({ src, onError, ...rest }, outer) {
    const ref = useRef<HTMLAudioElement | null>(null)
    useImperativeHandle(outer, () => ref.current as HTMLAudioElement)
    const signed = useSignedSource(src)
    useKeepsPlaying(ref, signed.src)
    // eslint-disable-next-line jsx-a11y/media-has-caption -- the caller decides its tracks
    return signed.src ? <audio ref={ref} src={signed.src} onError={signedAgainFirst(signed, onError)} {...rest} /> : null
  },
)

export function SignedObject({ data, children, onError, ...rest }: ObjectHTMLAttributes<HTMLObjectElement> & { data: string }) {
  const signed = useSignedSource(data)
  return signed.src ? <object data={signed.src} onError={signedAgainFirst(signed, onError)} {...rest}>{children}</object> : null
}

/** A link the browser follows itself (a download, a new tab). Until it is signed it is drawn
 *  without an `href`, so a press can't open the unsigned URL. */
export function SignedAnchor({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const signed = useSignedUrl(href)
  return <a href={signed} aria-disabled={signed ? undefined : true} {...rest}>{children}</a>
}

/** `TextLink` to a URL the browser follows itself, signed the same way. */
export function SignedTextLink({ href, ...rest }: ComponentProps<typeof TextLink> & { href: string }) {
  const signed = useSignedUrl(href)
  return <TextLink href={signed} {...rest} />
}
