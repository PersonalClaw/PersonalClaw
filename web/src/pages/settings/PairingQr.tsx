import { useMemo } from 'react'
import { QrCode, XCircle } from 'lucide-react'
import { QUIET_ZONE, encodeQr, qrPath } from '../../lib/qr'

/** The scannable form of a pairing payload (`MC-8`, rendering COMPANION-APPS §C2).
 *
 *  The QR is a RENDERING of `pair/start`'s `pairing_url` and nothing else — the code rides inside
 *  that URL, which is what makes one scan enough. Nothing is composed here: composing the URL in
 *  the browser is the defect §C2 (a) exists to prevent (the dashboard can be open on loopback
 *  while the scanning phone needs the LAN address), so this takes the gateway's string verbatim.
 *
 *  Three states are refusals, and each says which one it is, because the failure mode of a
 *  scannable image is a blank square: a camera that will not lock on, a code that has already run
 *  out, and an address the gateway could not work out are three different problems with three
 *  different fixes, and a grey rectangle is all of them at once.
 */

/** Rendered edge in CSS px. A version-4 symbol plus its quiet zone is 41 units wide, so this
 *  gives ~4.3px per module — comfortably above what a phone camera needs at arm's length. */
const EDGE = 'size-[176px]'

/** The shared frame, so a refusal occupies exactly the space the symbol would have. */
function Plate({ children, dashed }: { children: React.ReactNode; dashed?: boolean }) {
  return (
    <div
      className={`flex ${EDGE} shrink-0 flex-col items-center justify-center gap-2 rounded-lg px-3 text-center ${
        dashed ? 'border border-dashed border-outline-variant' : ''
      }`}
    >
      {children}
    </div>
  )
}

function Refusal({ what, icon: Icon }: { what: string; icon: typeof QrCode }) {
  return (
    <Plate dashed>
      <Icon size={26} className="text-on-surface-low" aria-hidden="true" />
      <span className="text-on-surface-low text-[0.75rem] leading-tight">{what}</span>
    </Plate>
  )
}

export function PairingQr({ url, expired }: { url: string; expired: boolean }) {
  // Encoding is pure and depends only on the URL, so a countdown tick must not redraw ~700
  // squares once a second.
  const symbol = useMemo(() => (url ? encodeQr(url) : null), [url])

  if (expired) {
    // Terse on purpose: the countdown row below already states the expiry and offers the action,
    // and the paragraph beside this one explains the withdrawal. A third full sentence here would
    // be the same fact told three times.
    return <Refusal icon={XCircle} what="Nothing left to scan." />
  }
  if (!url) {
    // `pair/start` returns an empty `pairing_url` when it cannot resolve its own address from the
    // request host. The code beside this still works; the scan does not.
    return <Refusal icon={QrCode} what="This gateway could not work out its own address — type the code instead." />
  }
  if (!symbol) {
    return <Refusal icon={QrCode} what="This link is too long to put in a QR code — type the code instead." />
  }

  const span = symbol.size + QUIET_ZONE * 2
  return (
    <Plate>
      {/* NOT themed, deliberately. Contrast polarity is part of the barcode format, not a
          surface style: a symbol that inverted with the colour scheme would stop being a QR
          code in dark mode for any scanner that does not try both polarities. The quiet zone is
          drawn INSIDE the viewBox so the light border survives being copied or screenshotted. */}
      <svg
        viewBox={`0 0 ${span} ${span}`}
        className={`${EDGE} rounded-lg`}
        role="img"
        aria-label="QR code for the pairing link — scan it with the camera on the device you are adding"
        shapeRendering="crispEdges"
      >
        <rect width={span} height={span} fill="white" />
        <path d={qrPath(symbol)} fill="black" />
      </svg>
    </Plate>
  )
}
