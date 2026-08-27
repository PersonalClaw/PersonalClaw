import assert from 'node:assert/strict'
import test from 'node:test'

import {
  activeBaseUrl,
  activeEndpoint,
  EMPTY_REGISTRY,
  ENDPOINT_FIELDS,
  forgetActiveGateway,
  newEndpointId,
  readRegistry,
  REGISTRY_FIELDS,
  REGISTRY_STORAGE_KEY,
  rememberGateway,
  writeRegistry,
} from '../www/shell/registry.mjs'

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
  }
}

const put = (storage, registry) => storage.map.set(REGISTRY_STORAGE_KEY, JSON.stringify(registry))

test('the vocabulary is endpoints.ts`s vocabulary', () => {
  // `tests/test_mobile_shell.py` holds these against `web/src/lib/endpoints.ts` itself; this is
  // the same claim from the JS side, so a drift reds in whichever suite runs first.
  assert.equal(REGISTRY_STORAGE_KEY, 'companion:endpoints')
  assert.deepEqual([...REGISTRY_FIELDS], ['active', 'endpoints'])
  assert.deepEqual([...ENDPOINT_FIELDS], ['id', 'label', 'base_url', 'kind', 'device_session_ref'])
  assert.deepEqual(EMPTY_REGISTRY, { active: '', endpoints: [] })
})

test('an id is minted, never derived from the URL', () => {
  const a = newEndpointId()
  assert.match(a, /^ep_[a-z0-9]{12}$/)
  // Deterministic rand proves the alphabet mapping, not just the shape.
  assert.equal(newEndpointId(() => 0), 'ep_aaaaaaaaaaaa')

  const storage = fakeStorage()
  rememberGateway(storage, { baseUrl: 'http://10.0.0.4:10000', mintId: () => 'ep_fixedfixedid' })
  const [row] = readRegistry(storage).endpoints
  assert.ok(!row.id.includes('10.0.0.4'), 'the id must not encode the URL')
})

test('a parse is total — every corrupt input lands on a usable registry', () => {
  for (const raw of ['', 'not json', '[]', 'null', '3', '{"endpoints":"nope"}', '{}']) {
    const storage = fakeStorage({ [REGISTRY_STORAGE_KEY]: raw })
    assert.deepEqual(readRegistry(storage), { active: '', endpoints: [] }, `raw=${raw}`)
  }
  const hostile = {
    getItem() {
      throw new Error('denied')
    },
  }
  assert.deepEqual(readRegistry(hostile), { active: '', endpoints: [] })
})

test('an id-less row is dropped rather than assigned a fresh id', () => {
  const storage = fakeStorage()
  put(storage, { active: '', endpoints: [{ base_url: 'http://10.0.0.4:10000' }, { id: 'ep_b', base_url: 'x' }] })
  const registry = readRegistry(storage)
  assert.deepEqual(
    registry.endpoints.map((e) => e.id),
    ['ep_b'],
  )
  assert.equal(registry.active, 'ep_b', 'active must never dangle after a parse')
})

test('a duplicate id keeps the FIRST row — it owns the namespaced state', () => {
  const storage = fakeStorage()
  put(storage, {
    active: 'ep_a',
    endpoints: [
      { id: 'ep_a', base_url: 'http://first' },
      { id: 'ep_a', base_url: 'http://second' },
    ],
  })
  const registry = readRegistry(storage)
  assert.equal(registry.endpoints.length, 1)
  assert.equal(registry.endpoints[0].base_url, 'http://first')
})

test('an unrecognized kind coerces to the LESS privileged value', () => {
  const storage = fakeStorage()
  put(storage, {
    active: 'ep_a',
    endpoints: [
      { id: 'ep_a', kind: 'local' },
      { id: 'ep_b', kind: 'wheelbarrow' },
      { id: 'ep_c' },
    ],
  })
  assert.deepEqual(
    readRegistry(storage).endpoints.map((e) => e.kind),
    ['local', 'remote', 'remote'],
  )
})

test('a dangling active pointer resolves to a present row, not to nothing', () => {
  const storage = fakeStorage()
  put(storage, { active: 'ep_gone', endpoints: [{ id: 'ep_a', base_url: 'http://10.0.0.4:10000' }] })
  const registry = readRegistry(storage)
  assert.equal(registry.active, 'ep_a')
  assert.equal(activeBaseUrl(registry), 'http://10.0.0.4:10000')
})

test('remembering a second gateway adds a row and re-points active', () => {
  const storage = fakeStorage()
  rememberGateway(storage, { baseUrl: 'http://10.0.0.4:10000', mintId: () => 'ep_one' })
  rememberGateway(storage, { baseUrl: 'http://10.0.0.5:10000', label: 'Studio', mintId: () => 'ep_two' })

  const registry = readRegistry(storage)
  assert.deepEqual(
    registry.endpoints.map((e) => e.base_url),
    ['http://10.0.0.4:10000', 'http://10.0.0.5:10000'],
  )
  assert.equal(registry.active, 'ep_two')
  assert.equal(activeEndpoint(registry).label, 'Studio')
})

test('forgetting the active row leaves the others and re-points active', () => {
  const storage = fakeStorage()
  rememberGateway(storage, { baseUrl: 'http://10.0.0.4:10000', mintId: () => 'ep_one' })
  rememberGateway(storage, { baseUrl: 'http://10.0.0.5:10000', mintId: () => 'ep_two' })

  const next = forgetActiveGateway(storage)
  assert.deepEqual(
    next.endpoints.map((e) => e.id),
    ['ep_one'],
  )
  assert.equal(next.active, 'ep_one')
  assert.deepEqual(readRegistry(storage), next, 'the drop must be persisted, not just returned')
})

test('a write that throws is reported, not raised', () => {
  const hostile = {
    getItem: () => null,
    setItem() {
      throw new Error('denied')
    },
  }
  assert.equal(writeRegistry(hostile, EMPTY_REGISTRY), false)
  assert.equal(rememberGateway(hostile, { baseUrl: 'http://10.0.0.4:10000' }).stored, false)
})

test('a round trip preserves every declared field', () => {
  const storage = fakeStorage()
  rememberGateway(storage, { baseUrl: 'http://10.0.0.4:10000', label: 'Studio', mintId: () => 'ep_one' })
  const [row] = readRegistry(storage).endpoints
  assert.deepEqual(Object.keys(row).sort(), [...ENDPOINT_FIELDS].sort())
})
