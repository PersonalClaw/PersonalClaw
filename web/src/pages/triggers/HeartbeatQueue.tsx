import { useEffect, useState } from 'react'
import { Button } from '../../ui/Button'
import { FieldError } from '../../ui/forms'
import { InlineLoadError } from '../../ui/ListScaffold'
import { api, type HeartbeatTask } from '../../lib/api'

/** The tasks in HEARTBEAT.md, on the Heartbeat tasks trigger's panel (`heartbeat.queued`).
 *
 *  The agent writes this file to keep checking on something, and a task runs only once the owner
 *  allowed it (`heartbeat.py`), as a trigger the chat makes does. So each task says which it is,
 *  and a waiting one has Allow — the gateway asks first, and the yes is to the task as written, so
 *  an edit to it waits again. Re-read on every pass (`reloadKey`), since a pass removes the tasks
 *  it finished. */
export function HeartbeatQueue({ reloadKey }: { reloadKey: number | string }) {
  const [tasks, setTasks] = useState<HeartbeatTask[] | null>(null)
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [retry, setRetry] = useState(0)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    let alive = true
    api.heartbeatTasks().then((t) => { if (alive) { setTasks(t); setLoadErr(null) } })
      .catch((e) => { if (alive) { setLoadErr(e); setTasks(null) } })
    return () => { alive = false }
  }, [reloadKey, retry])

  async function allow(task: HeartbeatTask) {
    setBusy(task.text); setErr('')
    try { await api.allowHeartbeatTask(task.text); setRetry((n) => n + 1) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not allow this task') }
    finally { setBusy('') }
  }

  // The error comes before the empty state, so a queue that could not be read is never reported
  // as an empty one.
  if (loadErr) return <Section label="Queued tasks"><InlineLoadError what="the heartbeat tasks" error={loadErr} onRetry={() => setRetry((n) => n + 1)} /></Section>
  if (tasks === null) return <Section label="Queued tasks"><div className="text-on-surface-low text-[0.8125rem]">Loading…</div></Section>
  if (tasks.length === 0) {
    return (
      <Section label="Queued tasks">
        <div className="text-on-surface-low text-[0.8125rem]">No tasks queued. Your agent adds one when it needs to keep checking on something.</div>
      </Section>
    )
  }
  return (
    <Section label={`Queued tasks · ${tasks.length}`}>
      <p className="mb-2 text-on-surface-var text-[0.8125rem]">
        A task runs only once you allow it. One you add in the Files editor is allowed as you save it.
      </p>
      <ul className="flex flex-col gap-1.5">
        {tasks.map((t, i) => (
          <li key={`${i}:${t.text}`} className="flex items-start gap-s rounded-md bg-surface-container px-m py-2">
            <div className="min-w-0 flex-1">
              <div className="text-on-surface text-[0.8125rem] whitespace-pre-wrap break-words">{t.text}</div>
              <div className="mt-0.5 text-on-surface-low text-[0.75rem]">
                {t.allowed ? 'Allowed — runs with your agent’s tools' : 'Waiting for your Allow — it does not run until you allow it'}
                {t.deliver && <> · result goes to <span className="font-mono">{t.deliver}</span></>}
              </div>
            </div>
            {!t.allowed && (
              <Button size="sm" variant="secondary" onClick={() => allow(t)} loading={busy === t.text} disabled={busy !== ''}
                ariaLabel={`Allow the queued task: ${t.text}`}>Allow</Button>
            )}
          </li>
        ))}
      </ul>
      {err && <FieldError>{err}</FieldError>}
    </Section>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
