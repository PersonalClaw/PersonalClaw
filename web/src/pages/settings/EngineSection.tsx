import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Circle, Cpu, Loader2, XCircle } from 'lucide-react'
import { api, type SidecarInstallStatus, type SidecarInstallStep } from '../../lib/api'
import { Button } from '../../ui/Button'
import { FieldError } from '../../ui/forms'

/** How often the section re-reads the install while it runs. */
const POLL_MS = 1500

const msg = (e: unknown) => String((e as Error)?.message || e)

/** *text* ending in exactly one full stop. */
const sentence = (text: string) => `${text.trim().replace(/[.\s]+$/, '')}.`

/** How long a step has been running, for "Installing … (for 4 min)". */
function elapsed(startedAt: number, now: number): string {
  if (!startedAt) return ''
  const secs = Math.max(0, Math.round(now / 1000 - startedAt))
  if (secs < 60) return `${secs} s`
  const mins = Math.floor(secs / 60)
  return mins < 60 ? `${mins} min` : `${Math.floor(mins / 60)} h ${mins % 60} min`
}

function StepIcon({ status }: { status: SidecarInstallStep['status'] }) {
  if (status === 'running') return <Loader2 size={13} className="mt-0.5 shrink-0 animate-spin" aria-hidden="true" />
  if (status === 'done' || status === 'skipped') return <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-primary" aria-hidden="true" />
  if (status === 'error') return <AlertTriangle size={13} className="mt-0.5 shrink-0 text-danger" aria-hidden="true" />
  if (status === 'cancelled') return <XCircle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
  return <Circle size={13} className="mt-0.5 shrink-0 opacity-50" aria-hidden="true" />
}

/** One step, in words. The weights step is left out: models are downloaded in Settings → Models. */
function stepLine(step: SidecarInstallStep, packages: string, now: number): string {
  const running = step.status === 'running'
  const took = running ? elapsed(step.started_at, now) : ''
  if (step.name === 'venv') {
    if (step.status === 'skipped') return 'Its Python environment is there'
    return running ? 'Making its Python environment…' : 'Its Python environment'
  }
  if (step.status === 'skipped') return `${packages} already installed`
  if (running) return `Installing ${packages}…${took ? ` (for ${took})` : ''}`
  return `Installs ${packages}`
}

/** An app's ENGINE: what a sidecar provider runs in a child process, in a Python environment of the
 *  app's own (`apps/<app>/venv`). Install engine puts the manifest's `sidecarDependencies` there with
 *  pip, which can take a long time for a torch-sized engine, so the section shows pip's output as it
 *  runs and offers Cancel. The Providers card and the app's Configure page both render this.
 *
 *  Renders nothing when the app declares no engine packages: there is nothing for it to install. */
