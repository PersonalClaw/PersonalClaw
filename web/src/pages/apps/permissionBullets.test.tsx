import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { PermissionList, permissionRows } from './installConsent'

// Two enforced grants whose bullets understated what they give an app.
//
// `agent` read "Run background agents", which sounds like agents that will ask you. They do not:
// `handlers/apps.api_app_agent_run` spawns with `approval_mode="auto"` and the write grant.
//
// `config` is new. It names the exact settings `/api/config` reaches for the app
// (`permissions.can_use_config_field`), and install consent is where the owner sees them.

const text = (el: HTMLElement) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ')

describe('the permission bullets say what an app agent and a settings grant really do', () => {
  it('an `agent` grant says its agents act without asking', () => {
    render(<PermissionList perms={{ agent: true }} />)
    expect(text(document.body)).toContain(
      'Run background agents that use any tool without asking you — they can change files and run commands',
    )
  })

  it('a `config` grant names each setting it reaches', () => {
    render(<PermissionList perms={{ config: ['voice.echo_filter_enabled', 'voice.tts_voice'] }} />)
    expect(text(document.body)).toContain(
      'Read and change your settings: voice.echo_filter_enabled, voice.tts_voice',
    )
  })

  it('an app that declares no settings gets no settings bullet', () => {
    expect(permissionRows({ config: [] }).some((r) => r.startsWith('Read and change your settings'))).toBe(false)
    expect(permissionRows({}).some((r) => r.startsWith('Read and change your settings'))).toBe(false)
  })
})
