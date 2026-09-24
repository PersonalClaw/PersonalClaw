/** Imperative dialog system — the promise-based API behind confirm / prompt /
 *  alert, plus a tiny subscribable store the global <DialogHost> renders from.
 *
 *  WHY imperative: the polished <ConfirmDialog>/<PromptDialog> visuals already
 *  existed but were controlled components — wiring open-state + JSX into every
 *  delete handler is far more friction than a one-line native `confirm()`, so
 *  adoption stayed near zero and native browser dialogs lingered everywhere.
 *  This layer makes the swap one line:
 *
 *      if (!confirm('Delete?')) return            // native, ugly, blocking
 *      if (!(await confirm({ title: 'Delete?' }))) return   // ours, styled, async
 *
 *  It mirrors the existing imperative `notify()` toast pattern (appSdk): a
 *  module-level store + a single host mounted once in the app shell. Callable
 *  from anywhere — event handlers, catch blocks, non-React code — no context or
 *  provider plumbing at the call site. */

export type DialogKind = 'confirm' | 'prompt' | 'alert'
export type DialogTone = 'default' | 'danger'

/** A field on a prompt dialog. A prompt with one field behaves like the old
 *  window.prompt(); multiple fields make it a small form (extensible). */
export interface DialogField {
  name: string
  label?: string
  placeholder?: string
  initial?: string
  /** 'text' (default) | 'textarea' | 'password' */
  type?: 'text' | 'textarea' | 'password'
  /** Field is required — submit stays disabled until it has a non-empty value. */
  required?: boolean
  /** Optional client-side validator. Return an error string to block submit. */
  validate?: (value: string) => string | null
}

export interface DialogRequest {
  kind: DialogKind
  title: string
  /** Body copy — string or rich React content. */
  body?: React.ReactNode
  tone?: DialogTone
  confirmLabel?: string
  cancelLabel?: string
  /** prompt-only: the input fields (single-field = classic prompt). */
  fields?: DialogField[]
  /** Optional lucide icon component for the header. */
  icon?: React.ComponentType<{ size?: number | string }>
}

/** What a dialog resolves to. confirm/alert → boolean; prompt → the field-value
 *  map (or null when cancelled). A single-field prompt also exposes `.value`. */
export type DialogResult = boolean | (Record<string, string> & { value?: string }) | null

interface ActiveDialog extends DialogRequest {
  id: number
  resolve: (result: DialogResult) => void
}

type Listener = (dialogs: ActiveDialog[]) => void

let _seq = 0
let _dialogs: ActiveDialog[] = []
const _listeners = new Set<Listener>()

function _emit() {
  for (const l of _listeners) l(_dialogs)
}

/** Subscribe the host to the active-dialog stack. Returns an unsubscribe fn. */
export function subscribeDialogs(listener: Listener): () => void {
  _listeners.add(listener)
  listener(_dialogs)
  return () => _listeners.delete(listener)
}

/** Open a dialog and resolve when the user acts. Stacks (a confirm inside a
 *  prompt's validate, etc.) — newest renders on top. */
export function openDialog(req: DialogRequest): Promise<DialogResult> {
  return new Promise<DialogResult>((resolve) => {
    const dialog: ActiveDialog = { ...req, id: ++_seq, resolve }
    _dialogs = [..._dialogs, dialog]
    _emit()
  })
}

/** Host calls this when a dialog finishes — removes it and resolves its promise. */
export function closeDialog(id: number, result: DialogResult): void {
  const dialog = _dialogs.find((d) => d.id === id)
  if (!dialog) return
  _dialogs = _dialogs.filter((d) => d.id !== id)
  _emit()
  dialog.resolve(result)
}

// ── Public imperative API ────────────────────────────────────────────────────

export interface ConfirmOptions {
  title: string
  body?: React.ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** Tint the confirm button + show a warning icon for destructive actions. */
  danger?: boolean
  icon?: React.ComponentType<{ size?: number | string }>
}

/** Styled async replacement for window.confirm(). Resolves true on confirm,
 *  false on cancel/dismiss. */
export async function confirm(opts: ConfirmOptions | string): Promise<boolean> {
  const o = typeof opts === 'string' ? { title: opts } : opts
  const res = await openDialog({
    kind: 'confirm',
    title: o.title,
    body: o.body,
    tone: o.danger ? 'danger' : 'default',
    confirmLabel: o.confirmLabel,
    cancelLabel: o.cancelLabel,
    icon: o.icon,
  })
  return res === true
}

export interface PromptOptions {
  title: string
  body?: React.ReactNode
  label?: string
  placeholder?: string
  initial?: string
  confirmLabel?: string
  cancelLabel?: string
  type?: DialogField['type']
  required?: boolean
  validate?: (value: string) => string | null
}

/** Styled async replacement for window.prompt(). Resolves the entered string,
 *  or null when cancelled. (Named promptInput to avoid shadowing the global.) */
export async function promptInput(opts: PromptOptions | string): Promise<string | null> {
  const o = typeof opts === 'string' ? { title: opts } : opts
  const res = await openDialog({
    kind: 'prompt',
    title: o.title,
    body: o.body,
    confirmLabel: o.confirmLabel,
    cancelLabel: o.cancelLabel,
    fields: [{
      name: 'value',
      label: o.label,
      placeholder: o.placeholder,
      initial: o.initial,
      type: o.type,
      required: o.required ?? true,
      validate: o.validate,
    }],
  })
  if (res && typeof res === 'object') return res.value ?? null
  return null
}

export interface FormOptions {
  title: string
  body?: React.ReactNode
  fields: DialogField[]
  confirmLabel?: string
  cancelLabel?: string
}

/** Multi-field form dialog — the extensible form of promptInput. Resolves the
 *  field-value map, or null when cancelled. */
export async function promptForm(opts: FormOptions): Promise<Record<string, string> | null> {
  const res = await openDialog({
    kind: 'prompt',
    title: opts.title,
    body: opts.body,
    fields: opts.fields,
    confirmLabel: opts.confirmLabel,
    cancelLabel: opts.cancelLabel,
  })
  if (res && typeof res === 'object') {
    const { value, ...rest } = res as Record<string, string> & { value?: string }
    void value
    return rest
  }
  return null
}

export interface AlertOptions {
  title: string
  body?: React.ReactNode
  confirmLabel?: string
  tone?: DialogTone
  icon?: React.ComponentType<{ size?: number | string }>
}

/** Styled async replacement for window.alert(). Resolves when acknowledged. */
export async function alertDialog(opts: AlertOptions | string): Promise<void> {
  const o = typeof opts === 'string' ? { title: opts } : opts
  await openDialog({
    kind: 'alert',
    title: o.title,
    body: o.body,
    confirmLabel: o.confirmLabel,
    tone: o.tone,
    icon: o.icon,
  })
}
