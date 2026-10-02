import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { PermissionList, permissionRows } from './installConsent'

// Two enforced grants whose bullets understated what they give an app.
//
// `agent` read "Run background agents", which said nothing about what they may use. It names a tier
// now, and its sentence says it (`agentTierConsent.test.tsx` holds every tier); the widest still
// says each call that needs approval asks you, because no tier lets an app approve one.
//
// `config` is new. It names the exact settings `/api/config` reaches for the app
// (`permissions.can_use_config_field`), and install consent is where the owner sees them.

const text = (el: HTMLElement) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ')

describe('the permission bullets say what an app agent and a settings grant really do', () => {
  it('an `agent` grant at the widest tier says its agents use your tools and ask you', () => {
    render(<PermissionList perms={{ agent: 'tools' }} />)
    expect(text(document.body)).toContain(
      'Run background agents that use your tools — they can change files, run commands and send messages, and the app can’t approve their calls, so each one that needs approval asks you',
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
