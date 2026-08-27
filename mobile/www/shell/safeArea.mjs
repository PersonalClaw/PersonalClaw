/**
 * Native safe-area insets, applied rather than merely declared.
 *
 * There are two documents to keep out from under a notch, and they need different mechanisms:
 *
 * 1. **The served companion**, once the WebView has navigated to the gateway. The shell owns no
 *    CSS there — it is the gateway's document, and `web/` declares no `env(safe-area-inset-*)`
 *    anywhere and no `viewport-fit=cover`. So that page is inset **natively**, by the two keys
 *    in `capacitor.config.json`: `android.adjustMarginsForEdgeToEdge` (Android draws
 *    edge-to-edge by default from API 35, and this margins the WebView back inside the system
 *    bars and the display cutout) and `ios.contentInset` (the WKWebView's scroll-view content
 *    inset). Neither is something JavaScript can observe, so `tests/test_mobile_shell.py`
 *    asserts they are shipped and a real device is the only proof they render — recorded as
 *    such in the plan's PARTIAL rather than claimed.
 *
 * 2. **The bootstrap screen** in this directory, which *is* the shell's document — and which is
 *    also the only place safe areas can be proven by a test that runs in CI. `shell.css`
 *    declares the four `env()` values as custom properties; `applySafeAreaInsets` below reads
 *    the computed values and writes them onto the layout element as padding. That split is
 *    what makes it falsifiable: CSS alone would be a declaration nothing consumes, and the unit
 *    test can watch the padding appear on a fake element.
 */

/** The custom properties `shell.css` resolves `env(safe-area-inset-*)` into. */
export const SAFE_AREA_VARS = Object.freeze({
  top: '--pc-safe-top',
  right: '--pc-safe-right',
  bottom: '--pc-safe-bottom',
  left: '--pc-safe-left',
})

/** What a platform without insets (or a browser preview) reports. */
const NO_INSET = '0px'

/**
 * The four resolved insets, as CSS lengths.
 *
 * Reads the custom properties rather than `env()` directly, because `env()` is not readable
 * from script — a stylesheet has to resolve it into something `getComputedStyle` can see.
 */
export function readSafeAreaInsets(doc, view) {
  const root = doc?.documentElement
  if (!root || typeof view?.getComputedStyle !== 'function') {
    return { top: NO_INSET, right: NO_INSET, bottom: NO_INSET, left: NO_INSET }
  }
  const computed = view.getComputedStyle(root)
  const read = (name) => {
    const value = String(computed.getPropertyValue(name) ?? '').trim()
    return value || NO_INSET
  }
  return {
    top: read(SAFE_AREA_VARS.top),
    right: read(SAFE_AREA_VARS.right),
    bottom: read(SAFE_AREA_VARS.bottom),
    left: read(SAFE_AREA_VARS.left),
  }
}

/**
 * Write the resolved insets onto `target` as padding, and return what was written.
 *
 * Padding and not margin: the bootstrap screen paints a full-bleed background, and a margin
 * would leave the notch strip unpainted.
 */
export function applySafeAreaInsets(doc, view, target) {
  const insets = readSafeAreaInsets(doc, view)
  if (target?.style) {
    target.style.paddingTop = insets.top
    target.style.paddingRight = insets.right
    target.style.paddingBottom = insets.bottom
    target.style.paddingLeft = insets.left
  }
  return insets
}

/**
 * Apply now and on every event that can change an inset.
 *
 * Rotation and the software keyboard both move the insets, and a shell that read them once at
 * startup would be correct only in the orientation the app happened to launch in.
 */
export function watchSafeAreaInsets(doc, view, target) {
  const reapply = () => applySafeAreaInsets(doc, view, target)
  reapply()
  for (const event of ['resize', 'orientationchange']) {
    view?.addEventListener?.(event, reapply)
  }
  return reapply
}