export function EngineSection({ app, displayName, onInstalled }: {
  /** The app's name, which is its provider's name. */
  app: string
  displayName: string
  /** After an install finishes: the gateway has started re-measuring the app, so re-read it. */
  onInstalled?: () => void
}) {
  const [status, setStatus] = useState<SidecarInstallStatus | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<'' | 'install' | 'cancel' | 'remove'>('')
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const load = useCallback(async () => {
    try {
      const s = await api.sidecarInstallStatus(app)
      setStatus(s); setLoadErr(''); setNow(Date.now())
      return s
    } catch (e) {
      setLoadErr(msg(e))
      return null
    }
  }, [app])

  useEffect(() => { void load() }, [load])

  const running = status?.job.state === 'running' || status?.job.state === 'queued'
  // Follow a running install until it ends, then say so to the page that holds this section.
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => {
      void load().then((s) => {
        if (s && s.job.state === 'done') onInstalled?.()
      })
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [running, load, onInstalled])

  const install = async () => {
    setBusy('install'); setErr('')
    try {
      await api.startSidecarInstall(app)
      await load()
    } catch (e) {
      setErr(`Couldn't start the install: ${msg(e)}`)
    } finally {
      setBusy('')
    }
  }

  const cancel = async () => {
    if (!status?.job.id) return
    setBusy('cancel'); setErr('')
    try {
      await api.cancelModelDownload(status.job.id)
      await load()
    } catch (e) {
      setErr(`Couldn't cancel the install: ${msg(e)}`)
    } finally {
      setBusy('')
    }
  }

  const remove = async () => {
    setBusy('remove'); setErr('')
    try {
      await api.deleteSidecarInstall(app)
      setConfirmRemove(false)
      await load()
      onInstalled?.()
    } catch (e) {
      setErr(`Couldn't remove the engine: ${msg(e)}`)
    } finally {
      setBusy('')
    }
  }

  if (!status && loadErr) return <FieldError>{`Couldn't read ${displayName}'s engine: ${loadErr}`}</FieldError>
  if (!status) return <div data-type="body-s" className="text-on-surface-low">Checking its engine…</div>
  if (status.requirements.length === 0) return null

  const packages = status.requirements.join(', ')
  const steps = status.job.steps.filter((s) => s.name !== 'weights')
  const stopped = steps.find((s) => s.status === 'error' || s.status === 'cancelled')
  const failed = !running && !status.installed && stopped
  const retry = failed && stopped?.status === 'error'
  const tail = status.job.log_tail.slice(-6)

  return (
    <section aria-label={`${displayName} engine`} className="mt-2 flex flex-col gap-s rounded-md border border-outline-variant bg-surface-high p-m">
      <div data-type="label-l" className="flex items-center gap-2 text-on-surface"><Cpu size={14} aria-hidden="true" /> Engine</div>

      {status.installed && !running ? (
        <div data-type="body-s" className="text-on-surface-var">
          Installed: <span className="font-mono text-on-surface">{packages}</span>, in {displayName}'s own Python environment.
        </div>
      ) : !running && !failed ? (
        <div data-type="body-s" className="text-on-surface-var">
          {displayName} runs its engine in a Python environment of its own. Install engine puts{' '}
          <span className="font-mono text-on-surface">{packages}</span> there with pip. A large engine can take a long
          time to download, and you can leave this page while it installs.
        </div>
      ) : null}

      {(running || failed) && (
        <ul className="flex flex-col gap-xs" aria-label="Install steps">
          {steps.map((s) => (
            <li key={s.name} data-type="body-s" className="flex items-start gap-2 text-on-surface-var">
              <StepIcon status={s.status} />
              <span>{stepLine(s, packages, now)}</span>
            </li>
          ))}
        </ul>
      )}

      {running && tail.length > 0 && (
        <pre aria-label="What pip is doing" data-type="caption"
          className="max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-container p-s font-mono text-on-surface-low">
          {tail.join('\n')}
        </pre>
      )}

      {failed && (
        <div role="alert" data-type="body-s" className="flex items-start gap-2 text-on-surface-var">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            {stopped?.status === 'cancelled' ? 'The install was cancelled.' : `The install stopped: ${sentence(status.job.error)}`}
            {status.job.remediation ? ` ${status.job.remediation}` : ''}
          </span>
        </div>
      )}

      {err && <FieldError>{err}</FieldError>}

      <div className="flex flex-wrap items-center gap-s">
        {running ? (
          <Button size="sm" variant="ghost" loading={busy === 'cancel'} disabled={!status.job.id}
            disabledReason={!status.job.id ? 'The install is starting' : undefined}
            onClick={cancel} ariaLabel={`Cancel the engine install: ${displayName}`}>
            Cancel
          </Button>
        ) : !status.installed ? (
          <Button size="sm" variant="primary" loading={busy === 'install'} onClick={install}
            ariaLabel={`${retry ? 'Try the engine install again' : 'Install engine'}: ${displayName}`}>
            {retry ? 'Try again' : 'Install engine'}
          </Button>
        ) : status.managed && (
          confirmRemove ? (
            <>
              <span data-type="body-s" className="text-on-surface-var">Remove it? This deletes {status.install_dir}.</span>
              <Button size="sm" variant="danger" loading={busy === 'remove'} onClick={remove}
                ariaLabel={`Remove the engine: ${displayName}`}>Remove</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmRemove(false)}>Keep it</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirmRemove(true)}
              ariaLabel={`Remove engine: ${displayName}`}>Remove engine</Button>
          )
        )}
      </div>
    </section>
  )
}
