/**
 * A multi-instance provider's stored secret is not offered back for editing.
 *
 * The backend now masks `x-meta.sensitive` fields on all four instance routes, which creates
 * the hazard the single-config form already had to solve: `InstanceRow` seeds its editor from
 * the list route's config and PUTs the WHOLE dict back, so a masked `api_key` left in place
 * would be written over a working credential the first time someone changed a model id. The
 * PUT's `preserve_unchanged_secrets` is one half of the guard; blanking the input is the other,
 * and it is the half the operator can see — a row of bullets is indistinguishable from a real
 * value, so the field says "saved — leave blank to keep" instead.
 */
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MultiInstanceCard, editableConfig } from './MultiInstanceCard'
import { SchemaField } from './ProviderConfigForm'
import type { ProviderInstance, ProviderSchemaProp, SettingsProvider } from '../../lib/api'

// 🪤 `editableConfig` and `SchemaField` are each unit-tested below, and that pair does NOT
// cover the WIRING between them. Measured: a mutant deleting `secretAlreadySet={…}` from
// `InstanceRow`'s SchemaField call left every unit test green while the rendered field fell
// back to a `••••••••` PLACEHOLDER — the operator sees bullets in an empty box and cannot
// tell a stored credential from an unset one. The card-level test at the bottom is what
// kills that mutant.
vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      providerSchema: () => Promise.resolve(CARD_SCHEMA),
      providerInstances: () => Promise.resolve([instance({ api_key: MASK, default_model: 'gpt-4o' }, ['api_key'])]),
      testProviderInstance: vi.fn(),
      updateProviderInstance: vi.fn(),
      deleteProviderInstance: vi.fn(),
      enableProvider: vi.fn(),
      disableProvider: vi.fn(),
    },
  }
})

afterEach(() => cleanup())

const MASK = '••••••••'

function instance(config: Record<string, unknown>, secretSet?: string[]): ProviderInstance {
  return {
    id: 'abc123',
    extension_name: 'openai-models',
    display_name: 'Primary',
    config,
    enabled: true,
    ...(secretSet ? { _secret_set: secretSet } : {}),
  }
}

describe('an instance editor does not offer a stored secret back', () => {
  it('blanks a masked sensitive field so a save cannot write the mask back', () => {
    const values = editableConfig(instance({ api_key: MASK, default_model: 'gpt-4o' }, ['api_key']))
    expect(values.api_key).toBe('')
  })

  it('leaves every non-sensitive field exactly as delivered', () => {
    const values = editableConfig(instance({ api_key: MASK, default_model: 'gpt-4o' }, ['api_key']))
    expect(values.default_model).toBe('gpt-4o')
  })

  it('does not mutate the instance the list route handed it', () => {
    const inst = instance({ api_key: MASK }, ['api_key'])
    editableConfig(inst)
    expect(inst.config.api_key).toBe(MASK)
  })

  it('changes nothing when no secret is stored, so a fresh field stays fillable', () => {
    const values = editableConfig(instance({ api_key: '', default_model: 'gpt-4o' }, []))
    expect(values).toEqual({ api_key: '', default_model: 'gpt-4o' })
  })

  it('tolerates an older response with no _secret_set at all', () => {
    const values = editableConfig(instance({ default_model: 'gpt-4o' }))
    expect(values).toEqual({ default_model: 'gpt-4o' })
  })
})

const SECRET_PROP: ProviderSchemaProp = {
  type: 'string',
  'x-meta': { label: 'OpenAI API Key', sensitive: true },
}

describe('a blanked secret field says it is saved rather than looking unset', () => {
  it('offers the keep-it placeholder when the instance already holds a secret', () => {
    const { container } = render(
      <SchemaField fieldKey="api_key" prop={SECRET_PROP} value="" onChange={vi.fn()} secretAlreadySet />,
    )
    const input = container.querySelector('input') as HTMLInputElement
    expect(input.placeholder).toBe('saved — leave blank to keep')
    expect(input.value).toBe('')
  })

  it('does not claim a secret is saved when none is', () => {
    const { container } = render(
      <SchemaField fieldKey="api_key" prop={SECRET_PROP} value="" onChange={vi.fn()} />,
    )
    const input = container.querySelector('input') as HTMLInputElement
    expect(input.placeholder).not.toBe('saved — leave blank to keep')
  })
})

const CARD_SCHEMA = {
  type: 'object',
  properties: {
    api_key: SECRET_PROP,
    default_model: { type: 'string', 'x-meta': { label: 'Default Model' } } as ProviderSchemaProp,
  },
}

const EXT: SettingsProvider = {
  name: 'openai-models',
  displayName: 'OpenAI',
  description: 'OpenAI chat + embedding models',
  enabled: true,
  multiInstance: true,
} as SettingsProvider

describe('the instance card wires the two halves together', () => {
  it('opens its editor with the key box EMPTY and labelled saved, not full of bullets', async () => {
    render(<MultiInstanceCard ext={EXT} onChanged={vi.fn()} />)
    const edit = await screen.findByRole('button', { name: 'Edit' })
    await userEvent.click(edit)

    const key = await waitFor(() => {
      const el = screen.getByLabelText('OpenAI API Key') as HTMLInputElement
      expect(el).toBeTruthy()
      return el
    })
    // The mask must not survive into the form as a value OR as a placeholder: both read as
    // "there is something here" and one of them gets PUT back over a working credential.
    expect(key.value).toBe('')
    expect(key.placeholder).toBe('saved — leave blank to keep')
    expect(key.placeholder).not.toBe(MASK)

    // …and the non-sensitive field arrives filled in, so the editor is still usable.
    const model = screen.getByLabelText('Default Model') as HTMLInputElement
    expect(model.value).toBe('gpt-4o')
  })

  // 🪤 Cancel re-seeds the form, and it must re-seed from the BLANKED config rather than from
  // the masked one the list route delivered. Measured: a mutant swapping Cancel's
  // `setConfig(editableConfig(inst))` for `setConfig(inst.config)` left every other test in
  // this file green while re-opening the editor showed `••••••••` sitting in the key box as a
  // real VALUE — which is both unreadable as "already saved" and the exact string the save path
  // would then PUT back. The backend's `preserve_unchanged_secrets` catches the write, so this
  // is a legibility defect rather than a data-loss one; it is still the defect the whole
  // blanking treatment exists to prevent, so it gets its own rail.
  it('re-seeds from the blanked config after Cancel, not from the masked one', async () => {
    render(<MultiInstanceCard ext={EXT} onChanged={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }))

    const typed = await waitFor(() => screen.getByLabelText('OpenAI API Key') as HTMLInputElement)
    await userEvent.type(typed, 'sk-typed-then-abandoned')
    expect(typed.value).toBe('sk-typed-then-abandoned')

    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Edit' }))

    const reopened = await waitFor(() => screen.getByLabelText('OpenAI API Key') as HTMLInputElement)
    expect(reopened.value).toBe('')
    expect(reopened.value).not.toBe(MASK)
    // The abandoned edit is gone AND the mask did not take its place.
    expect(reopened.placeholder).toBe('saved — leave blank to keep')
  })
})
