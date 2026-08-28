import { useState } from 'react'
import { KeyRound, Server, FolderLock, Trash2, Plus, Workflow, Zap, Globe } from 'lucide-react'
import { api } from '../../lib/api'
import type { SecretPresenceWire, SecretsVaultState } from '../../lib/api'
import { useQuery } from '../../lib/data/useQuery'
import { Button } from '../../ui/Button'
import { TextInput } from '../../ui/forms'
import { CardGridSkeleton, EmptyState, ListRow, LoadError } from '../../ui/ListScaffold'
import { confirm } from '../../ui/dialog'
import { StatusPill } from './bento'
import { PanelHeader, Row, RowGroup, Section } from './settingsUI'

/** The name field's stable DOM id. `TextInput` publishes `id={name || autoId}`, so passing this as
 *  `name` gives the empty state's on-ramp something to focus — the create surface for this
 *  collection is the form already on the page, not a separate route, so the on-ramp is "put the
 *  cursor in it" rather than a navigation. */
const NAME_FIELD_ID = 'secrets-vault-name'

/** Settings › Secrets — the vault (EI-10).
 *
 *  🔴 **THIS PANEL CANNOT DISPLAY A SECRET, BECAUSE IT NEVER RECEIVES ONE.** `/api/secrets`
 *  answers with `SecretPresenceWire` rows, a type with no value field, built server-side from
 *  credential key NAMES only. So there is no masked-value control here, no "reveal" affordance
 *  and no copy button — not as a policy choice a later change could reverse, but because there is
 *  no endpoint that would answer one. The value field in the add form below is write-only: it
 *  goes out in a POST body and is cleared on success.
 *
 *  🔑 THE READ IS BARE — no `.catch(() => null)`. Same reasoning as `SecurityPanel`'s three reads:
 *  "no secrets stored" is pixel-identical to a failed fetch, and on the page that tells a user
 *  which credentials their automations can reach, that is the one lie it must not tell.
 *
 *  **Three row types, rendered three ways, because their trust stories differ.** Vault rows
 *  (global and per-project) hold a value the vault owns and can rotate or delete. A HOST row's
 *  value lives in the gateway's own environment: the vault can see the name and nothing else, and
 *  cannot remove it. Rendering the third like the first two would tell the user the vault is
 *  managing something it has no control over — so host rows get their own section, their own
 *  glyph, a "from host environment" pill, and a delete control that is disabled WITH the reason.
 */
