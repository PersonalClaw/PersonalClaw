import { useState } from 'react'
import { KeyRound, Server, FolderLock, Trash2, Plus, Workflow, Zap, Globe } from 'lucide-react'
import { api } from '../../lib/api'
import type { ProjectItem, SecretPresenceWire, SecretStoreWire, SecretsVaultState } from '../../lib/api'
import { useQuery } from '../../lib/data/useQuery'
import { Button } from '../../ui/Button'
import { TextInput } from '../../ui/forms'
import { CardGridSkeleton, EmptyState, InlineLoadError, ListRow, LoadError } from '../../ui/ListScaffold'
import { ProjectPicker } from '../../ui/ProjectPicker'
import { confirm } from '../../ui/dialog'
import { StatusPill } from './bento'
import { PanelHeader, Row, RowGroup, Section } from './settingsUI'

/** The name field's stable DOM id. `TextInput` publishes `id={name || autoId}`, so passing this as
 *  `name` gives the empty state's on-ramp something to focus — the create surface for this
 *  collection is the form already on the page, not a separate route, so the on-ramp is "put the
 *  cursor in it" rather than a navigation. */
const NAME_FIELD_ID = 'secrets-vault-name'

/** A project as this page names it: its name, or — while the list is unknown — its id. An id the
 *  loaded list does not hold is said to be no current project, never given a name it does not
 *  have. */
type ProjectLabel = (projectId: string) => string

/** Settings › Secrets — the vault.
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
 *
 *  **A project's secret is read by that project's work alone** — its workflow runs, its loops, the
 *  commands its chats run — ahead of a global secret of the same name, and by nothing outside it
 *  (the backend's one resolver, `llm.credentials.resolve_secret`). The page says so where a project
 *  secret is stored, listed and removed, and names each project by its name, chosen with the same
 *  picker every other project choice uses: an id typed by hand was a secret nothing would read.
 */
