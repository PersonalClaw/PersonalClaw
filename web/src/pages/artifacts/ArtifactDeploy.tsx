import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ExternalLink, FolderOpen, PanelTop, PowerOff, Rocket } from 'lucide-react'
import { api, type ArtifactDeployment, type ArtifactKind } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { QuietButton } from '../../ui/QuietButton'
import { Popover } from '../../ui/Popover'

/** Kinds that can be deployed — mirrors `DEPLOYABLE_KINDS` in
 *  `artifacts/deploy.py`. A markdown/image artifact already has a reader; "deploy" on
 *  it would only be a control that always 400s. */
const DEPLOYABLE: ReadonlySet<string> = new Set(['widget', 'html', 'react'])

export function isDeployableKind(kind: ArtifactKind | string): boolean {
  return DEPLOYABLE.has(String(kind))
}

/** Deploy / Open / Tear down for one artifact.
 *
 *  Deploying publishes the artifact at an in-gateway URL of its own, which carries the
 *  deployment's capability (every deploy mints a new one, and tear down revokes it). The
 *  page is model-authored, so the gateway serves it sandboxed into an opaque origin under a
 *  strict CSP fence (`connect-src 'none'` — it cannot call `/api`): it runs its own scripts
 *  and reaches nothing of the dashboard's. "Preview" embeds that URL in a pane so the page
 *  can be driven without leaving the app; "Open" is the same URL in a new tab. "Tear down"
 *  removes the route and touches no content — it un-publishes, so it is a plain action
 *  rather than a destructive one. */
export function ArtifactDeploy({ slug, kind }: { slug: string; kind: ArtifactKind | string }) {
  const [dep, setDep] = useState<ArtifactDeployment | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState(false)
  // Bumped on every (re)deploy so the embedded pane re-fetches instead of showing the
  // body from before the last edit — the serve response is `no-store`, but a live
  // iframe keeps whatever it already rendered.
  const [nonce, setNonce] = useState(0)

  const refresh = useCallback(async () => {
    try {
      const rows = await api.deployedArtifacts()
      setDep(rows.find((r) => r.slug === slug) ?? null)
    } catch { setDep(null) }
    finally { setLoaded(true) }
  }, [slug])
  useEffect(() => { setPreview(false); refresh() }, [refresh])

  if (!isDeployableKind(kind)) return null

  const deploy = async () => {
    if (busy) return
    setBusy(true)
    try {
      const r = await api.deployArtifact(slug)
      setDep(r.deployment); setNonce((n) => n + 1); setPreview(true)
    } catch (e) { notify(`Could not deploy: ${(e as Error).message}`, 'error') }
    finally { setBusy(false) }
  }
  const teardown = async () => {
    if (busy) return
    setBusy(true)
    try {
      await api.teardownArtifact(slug)
      setDep(null); setPreview(false)
    } catch (e) { notify(`Could not tear down: ${(e as Error).message}`, 'error') }
    finally { setBusy(false) }
  }

  if (!loaded) return null

  return (
    <>
      <div className="flex flex-wrap items-center gap-s border-b border-outline/40 px-m py-1.5 text-[0.75rem]">
        {dep ? (
          <>
            <Rocket size={13} className="shrink-0 text-primary" />
            <span className="text-on-surface-low">Deployed at</span>
            <code className="truncate rounded bg-surface-high px-1.5 py-0.5 font-mono text-on-surface">{dep.url}</code>
            <div className="ml-auto flex items-center gap-1">
              <QuietButton onClick={() => setPreview((v) => !v)} ariaExpanded={preview}
                title={preview ? 'Hide the embedded preview' : 'Open the deployed page in a pane here'}>
                <PanelTop size={13} /> {preview ? 'Hide preview' : 'Preview'}
              </QuietButton>
              {/* A plain link, not a scripted window.open: the served page is a document
                  at a real URL, and a link is what middle-click / open-in-new-window expect. */}
              <a href={dep.url} target="_blank" rel="noopener noreferrer"
                className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface"
                title="Open the deployed page in a new tab">
                <ExternalLink size={13} /> Open
              </a>
              <Button variant="ghost" size="xs" loading={busy} onClick={teardown}
                title="Remove the serve route. The artifact itself is not deleted.">
                <PowerOff size={13} /> Tear down
              </Button>
            </div>
          </>
        ) : (
          <>
            <span className="text-on-surface-low">Serve this artifact as a page at its own in-gateway URL.</span>
            <div className="ml-auto">
              <Button variant="ghost" size="xs" loading={busy} onClick={deploy}
                title="Publish this artifact at /artifacts/serve/…">
                <Rocket size={13} /> Deploy
              </Button>
            </div>
          </>
        )}
      </div>
      {dep && preview && (
        <div className="border-b border-outline/40 bg-surface-high/40 p-m">
          {/* `allow-scripts` alone: the framed page runs its own scripts in an opaque origin,
              so `window.parent` is a window it cannot touch, and it gets no popups, no
              top-level navigation and no forms. The serve response sets the same `sandbox`
              directive itself, which is what holds when the page is opened anywhere else; this
              attribute holds it here even before the response says so. Its own files still
              load: the URL's capability authorizes them, since a sandboxed page's requests
              carry no session cookie. */}
          <iframe key={nonce} src={dep.url} sandbox="allow-scripts" title={`Deployed artifact: ${slug}`}
            className="h-[28rem] w-full rounded-md border border-outline/40 bg-surface" />
        </div>
      )}
    </>
  )
}

