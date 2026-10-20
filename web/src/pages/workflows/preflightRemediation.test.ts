import { describe, expect, it } from 'vitest'
import { preflightRemediations } from './preflightRemediation'
import { errEnvelope } from '../../lib/errText'

/** The exact body `POST /api/workflows/runs` returned when driven in a browser with no model
 *  bound — copied from the response, not composed, so the shape under test is the wire shape. */
const REAL_BODY = {
  error: {
    code: 'preflight_failed',
    message:
      "the run cannot start: no model resolves for the 'background' use case; no model resolves " +
      "for the 'orchestration' use case; no model resolves for the 'reasoning' use case",
    detail: {
      preflight: {
        ok: false,
        findings: [
          {
            code: 'WF_PRE_MODEL_UNRESOLVED',
            message: "no model resolves for the 'background' use case",
            remediation: "select a model for background in Settings → Models, or change the node's model_tier",
            severity: 'error',
            kind: 'models',
          },
          {
            code: 'WF_PRE_MODEL_UNRESOLVED',
            message: "no model resolves for the 'orchestration' use case",
            remediation: "select a model for orchestration in Settings → Models, or change the node's model_tier",
            severity: 'error',
            kind: 'models',
          },
        ],
        checked: { credentials: [], binaries: [], models: ['background', 'orchestration'], action_providers: [] },
      },
    },
    service_code: 'WF_RUN_PREFLIGHT_FAILED',
  },
}

function res(body: unknown, status = 422): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('errEnvelope carries the detail', () => {
  it('lifts error.detail alongside the message and code', async () => {
    const env = await errEnvelope(res(REAL_BODY))
    expect(env.code).toBe('preflight_failed')
    expect(env.message).toContain('the run cannot start')
    // The regression this closes: `detail` was dropped at the funnel, so no caller could reach it.
    expect(env.detail).toBeDefined()
  })

  it('leaves detail undefined when the envelope carries none', async () => {
    const env = await errEnvelope(res({ error: { code: 'nope', message: 'no' } }))
    expect(env.detail).toBeUndefined()
  })

  it('does not change the sentence a user reads', async () => {
    const env = await errEnvelope(res(REAL_BODY))
    expect(env.message).toBe(REAL_BODY.error.message)
  })
})

describe('preflightRemediations', () => {
  it('collapses the real refusal to one instruction — its two findings share kind: models', async () => {
    const { detail } = await errEnvelope(res(REAL_BODY))
    expect(preflightRemediations(detail)).toEqual([
      "select a model for background in Settings → Models, or change the node's model_tier",
    ])
  })

  it('keeps one remediation per kind, in first-seen order', () => {
    const fixes = preflightRemediations({
      preflight: {
        findings: [
          { kind: 'models', remediation: 'select a model for background in Settings → Models' },
          { kind: 'credentials', remediation: 'add OPENAI_API_KEY in Settings → Providers' },
          { kind: 'models', remediation: 'select a model for reasoning in Settings → Models' },
          { kind: 'binaries', remediation: 'install rg, or edit the workflow to not need it' },
        ],
      },
    })
    expect(fixes).toEqual([
      'select a model for background in Settings → Models',
      'add OPENAI_API_KEY in Settings → Providers',
      'install rg, or edit the workflow to not need it',
    ])
  })

  it('dedupes on the text when a finding carries no kind, and never merges unlike sentences', () => {
    const fixes = preflightRemediations({
      preflight: {
        findings: [
          { remediation: 'add OPENAI_API_KEY in Settings → Providers, then start the run again' },
          { remediation: 'add OPENAI_API_KEY in Settings → Providers, then start the run again' },
          { remediation: 'install rg, or edit the workflow to not need it' },
        ],
      },
    })
    expect(fixes).toEqual([
      'add OPENAI_API_KEY in Settings → Providers, then start the run again',
      'install rg, or edit the workflow to not need it',
    ])
  })

  it('keeps a warning-severity remediation — it is still the answer to "what do I do"', () => {
    const fixes = preflightRemediations({
      preflight: {
        findings: [{ severity: 'warning', remediation: 'check Settings → Providers if it fails' }],
      },
    })
    expect(fixes).toEqual(['check Settings → Providers if it fails'])
  })

  it('never throws on a shape it does not expect — the error path is the worst place to crash', () => {
    for (const bad of [undefined, null, 0, '', 'detail', [], {}, { preflight: 1 }, { preflight: { findings: 'no' } },
      { preflight: { findings: [null, 3, {}, { remediation: '' }, { remediation: 7 }] } }]) {
      expect(preflightRemediations(bad)).toEqual([])
    }
  })
})
