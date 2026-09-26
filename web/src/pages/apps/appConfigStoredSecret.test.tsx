import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { AppConfigFields, type SchemaProp } from './appConfigForm'

// The config GET masks EVERY field whose stored value is a credential-store reference — not only
// the ones the schema declares `sensitive` — and names each in `_secret_set`. A credential-named
// field an app forgot to declare is kept in the store all the same, so the form must present it as
// what it is: a saved secret whose blank input means "keep it", not an empty setting to fill in.

afterEach(() => cleanup())

const PROPS: Record<string, SchemaProp> = {
  api_token: { type: 'string', 'x-meta': { label: 'API token' } },
  endpoint: { type: 'string', 'x-meta': { label: 'Endpoint' } },
}

describe('a field the backend reports as a stored secret', () => {
  it('reads as saved even when the schema does not call it sensitive', () => {
    render(<AppConfigFields appName="t" props={PROPS} cur={{ api_token: '', endpoint: 'https://x' }}
      set={() => {}} secretSet={['api_token']} />)
    const token = document.getElementById('app-cfg-t-api_token') as HTMLInputElement
    expect(token.type).toBe('password')
    expect(token.placeholder).toBe('saved — leave blank to keep')
  })

  it('leaves an ordinary field an ordinary text input', () => {
    render(<AppConfigFields appName="t" props={PROPS} cur={{ api_token: '', endpoint: 'https://x' }}
      set={() => {}} secretSet={['api_token']} />)
    const endpoint = document.getElementById('app-cfg-t-endpoint') as HTMLInputElement
    expect(endpoint.type).toBe('text')
    expect(endpoint.placeholder).toBe('')
  })
})