/** The deployed-app listing: every currently-served artifact with its URL, plus open
 *  and tear-down. Lives in the library toolbar because "what of mine is currently
 *  serving?" is a library-level question, not a per-artifact one. */
export function DeployedAppsMenu({ onOpen, onChanged }: {
  onOpen: (slug: string) => void
  onChanged?: () => void
}) {
  const [rows, setRows] = useState<ArtifactDeployment[]>([])

  const refresh = useCallback(async () => {
    try { setRows(await api.deployedArtifacts()) } catch { setRows([]) }
  }, [])
  useEffect(() => { refresh() }, [refresh])

  // Absent rather than an empty menu: a control that opens onto "nothing here" is
  // noise in a toolbar that already carries five filters.
  if (rows.length === 0) return null

  const teardown = async (slug: string) => {
    try {
      await api.teardownArtifact(slug)
      await refresh(); onChanged?.()
    } catch (e) { notify(`Could not tear down: ${(e as Error).message}`, 'error') }
  }

  return (
    // `portal`: the toolbar row this trigger sits in clips its overflow, so an
    // in-flow menu would be cut off at the row's edge.
    <Popover placement="bottom" portal trigger={(open, toggle) => (
      <QuietButton onClick={toggle} ariaExpanded={open} title="Artifacts currently served as pages">
        <Rocket size={13} /> Deployed ({rows.length}) <ChevronDown size={11} />
      </QuietButton>
    )}>
      {() => (
        <div className="min-w-[20rem] p-1">
          {rows.map((r) => (
            <div key={r.slug} className="flex items-center gap-s rounded-md px-2 py-1.5 text-[0.75rem] hover:bg-surface-high">
              {/* The row's primary action is the served page itself, so the row IS the link. */}
              <a href={r.url} target="_blank" rel="noopener noreferrer"
                className="min-w-0 flex-1 truncate text-on-surface" title={`Open ${r.slug} in a new tab`}>
                {r.slug}
                <span className="ml-1.5 truncate font-mono text-on-surface-low">{r.url}</span>
              </a>
              <IconButton icon={FolderOpen} size={24} iconSize={12} onClick={() => onOpen(r.slug)}
                label={`Open ${r.slug} in the library`} title="Open in the library" />
              <IconButton icon={PowerOff} size={24} iconSize={12} onClick={() => teardown(r.slug)}
                label={`Tear down ${r.slug}`} title="Tear down (the artifact is kept)" />
            </div>
          ))}
        </div>
      )}
    </Popover>
  )
}
