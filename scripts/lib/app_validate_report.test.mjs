// Unit tests for the validation harness's report shaping. Run: node --test scripts/lib/
//
// These cover the properties that decide whether the harness can be trusted:
// a leg that never ran must not read as green, a SKIPPED leg must always carry a
// reason, and the verdict/exit code must follow from the legs rather than from
// whatever the driver happened to reach.

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  LEGS,
  STATUS,
  NOT_REACHED_REASON,
  newLegs,
  passLeg,
  failLeg,
  skipLeg,
  noteLeg,
  finalizeLegs,
  bundleVerdict,
  shapeBundleReport,
  countStatuses,
  exitCodeFor,
  shapeReport,
  formatReport,
  REPORT_SCHEMA,
} from './app_validate_report.mjs'

const ids = LEGS.map((l) => l.id)

test('newLegs starts every standard leg PENDING with no reason', () => {
  const legs = newLegs()
  assert.deepEqual(legs.map((l) => l.id), ids)
  assert.ok(legs.every((l) => l.status === STATUS.PENDING && l.reason === ''))
})

test('a leg the driver never reached becomes SKIPPED with the not-reached reason', () => {
  const legs = newLegs()
  passLeg(legs, 'store-source')
  finalizeLegs(legs)
  assert.equal(legs[0].status, STATUS.PASS)
  for (const leg of legs.slice(1)) {
    assert.equal(leg.status, STATUS.SKIPPED)
    assert.equal(leg.reason, NOT_REACHED_REASON)
  }
})

test('finalizeLegs never carries a PENDING leg into a report', () => {
  const report = shapeBundleReport({ bundle: 'x', path: '/x', legs: newLegs() })
  assert.ok(report.legs.every((l) => l.status !== STATUS.PENDING))
})

test('finalizeLegs takes a caller-supplied cause and falls back when blank', () => {
  const legs = newLegs()
  finalizeLegs(legs, 'the gateway died mid-run')
  assert.equal(legs[0].reason, 'the gateway died mid-run')
  const other = newLegs()
  finalizeLegs(other, '   ')
  assert.equal(other[0].reason, NOT_REACHED_REASON)
})

test('finalizeLegs leaves an already-settled leg alone', () => {
  const legs = newLegs()
  skipLeg(legs, 'tool-invoke', 'gh auth is not configured on this machine')
  finalizeLegs(legs, 'aborted')
  const leg = legs.find((l) => l.id === 'tool-invoke')
  assert.equal(leg.status, STATUS.SKIPPED)
  assert.equal(leg.reason, 'gh auth is not configured on this machine')
})

test('SKIPPED without a reason is a programming error, not a blank skip', () => {
  const legs = newLegs()
  assert.throws(() => skipLeg(legs, 'tool-invoke', ''), /a reason is required/)
  assert.throws(() => skipLeg(legs, 'tool-invoke', '   '), /a reason is required/)
  assert.equal(legs.find((l) => l.id === 'tool-invoke').status, STATUS.PENDING)
})

test('FAIL without a reason is refused too', () => {
  const legs = newLegs()
  assert.throws(() => failLeg(legs, 'ui-install', ''), /a reason is required/)
})

test('an unknown leg id throws rather than silently vanishing', () => {
  const legs = newLegs()
  assert.throws(() => passLeg(legs, 'no-such-leg'), /unknown leg id/)
})

test('passing a leg clears any reason left from an earlier attempt', () => {
  const legs = newLegs()
  skipLeg(legs, 'reactivate', 'first attempt found no toggle')
  passLeg(legs, 'reactivate')
  const leg = legs.find((l) => l.id === 'reactivate')
  assert.equal(leg.status, STATUS.PASS)
  assert.equal(leg.reason, '')
})

test('evidence accumulates across mid-leg notes and the settling call', () => {
  const legs = newLegs()
  noteLeg(legs, 'ui-install', { screenshots: ['/a.png'], notes: ['consent modal shown'] })
  passLeg(legs, 'ui-install', { screenshots: ['/b.png'], details: { consent: true } })
  const leg = legs.find((l) => l.id === 'ui-install')
  assert.deepEqual(leg.screenshots, ['/a.png', '/b.png'])
  assert.deepEqual(leg.notes, ['consent modal shown'])
  assert.deepEqual(leg.details, { consent: true })
})

test('falsy screenshot paths are dropped rather than reported as evidence', () => {
  const legs = newLegs()
  passLeg(legs, 'store-card', { screenshots: ['', null, undefined, '/real.png'] })
  assert.deepEqual(legs.find((l) => l.id === 'store-card').screenshots, ['/real.png'])
})