export function SecretsPanel() {
  const { data: v, error, refresh } = useQuery<SecretsVaultState>(
    'settings:secrets', () => api.secrets(),
  )

  if (!v) {
    return (
      <>
        <PanelHeader title="Secrets" hint="Credentials your workflows and automations can reference." />
        {/* Error branch BEFORE the loading branch — a failed read must not shimmer forever. */}
        {error ? <LoadError what="secrets vault" error={error} onRetry={refresh} />
          : <CardGridSkeleton cards={2} cols={1} what="secrets vault" />}
      </>
    )
  }

  const vault = v.secrets.filter((s) => s.scope !== 'host')
  const globals = vault.filter((s) => s.scope === 'global')
  const host = v.secrets.filter((s) => s.scope === 'host')
  // Grouped by project so a user with three projects reads three short lists rather than one
  // long one whose scope column they have to scan.
  const byProject = new Map<string, SecretPresenceWire[]>()
  for (const s of v.secrets.filter((r) => r.scope === 'project')) {
    byProject.set(s.project_id, [...(byProject.get(s.project_id) ?? []), s])
  }

  return (
    <>
      <PanelHeader
        title="Secrets"
        hint={'Values are write-only: once stored, a secret can be replaced or removed, but never '
          + 'read back — not by this page and not by any API.'}
      />

      <AddSecret onSaved={refresh} />

      {v.secrets.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="No secrets yet"
          // The server composes this sentence so the CLI and the dashboard say the same thing
          // about the same state. It names the NEXT ACTION — "no secrets yet" alone reads as
          // "secrets are broken" on a page whose whole subject is credentials.
          hint={v.empty_hint}
          // A real on-ramp (PEP-2), reaching the same create surface a non-empty vault uses: the
          // form above. It focuses rather than navigates because there is nowhere to navigate to,
          // and a CTA that only scrolled would be decoration.
          action={{
            label: 'Add your first secret',
            icon: Plus,
            onClick: () => document.getElementById(NAME_FIELD_ID)?.focus(),
          }}
        />
      ) : (
        <>
          <Section
            title="Global"
            icon={KeyRound}
            iconTone="muted"
            hint={`${v.counts.global} available to every project on this instance.`}
          >
            {globals.length === 0
              ? <RowGroup><Row label="None stored" hint="Add one above to make it available everywhere." ><span /></Row></RowGroup>
              : <RowGroup>{globals.map((s, i) => <SecretRow key={s.name} s={s} index={i} onChanged={refresh} />)}</RowGroup>}
          </Section>

          {byProject.size > 0 && (
            <Section
              title="Per-project"
              icon={FolderLock}
              iconTone="muted"
              hint={`${v.counts.project} scoped to a single project.`}
            >
              {[...byProject.entries()].map(([pid, rows]) => (
                <div key={pid} className="mb-l last:mb-0">
                  <div className="mb-1 text-on-surface-low text-[0.75rem]">{pid}</div>
                  <RowGroup>
                    {rows.map((s, i) => <SecretRow key={s.name} s={s} index={i} onChanged={refresh} />)}
                  </RowGroup>
                </div>
              ))}
            </Section>
          )}

          {host.length > 0 && (
            <Section
              title="Inherited from the host environment"
              icon={Server}
              iconTone="muted"
              // The trust story, stated where it applies. These rows are detected by NAME SHAPE
              // (the same test the workflow engine uses to decide what to strip from a sandboxed
              // child's environment), so the list is deliberately generous — a name that merely
              // looks credential-bearing is shown, because the runtime already treats it as one.
              hint={`${v.counts.host} credential-shaped variables the gateway inherited from its own `
                + 'environment. The vault holds no copy of these values and cannot change or remove '
                + 'them — edit them where the gateway is launched. Store one above to take ownership.'}
            >
              <RowGroup>
                {host.map((s, i) => <SecretRow key={s.name} s={s} index={i} onChanged={refresh} />)}
              </RowGroup>
            </Section>
          )}
        </>
      )}
    </>
  )
}