export function SecretsPanel() {
  const { data: v, error, refresh } = useQuery<SecretsVaultState>(
    'settings:secrets', () => api.secrets(),
  )
  // The same read (and cache) the Projects page names projects from. Pending or failed, a group is
  // named by its id, which is still true — and a failed read says so above the groups, or the ids
  // would read as projects that have no name.
  const { data: projects, error: projectsError, refresh: refreshProjects } = useQuery<ProjectItem[]>(
    'projects:list', () => api.projects(), { persist: true },
  )
  const projectLabel: ProjectLabel = (pid) => {
    const found = projects?.find((p) => p.id === pid)
    if (found) return found.name
    return projects ? `${pid} (not a current project)` : pid
  }

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
  const globalNames = new Set(globals.map((s) => s.name))
  const projectsHolding = (name: string) =>
    [...byProject.entries()].filter(([, rows]) => rows.some((r) => r.name === name)).map(([pid]) => projectLabel(pid))

  return (
    <>
      <PanelHeader
        title="Secrets"
        hint={'Values are write-only: once stored, a secret can be replaced or removed, but never '
          + 'read back — not by this page and not by any API.'}
      />

      <AddSecret onSaved={refresh} projectLabel={projectLabel} store={v.store} />

      {v.secrets.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="No secrets yet"
          // The server composes this sentence so the CLI and the dashboard say the same thing
          // about the same state. It names the NEXT ACTION — "no secrets yet" alone reads as
          // "secrets are broken" on a page whose whole subject is credentials.
          hint={v.empty_hint}
          // A real on-ramp, reaching the same create surface a non-empty vault uses: the
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
            hint={`${v.counts.global} available everywhere on this instance. A project's own secret `
              + 'of the same name comes first for that project\'s work.'}
          >
            {globals.length === 0
              ? <RowGroup><Row label="None stored" hint="Add one above to make it available everywhere." ><span /></Row></RowGroup>
              : (
                <RowGroup>
                  {globals.map((s, i) => (
                    <SecretRow key={s.name} s={s} index={i} onChanged={refresh}
                      removeBody={globalRemoveBody(s.name, projectsHolding(s.name))} />
                  ))}
                </RowGroup>
              )}
          </Section>

          {byProject.size > 0 && (
            <Section
              title="Per-project"
              icon={FolderLock}
              iconTone="muted"
              hint={`${v.counts.project} scoped to one project. Only that project's work — its `
                + 'workflow runs, loops and chats — reads one, ahead of a global secret of the same '
                + 'name. Nothing outside the project reads it.'}
            >
              {projectsError ? (
                <InlineLoadError what="your projects" error={projectsError} onRetry={refreshProjects} />
              ) : null}
              {[...byProject.entries()].map(([pid, rows]) => (
                <div key={pid} className="mb-l last:mb-0">
                  <div data-type="caption" className="mb-xs text-on-surface-low">{projectLabel(pid)}</div>
                  <RowGroup>
                    {rows.map((s, i) => (
                      <SecretRow key={s.name} s={s} index={i} onChanged={refresh}
                        removeTitle={`Remove ${s.name} from ${projectLabel(pid)}?`}
                        removeBody={projectRemoveBody(s.name, projectLabel(pid), globalNames.has(s.name))} />
                    ))}
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
                {host.map((s, i) => <SecretRow key={s.name} s={s} index={i} onChanged={refresh} removeBody="" />)}
              </RowGroup>
            </Section>
          )}
        </>
      )}
    </>
  )
}

/** What removing a GLOBAL secret does: everything that refers to it fails until it is replaced,
 *  except the work of a project that keeps its own secret of that name. */
function globalRemoveBody(name: string, shadowedIn: string[]): string {
  const except = shadowedIn.length
    ? `, except work in ${shadowedIn.join(', ')}, which reads its project's own ${name}`
    : ''
  return 'The stored value is deleted from the credential store and from this gateway\'s '
    + `environment. Anything that references {{secret:${name}}} fails until it is replaced${except}. `
    + 'This cannot be undone: the value cannot be read back out to save it.'
}

/** What removing a PROJECT's secret does: that project's work falls back to the global secret of
 *  the same name, or fails while there is none. */
function projectRemoveBody(name: string, project: string, hasGlobal: boolean): string {
  const then = hasGlobal
    ? `then reads the global ${name} instead`
    : `then fails until it is replaced, since no global ${name} is stored`
  return `The stored value is deleted from the credential store. Work in ${project} that references `
    + `{{secret:${name}}} ${then}. This cannot be undone: the value cannot be read back out to save it.`
}

/** One vault row: name, scope treatment, and what references it. */
function SecretRow({ s, index, onChanged, removeTitle, removeBody }: {
  s: SecretPresenceWire
  index: number
  onChanged: () => void
  /** The confirm's title; `Remove NAME?` unless the scope needs saying. */
  removeTitle?: string
  /** What removing this row does, in the confirm. Unused for a host row, which cannot be removed. */
  removeBody: string
}) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const hostRow = s.inherited_from_host
  const projectRow = s.scope === 'project'

  const remove = async () => {
    if (!(await confirm({
      title: removeTitle ?? `Remove ${s.name}?`,
      body: removeBody,
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
      <div className="flex items-start justify-between gap-l py-s">
        <div className="min-w-0">
          <div className="flex items-center gap-s">
            {hostRow ? <Globe size={14} className="text-on-surface-low" aria-hidden />
              : <KeyRound size={14} className="text-primary" aria-hidden />}
            <span data-type="body-s" className="truncate font-mono text-on-surface">{s.name}</span>
            {/* `present` is the whole payload of a vault row, so it is stated rather than
                implied by the row existing — the user is reading a presence list, not a
                value list whose values happen to be missing. */}
            {hostRow
              ? <StatusPill label="from host environment" tone="warn" />
              : <StatusPill label={s.present ? 'set' : 'not set'} tone="ok" />}
            {projectRow && <StatusPill label="project" tone="primary" />}
          </div>
          {s.consumers.length > 0 ? (
            <div data-type="caption" className="mt-xs flex flex-wrap items-center gap-x-s gap-y-xs text-on-surface-low">
              {/* A project row's consumers are the workflows that name it: they read it when they
                  run in this project, and the global one anywhere else. */}
              <span>{projectRow ? 'Read in this project by' : 'Used by'}</span>
              {s.consumers.map((c) => (
                <span key={`${c.kind}:${c.id}`} className="inline-flex items-center gap-xs">
                  {c.kind === 'workflow' ? <Workflow size={12} aria-hidden /> : <Zap size={12} aria-hidden />}
                  <span className="truncate">{c.label || c.id}</span>
                </span>
              ))}
            </div>
          ) : (
            // Said explicitly rather than left blank. A blank "used by" line is ambiguous
            // between "nothing references this" (safe to delete) and "we didn't check". An
            // automation runs in no project, so a project row only ever lists workflows.
            <div data-type="caption" className="mt-xs text-on-surface-low">
              {projectRow ? 'Not referenced by any workflow.' : 'Not referenced by any workflow or automation.'}
            </div>
          )}
          {err && <div role="alert" data-type="caption" className="mt-xs text-danger">{err}</div>}
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

/** Whose namespace in the OS keychain this home's secrets are filed under, keyed by the read's
 *  `keychain_scope`. Each home has its own: the default home keeps the name every home used before
 *  (`personalclaw`), any other home a name made from an id it keeps, so no home reads, changes or
 *  deletes another's. A scope of '' means no keychain answers here, and there is no namespace. */
const KEYCHAIN_SCOPE: Record<string, { pill: string; tone: 'ok' | 'warn' | 'muted'; hint: string }> = {
  default: { pill: 'default home', tone: 'muted', hint: "is the default home's namespace in the OS keychain." },
  own: {
    pill: "this home's own",
    tone: 'ok',
    hint: "is this home's own namespace in the OS keychain. Other homes keep their secrets under names of their own.",
  },
  unnamed: {
    pill: 'none yet',
    tone: 'muted',
    hint: 'This home gets a namespace of its own in the OS keychain when it first stores a secret there.',
  },
  unreadable: {
    pill: 'unreadable',
    tone: 'warn',
    hint: "This home's keychain_namespace file holds no namespace id, so the OS keychain is not used here. "
      + "Write the id back into it (this home's keychain items are filed under personalclaw-<id>), or delete "
      + 'it to start a new, empty namespace.',
  },
}

/** The keychain namespace this home's secrets are filed under, named where a secret is stored. */
function KeychainNamespaceRow({ store }: { store: SecretStoreWire }) {
  const scope = KEYCHAIN_SCOPE[store.keychain_scope]
  if (!scope) return null
  return (
    <Row
      label="Keychain namespace"
      hint={store.keychain_namespace
        ? <><span className="break-all font-mono">{store.keychain_namespace}</span> {scope.hint}</>
        : scope.hint}
    >
      <StatusPill label={scope.pill} tone={scope.tone} />
    </Row>
  )
}

/** The write-only add form. The value leaves in a POST body and is cleared on success. */
function AddSecret({ onSaved, projectLabel, store }: { onSaved: () => void; projectLabel: ProjectLabel; store: SecretStoreWire }) {
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [projectId, setProjectId] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const save = async () => {
    setBusy(true); setErr(''); setNote('')
    try {
      await api.putSecret(name.trim(), value, projectId)
      // Cleared immediately on success. The component holds the value only for as long as it
      // takes to send it; nothing renders it, and nothing re-reads it.
      setValue('')
      setNote(`${name.trim()} stored for ${projectId ? projectLabel(projectId) : 'every project'}.`)
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
        <KeychainNamespaceRow store={store} />
        <Row
          label="Project"
          hint={'Every project, or one: then only that project\'s work reads it — ahead of a global '
            + 'secret of the same name — and nothing outside the project does.'}
        >
          <ProjectPicker value={projectId} onChange={setProjectId} emptyLabel="Every project" emptyHint="" align="right" />
        </Row>
        <Row label="">
          <div className="flex items-center gap-l">
            {note && <span role="status" data-type="caption" className="text-success">{note}</span>}
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
      {err && <div role="alert" data-type="body-s" className="mt-s text-danger">{err}</div>}
    </Section>
  )
}
