import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CallerRow, HealthRow } from './GuardrailsPanel'
import type { CallerHealth, ProviderHealth } from '../../lib/api'

// ── Wire fields the backend computes and the UI dropped ────────────────────────
//
// The coherence scanner's `wire` lens flags interfaces whose fields no surface reads. Two hits
// here were real, and both are the "backend truth, frontend silence" shape — sweep the READERS of
// a wire field, not its writers:
//
//   ProviderHealth.failure_modes   the per-mode tally BEHIND the `failed` count already rendered.
//                                  Without it "12 failed" cannot distinguish a rate limit from a
//                                  bad key from a timeout — three different user actions.
//   ProviderHealth.p99_ms          `provider_health()` computes p50/p90/p99; the row rendered p90
//                                  alone, so a provider that is usually fast but occasionally
//                                  stalls looked identical to a uniformly fast one — the exact
//                                  case the circuit breaker exists for.
//   MemoryLink.from_kind           semantic (durable fact, name-keyed) vs episodic (event,
//                                  uuid-keyed). EntityBacklinks rendered the bare `from_ref`, so a
//                                  uuid and a fact key read as the same kind of thing.
//
// DELIBERATELY NOT "fixed" — verified distinctions, so a later pass does not re-raise them:
//  · MemoryLink.to_entity / to_ref — `backlinks()` queries `WHERE to_entity = ?`, so within that
//    view to_entity is a CONSTANT (the entity you clicked) and to_ref is always NULL. add_link
//    enforces "exactly one of to_entity / to_ref", so rendering either in a backlinks row is
//    either redundant or blank. Measured on the validation home: 692 links, 0 with to_entity set.
//  · MemoryGraphSummary.semantic_orphans / episodic_orphans / phantom_entities — a FALSE POSITIVE.
//    The counts already reach the UI: memory_lint.py turns each into a `graph_orphans` /
//    `phantom_entity` flag with the count in its detail text, and MemoryPanel renders lint.flags
//    generically. The FIELD is unread; the DATUM is surfaced. Checking the fact rather than the
//    field name is what separates these two — the ledger's standing warning about this lens.
//  · ProviderHealth.p50_ms — its only "reader" is its own type declaration. Left unrendered: p90
//    plus a conditional p99 already answers "is this provider slow, and does it have a tail";
//    a third percentile on one line is noise, not information. Not drift, a judgement.

const SETTINGS = join(process.cwd(), 'src/pages/settings')
const read = (f: string) => readFileSync(join(SETTINGS, f), 'utf8')

const base: ProviderHealth = {
  name: 'bedrock',
  breaker_state: 'closed',
  consecutive_failures: 0,
  calls: 20,
  passed: 8,
  failed: 12,
  pass_rate: 0.4,
  p50_ms: 100,
  p90_ms: 200,
  p99_ms: 900,
  failure_modes: { rate_limited: 7, provider_error: 4, timeout: 1 },
  degraded: false,
}

describe('ProviderHealth.failure_modes reaches the row', () => {
  it('renders every mode with its count', () => {
    const { container } = render(<HealthRow p={base} />)
    const text = container.textContent ?? ''
    expect(text).toContain('rate limited ×7')
    expect(text).toContain('provider error ×4')
    expect(text).toContain('timeout ×1')
  })

  it('orders modes by frequency so the dominant one reads first', () => {
    const { container } = render(<HealthRow p={base} />)
    const text = container.textContent ?? ''
    expect(text.indexOf('rate limited')).toBeLessThan(text.indexOf('provider error'))
    expect(text.indexOf('provider error')).toBeLessThan(text.indexOf('timeout'))
  })

  it('renders nothing extra for a provider with no failures', () => {
    // A healthy provider must not carry an empty chip row.
    const { container } = render(
      <HealthRow p={{ ...base, failed: 0, pass_rate: 1, failure_modes: {} }} />)
    expect(container.querySelectorAll('.rounded-pill').length).toBe(0)
  })

  it('survives a missing failure_modes without throwing', () => {
    // Older audit rows / a provider known only by breaker state can omit it.
    const { container } = render(
      <HealthRow p={{ ...base, failure_modes: undefined as unknown as Record<string, number> }} />)
    expect(container.textContent).toContain('bedrock')
  })
})

describe('ProviderHealth.p99_ms is shown when the tail diverges', () => {
  it('shows p99 when it is materially worse than p90', () => {
    // 900 vs 200 — a real stall tail the p90-only row hid.
    expect(render(<HealthRow p={base} />).container.textContent).toContain('p99 900ms')
  })

  it('omits p99 when it tracks p90', () => {
    // Two numbers that always agree are noise; a uniformly fast provider keeps one.
    const { container } = render(<HealthRow p={{ ...base, p90_ms: 200, p99_ms: 210 }} />)
    expect(container.textContent).toContain('p90 200ms')
    expect(container.textContent).not.toContain('p99')
  })
})

describe('MemoryLink.from_kind reaches the backlinks row', () => {
  it('EntityBacklinks renders from_kind beside from_ref', () => {
    const src = read('MemoryPanel.tsx')
    expect(src, 'the backlinks row should render l.from_kind').toMatch(/\{l\.from_kind\}/)
  })

  it('does NOT render to_entity or to_ref in a backlinks row', () => {
    // The counterpart assertion. backlinks() filters WHERE to_entity = ?, so to_entity is a
    // constant there and to_ref is always NULL — rendering either is redundant or blank. If this
    // fails, someone "finished the sweep" by surfacing fields that carry no information here.
    const src = read('MemoryPanel.tsx')
    expect(src).not.toMatch(/\{l\.to_entity\}/)
    expect(src).not.toMatch(/\{l\.to_ref\}/)
  })
})

// ── CallerHealth: the SUBSYSTEM axis of the same audit (ACP-AGENT-PARITY G47) ──────────
//
// A provider row averages every unattended caller together, so a learning pass that fails on
// every turn reads as a small dent in a healthy provider — measured on a skill-ladder pass dying
// as provider_error at 60,010 ms. These assert the caller axis actually reaches a rendered row:
// a wire field the backend computes and nothing shows is the defect this file exists to catch.
const deadCaller: CallerHealth = {
  name: 'skill_ladder',
  calls: 6,
  passed: 0,
  failed: 6,
  pass_rate: 0,
  p50_ms: 0,
  p90_ms: 0,
  p99_ms: 0,
  failure_modes: { provider_error: 6 },
  dollars_est: 0.12,
}

describe('CallerHealth reaches a rendered row', () => {
  it('names the dead subsystem, its rate, and why', () => {
    const { container } = render(<CallerRow c={deadCaller} />)
    const text = container.textContent ?? ''
    expect(text).toContain('skill ladder')
    expect(text).toContain('6 calls')
    expect(text).toContain('0% ok')
    // The mode is what makes it actionable — "dead" and "dead because the provider errors" are
    // different next steps for the operator.
    expect(text).toContain('provider error')
  })

  it('a healthy caller stays a one-line summary, no failure mode', () => {
    // The counterpart. Every caller printing its dominant mode would turn a summary block into a
    // second failure table; the mode appears only when the pass is producing nothing at all.
    const healthy: CallerHealth = { ...deadCaller, passed: 6, failed: 0, pass_rate: 1, p90_ms: 800 }
    const text = render(<CallerRow c={healthy} />).container.textContent ?? ''
    expect(text).toContain('100% ok')
    expect(text).toContain('p90 800ms')
    expect(text).not.toContain('provider error')
  })
})