/** One vault row: name, scope treatment, and what references it. */
function SecretRow({ s, index, onChanged }: {
  s: SecretPresenceWire
  index: number
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const hostRow = s.inherited_from_host

  const remove = async () => {
    if (!(await confirm({
      title: `Remove ${s.name}?`,
      body: 'The stored value is deleted from the credential store and from this gateway\'s '
        + 'environment. Anything referencing {{secret:' + s.name + '}} will fail until it is '
        + 'replaced — this cannot be undone, because the value cannot be read back out to save it.',
      confirmLabel: 'Remove secret',
      danger: true,
    }))) return
    setBusy(true); setErr('')
    try {
      await api.deleteSecret(s.name, s.project_id)
      onChanged()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Remove failed') }
    finally { setBusy(false) }
  }

  return (
    <ListRow index={index} label={s.name}>
      <div className="flex items-start justify-between gap-l py-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {hostRow ? <Globe size={14} className="text-on-surface-low" aria-hidden />
              : <KeyRound size={14} className="text-primary" aria-hidden />}
            <span className="truncate font-mono text-on-surface text-[0.8125rem]">{s.name}</span>
            {/* `present` is the whole payload of a vault row, so it is stated rather than
                implied by the row existing — the user is reading a presence list, not a
                value list whose values happen to be missing. */}
            {hostRow
              ? <StatusPill label="from host environment" tone="warn" />
              : <StatusPill label={s.present ? 'set' : 'not set'} tone="ok" />}
            {s.scope === 'project' && <StatusPill label="project" tone="primary" />}
          </div>
          {s.consumers.length > 0 ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-on-surface-low text-[0.75rem]">
              <span>Used by</span>
              {s.consumers.map((c) => (
                <span key={`${c.kind}:${c.id}`} className="inline-flex items-center gap-1">
                  {c.kind === 'workflow' ? <Workflow size={12} aria-hidden /> : <Zap size={12} aria-hidden />}
                  <span className="truncate">{c.label || c.id}</span>
                </span>
              ))}
            </div>
          ) : (
            // Said explicitly rather than left blank. A blank "used by" line is ambiguous
            // between "nothing references this" (safe to delete) and "we didn't check".
            <div className="mt-1 text-on-surface-low text-[0.75rem]">
              Not referenced by any workflow or automation.
            </div>
          )}
          {err && <div role="alert" className="mt-1 text-danger text-[0.75rem]">{err}</div>}
        </div>
        {/* `Button`'s own soft-off carrier rather than a spread of `unavailableWhen`: this
            component does not forward arbitrary DOM props, and `disabledReason` is the
            componentized form of the same contract — aria-disabled + an announced reason, so the
            control stays reachable and says why instead of vanishing from the tab order. */}
        <Button
          size="sm"
          variant="danger"
          onClick={remove}
          loading={busy}
          disabled={hostRow}
          disabledReason={hostRow
            ? "This value lives in the gateway's environment, not the vault — unset it where the gateway is launched."
            : undefined}
        >
          <Trash2 size={14} /> Remove
        </Button>
      </div>
    </ListRow>
  )
}

/** The write-only add form. The value leaves in a POST body and is cleared on success. */
function AddSecret({ onSaved }: { onSaved: () => void }) {
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [projectId, setProjectId] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const save = async () => {
    setBusy(true); setErr(''); setNote('')
    try {
      await api.putSecret(name.trim(), value, projectId.trim())
      // Cleared immediately on success. The component holds the value only for as long as it
      // takes to send it; nothing renders it, and nothing re-reads it.
      setValue('')
      setNote(`${name.trim()} stored.`)
      setName(''); setProjectId('')
      onSaved()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Could not store the secret') }
    finally { setBusy(false) }
  }

  const missing = !name.trim() || !value
  return (
    <Section title="Add a secret" icon={Plus} iconTone="muted" hint="Reference it from a workflow or automation as {{secret:NAME}}.">
      <RowGroup>
        <Row label="Name" hint="An environment-variable name — letters, digits and underscores.">
          <TextInput value={name} onChange={setName} name={NAME_FIELD_ID} ariaLabel="Secret name" placeholder="GITHUB_TOKEN" mono size="sm" />
        </Row>
        <Row label="Value" hint="Write-only. It is stored in the credential store and never returned.">
          <TextInput value={value} onChange={setValue} ariaLabel="Secret value" type="password" size="sm" />
        </Row>
        <Row label="Project" hint="Leave empty to make it available to every project.">
          <TextInput value={projectId} onChange={setProjectId} ariaLabel="Project id (optional)" placeholder="(global)" mono size="sm" />
        </Row>
        <Row label="">
          <div className="flex items-center gap-l">
            {note && <span role="status" className="text-success text-[0.75rem]">{note}</span>}
            <Button
              size="sm"
              onClick={save}
              loading={busy}
              disabled={missing}
              disabledReason={missing ? 'Enter a name and a value first.' : undefined}
            >
              Store secret
            </Button>
          </div>
        </Row>
      </RowGroup>
      {err && <div role="alert" className="mt-2 text-danger text-[0.8125rem]">{err}</div>}
    </Section>
  )
}
