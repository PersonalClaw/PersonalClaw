import { useEffect, useState } from 'react'
import { Pin, PinOff } from 'lucide-react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { failureSentence, reportActionFailure } from '../../app/reportingWrite'
import { QuietButton } from '../../ui/QuietButton'

/** Pin the open artifact to Home, or unpin it — the control Home's "Pinned artifacts" card sends
 *  people to ("Pin one from its page to keep it here").
 *
 *  Offered on every artifact, a read-only record included: a pin is a bookmark in Home's list
 *  (`entity_settings/pinned_artifacts.json`), not a write to the artifact. Whether this one is
 *  pinned is read from that list, and after a press from the list the write returns, so the label
 *  names what the next press does. When the list cannot be read the control still offers Pin, the
 *  one press that is right either way (pinning a pinned artifact keeps it pinned), and its title
 *  says the read failed rather than claiming the artifact is not pinned. */
export function ArtifactPinButton({ slug, name }: { slug: string; name: string }) {
  const [pinned, setPinned] = useState<boolean | null>(null)
  const [readError, setReadError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    setPinned(null)
    setReadError(null)
    api.pinnedArtifacts()
      .then(({ pins }) => { if (alive) setPinned(pins.some((p) => p.slug === slug)) })
      .catch((e: unknown) => { if (alive) setReadError(e) })
    return () => { alive = false }
  }, [slug])

  if (pinned === null && !readError) return null

  const toggle = () => {
    const want = pinned !== true
    setBusy(true)
    api.pinArtifact(slug, want)
      .then(({ pins }) => {
        const now = pins.some((p) => p.slug === slug)
        setPinned(now)
        setReadError(null)
        notify(now ? `Pinned ${name} to Home.` : `Unpinned ${name} from Home.`, 'success')
      })
      .catch(reportActionFailure(want ? `pin ${name} to Home` : `unpin ${name} from Home`))
      .finally(() => setBusy(false))
  }

  const title = pinned === true ? 'Take this artifact off Home'
    : pinned === false ? 'Keep this artifact on Home, under Pinned artifacts'
    : `${failureSentence('read your pins', readError)} Pinning keeps it on Home either way.`
  return (
    <QuietButton onClick={toggle} disabled={busy} title={title}>
      {pinned === true ? <PinOff size={13} /> : <Pin size={13} />} {pinned === true ? 'Unpin from Home' : 'Pin to Home'}
    </QuietButton>
  )
}
