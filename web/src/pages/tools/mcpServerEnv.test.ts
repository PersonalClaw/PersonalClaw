import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { buildMcpEnv, parseEnvLines } from './mcpServerEnv'

// The Add-tool-server form's two environment fields. Every value goes to the credential store
// except the variables `plainEnv` names — so which list a variable lands in decides whether its
// value is written into mcp.json and carried by every export. These pin that split.

describe('buildMcpEnv', () => {
  it('sends every variable as env, and names only the plain ones in plainEnv', () => {
    expect(buildMcpEnv('GITHUB_TOKEN=ghp_x', 'LOG_LEVEL=info')).toEqual({
      env: { GITHUB_TOKEN: 'ghp_x', LOG_LEVEL: 'info' },
      plainEnv: ['LOG_LEVEL'],
    })
  })

  it('treats a name typed in both fields as a secret', () => {
    expect(buildMcpEnv('API_KEY=from-secret', 'API_KEY=from-plain')).toEqual({
      env: { API_KEY: 'from-secret' },
      plainEnv: undefined,
    })
  })

  it('sends neither field when both are empty', () => {
    expect(buildMcpEnv('', '\n')).toEqual({ env: undefined, plainEnv: undefined })
  })

  it('parses one KEY=value per line and ignores a line with no name', () => {
    expect(parseEnvLines('A=1\n=orphan\nnot a pair\n B = two words ')).toEqual({ A: '1', B: 'two words' })
  })
})

describe('the Add form sends both fields', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/tools/ToolsPage.tsx'), 'utf8')

  it('builds the request env through buildMcpEnv', () => {
    expect(src).toMatch(/\.\.\.buildMcpEnv\(env, plainEnv\)/)
  })

  it('says where each field goes', () => {
    expect(src).toContain('Each value is kept in your credential store, never in mcp.json or an export.')
    expect(src).toContain('<Field label="Plain values"')
  })
})