test('bundleVerdict: all PASS is PASS, any FAIL is FAIL, a skip is PARTIAL', () => {
  const allPass = newLegs()
  for (const id of ids) passLeg(allPass, id)
  assert.equal(bundleVerdict(allPass), STATUS.PASS)

  const withSkip = newLegs()
  for (const id of ids) passLeg(withSkip, id)
  skipLeg(withSkip, 'tool-invoke', 'no credential')
  assert.equal(bundleVerdict(withSkip), 'PARTIAL')

  const withFail = newLegs()
  for (const id of ids) passLeg(withFail, id)
  skipLeg(withFail, 'tool-invoke', 'no credential')
  failLeg(withFail, 'reactivate', 'the toggle never came back')
  assert.equal(bundleVerdict(withFail), STATUS.FAIL)
})

test('bundleVerdict: a FAIL outranks every other status', () => {
  const legs = newLegs()
  failLeg(legs, 'store-source', 'the source was never accepted')
  finalizeLegs(legs)
  assert.equal(bundleVerdict(legs), STATUS.FAIL)
})

test('bundleVerdict: no legs at all is FAIL, never PASS', () => {
  assert.equal(bundleVerdict([]), STATUS.FAIL)
})

test('a bundle whose legs are all unreached is PARTIAL, not PASS', () => {
  const report = shapeBundleReport({ bundle: 'x', path: '/x', legs: newLegs() })
  assert.equal(report.verdict, 'PARTIAL')
  assert.equal(countStatuses([report]).PASS, 0)
})

test('shapeBundleReport copies legs so later driver mutation cannot rewrite history', () => {
  const legs = newLegs()
  passLeg(legs, 'store-source')
  const report = shapeBundleReport({ bundle: 'x', path: '/x', legs })
  failLeg(legs, 'store-source', 'changed my mind')
  assert.equal(report.legs[0].status, STATUS.PASS)
})

test('countStatuses sums legs across bundles', () => {
  const a = newLegs()
  passLeg(a, 'store-source')
  const b = newLegs()
  failLeg(b, 'store-source', 'nope')
  const bundles = [
    shapeBundleReport({ bundle: 'a', path: '/a', legs: a }),
    shapeBundleReport({ bundle: 'b', path: '/b', legs: b }),
  ]
  const totals = countStatuses(bundles)
  assert.equal(totals.PASS, 1)
  assert.equal(totals.FAIL, 1)
  assert.equal(totals.SKIPPED, (LEGS.length - 1) * 2)
})

test('exit code is non-zero only when a bundle failed; skips alone stay zero', () => {
  const skipped = newLegs()
  for (const id of ids) passLeg(skipped, id)
  skipLeg(skipped, 'tool-invoke', 'no model configured')
  const ok = [shapeBundleReport({ bundle: 'a', path: '/a', legs: skipped })]
  assert.equal(exitCodeFor(ok), 0)

  const broken = newLegs()
  failLeg(broken, 'ui-install', 'the consent dialog never opened')
  const bad = [...ok, shapeBundleReport({ bundle: 'b', path: '/b', legs: broken })]
  assert.equal(exitCodeFor(bad), 1)
})

test('shapeReport carries the schema id, the leg catalogue, totals and an exit code', () => {
  const legs = newLegs()
  passLeg(legs, 'store-source', { screenshots: ['/s.png'] })
  const bundles = [shapeBundleReport({ bundle: 'a', path: '/a', legs })]
  const report = shapeReport({ bundles, harness: { port: 10123 }, model: { configured: false, reason: 'no endpoint' }, generatedAt: '2026-01-01T00:00:00.000Z' })
  assert.equal(report.schema, REPORT_SCHEMA)
  assert.equal(report.generatedAt, '2026-01-01T00:00:00.000Z')
  assert.deepEqual(report.legs.map((l) => l.id), ids)
  assert.equal(report.totals.PASS, 1)
  assert.equal(report.exitCode, 0)
  assert.equal(report.model.reason, 'no endpoint')
  assert.equal(report.harness.port, 10123)
})

test('formatReport prints every leg status, its reason and its screenshots', () => {
  const legs = newLegs()
  passLeg(legs, 'store-source', { screenshots: ['/shots/store-source.png'] })
  skipLeg(legs, 'tool-invoke', 'gh auth missing')
  const report = shapeReport({ bundles: [shapeBundleReport({ bundle: 'notes', path: '/notes', legs, app: { version: '0.1.0' } })] })
  const text = formatReport(report)
  assert.match(text, /notes: PARTIAL \(v0\.1\.0\)/)
  assert.match(text, /PASS\s+store-source/)
  assert.match(text, /SKIPPED\s+tool-invoke — gh auth missing/)
  assert.match(text, /shot: \/shots\/store-source\.png/)
  assert.match(text, /totals: 1 PASS · 0 FAIL · 5 SKIPPED/)
})
