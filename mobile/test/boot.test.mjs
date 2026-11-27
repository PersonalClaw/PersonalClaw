import assert from 'node:assert/strict'
import test from 'node:test'

import { connect, connectFromScan, platformOf, readStoredGateway, start } from '../www/shell/boot.mjs'
import { ENDPOINT_FIELDS, REGISTRY_STORAGE_KEY } from '../www/shell/registry.mjs'

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial))
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    removeItem: (k) => map.delete(k),
  }
}

function fakeLocation() {
  const replaced = []
  return { replaced, replace: (url) => replaced.push(url) }
}

const stored = (storage) => JSON.parse(storage.map.get(REGISTRY_STORAGE_KEY))

test('connecting hands the WebView to the served companion', () => {
  const storage = fakeStorage()
  const location = fakeLocation()
  const result = connect({ raw: '192.168.1.10:10000', storage, location })

  assert.deepEqual(result, { ok: true, gatewayUrl: 'http://192.168.1.10:10000' })
  assert.deepEqual(location.replaced, ['http://192.168.1.10:10000/#/companion'])
})

test('the connect writes the registry endpoints.ts declares, under ITS key', () => {
  // The anti-second-contract assertion. A bespoke `personalclaw.gatewayUrl` would pass every
  // other test in this file and still be the thing endpoints.ts exists to prevent.
  const storage = fakeStorage()
  connect({ raw: '192.168.1.10:10000', storage, location: fakeLocation() })

  assert.deepEqual([...storage.map.keys()], [REGISTRY_STORAGE_KEY])
  const registry = stored(storage)
  assert.deepEqual(Object.keys(registry).sort(), ['active', 'endpoints'])
  assert.equal(registry.endpoints.length, 1)

  const row = registry.endpoints[0]
  assert.deepEqual(Object.keys(row).sort(), [...ENDPOINT_FIELDS].sort())
  assert.equal(registry.active, row.id)
  assert.match(row.id, /^ep_[a-z0-9]{12}$/)
  assert.equal(row.base_url, 'http://192.168.1.10:10000')
  assert.equal(row.kind, 'remote', 'a phone never owns a gateway`s lifecycle')
  assert.equal(row.device_session_ref, '')
})

test('the registry holds no credential — only a URL, a label and ids', () => {
  const storage = fakeStorage()
  connect({ raw: '10.0.0.4:10000', storage, location: fakeLocation() })
  const serialized = storage.map.get(REGISTRY_STORAGE_KEY)
  // The device session is an httponly `Set-Cookie` from `pair/complete`, so it lives in the
  // WebView cookie jar and is invisible to this script. Nothing token-shaped can be here.
  for (const smell of ['token', 'secret', 'cookie', 'pc_token', 'password']) {
    assert.ok(!serialized.toLowerCase().includes(smell), `${smell} must not be persisted`)
  }
})

test('a rejected address navigates NOWHERE and leaves no row', () => {
  const storage = fakeStorage()
  const location = fakeLocation()
  const result = connect({ raw: 'http://companion.example.com', storage, location })

  assert.equal(result.ok, false)
  assert.equal(result.code, 'NOT_PRIVATE')
  assert.match(result.message, /private network/)
  assert.deepEqual(location.replaced, [], 'a refused gateway must not be navigated to')
  assert.equal(storage.map.size, 0)
})

test('relaunching the same gateway reuses its row rather than accumulating one per launch', () => {
  const storage = fakeStorage()
  connect({ raw: '192.168.1.10:10000', storage, location: fakeLocation() })
  const first = stored(storage).endpoints[0].id

  connect({ raw: 'http://192.168.1.10:10000/#/settings', storage, location: fakeLocation() })
  const registry = stored(storage)

  assert.equal(registry.endpoints.length, 1)
  assert.equal(registry.endpoints[0].id, first, 'the id is minted once at pair time, not per launch')
})

test('a scan sends the WebView to the gateway`s own /pair page, not to a native redemption', () => {
  const storage = fakeStorage()
  const location = fakeLocation()
  const result = connectFromScan({
    scanned: 'http://192.168.1.10:10000/pair?code=ABCD-2345',
    storage,
    location,
  })

  assert.deepEqual(result, { ok: true, gatewayUrl: 'http://192.168.1.10:10000' })
  assert.deepEqual(location.replaced, ['http://192.168.1.10:10000/pair?code=ABCD-2345'])
  assert.equal(stored(storage).endpoints[0].base_url, 'http://192.168.1.10:10000')
})

test('storage that throws costs the shortcut, not the session', () => {
  const hostile = {
    getItem() {
      throw new Error('storage denied')
    },
    setItem() {
      throw new Error('storage denied')
    },
    removeItem() {
      throw new Error('storage denied')
    },
  }
  assert.equal(readStoredGateway(hostile), '')

  const location = fakeLocation()
  // Still connects: the address was usable this launch even though it cannot be remembered.
  assert.equal(connect({ raw: '10.0.0.4:10000', storage: hostile, location }).ok, true)
  assert.deepEqual(location.replaced, ['http://10.0.0.4:10000/#/companion'])
})

// ── start(): the second-launch path ────────────────────────────────────────

function fakeDoc(elements = {}) {
  const listeners = new Map()
  const nodes = {
    shell: { style: {} },
    connect: {
      addEventListener: (event, handler) => listeners.set(event, handler),
    },
    gateway: { value: '' },
    status: { textContent: '', hidden: true },
    ...elements,
  }
  return { doc: { documentElement: {}, getElementById: (id) => nodes[id] ?? null }, nodes, listeners }
}

const view = { getComputedStyle: () => ({ getPropertyValue: () => '0px' }), addEventListener() {} }

/** A registry naming one active endpoint at `baseUrl`. */
function registryFor(baseUrl) {
  return {
    [REGISTRY_STORAGE_KEY]: JSON.stringify({
      active: 'ep_aaaaaaaaaaaa',
      endpoints: [
        {
          id: 'ep_aaaaaaaaaaaa',
          label: 'Studio',
          base_url: baseUrl,
          kind: 'remote',
          device_session_ref: '',
        },
      ],
    }),
  }
}

test('a remembered gateway goes straight through to the companion', () => {
  const { doc, nodes } = fakeDoc()
  const storage = fakeStorage(registryFor('http://192.168.1.10:10000'))
  const location = fakeLocation()

  start({ doc, view, storage, location })

  assert.deepEqual(location.replaced, ['http://192.168.1.10:10000/#/companion'])
  assert.equal(nodes.gateway.value, 'http://192.168.1.10:10000')
})

test('a remembered gateway that no longer validates is dropped, not left wedging the app', () => {
  const { doc, nodes } = fakeDoc()
  const storage = fakeStorage(registryFor('http://companion.example.com'))
  const location = fakeLocation()

  start({ doc, view, storage, location })

  assert.deepEqual(location.replaced, [])
  assert.deepEqual(stored(storage).endpoints, [], 'the stale row must be dropped')
  assert.equal(nodes.status.hidden, false)
  assert.match(nodes.status.textContent, /private network/)
})

test('with nothing remembered the form drives the connect', () => {
  const { doc, nodes, listeners } = fakeDoc()
  const storage = fakeStorage()
  const location = fakeLocation()

  start({ doc, view, storage, location })
  assert.deepEqual(location.replaced, [], 'nothing remembered means nothing to navigate to yet')

  nodes.gateway.value = 'my-box.local:10000'
  let defaultPrevented = false
  listeners.get('submit')({ preventDefault: () => (defaultPrevented = true) })

  assert.ok(defaultPrevented, 'a form submit that reloads the shell loses the input')
  assert.deepEqual(location.replaced, ['http://my-box.local:10000/#/companion'])
})

test('safe areas are applied to the shell element during start', () => {
  const { doc, nodes } = fakeDoc()
  const insetView = {
    getComputedStyle: () => ({ getPropertyValue: (name) => (name === '--pc-safe-top' ? '47px' : '0px') }),
    addEventListener() {},
  }
  start({ doc, view: insetView, storage: fakeStorage(), location: fakeLocation() })
  assert.equal(nodes.shell.style.paddingTop, '47px')
})

// ── Android: a gateway the app opens outside itself ────────────────────────

const android = { ...view, Capacitor: { getPlatform: () => 'android' } }

test('on Android, a gateway at an IP address is remembered but not handed off unasked', () => {
  const storage = fakeStorage()
  const location = fakeLocation()
  const result = connect({ raw: '192.168.1.10:10000', storage, location, platform: 'android' })

  assert.equal(result.ok, false)
  assert.equal(result.code, 'OPENS_OUTSIDE')
  assert.match(result.message, /192\.168\.1\.10 is an IP address.*outside the app.*\.local or Tailscale name/)
  assert.equal(result.target, 'http://192.168.1.10:10000/#/companion')
  assert.deepEqual(location.replaced, [], 'no silent hand-off')
  assert.equal(stored(storage).endpoints[0].base_url, 'http://192.168.1.10:10000')
})

test('on Android, a .local or Tailscale name goes straight through, and on iOS an IP does', () => {
  for (const [raw, platform] of [['my-box.local:10000', 'android'], ['box.tail1.ts.net', 'android'],
    ['192.168.1.10:10000', 'ios']]) {
    const location = fakeLocation()
    assert.equal(connect({ raw, storage: fakeStorage(), location, platform }).ok, true, `${raw} on ${platform}`)
    assert.equal(location.replaced.length, 1)
  }
})

test('on Android, a scanned pairing link at an IP address asks first too', () => {
  const location = fakeLocation()
  const result = connectFromScan({
    scanned: 'http://10.0.0.4:10000/pair?code=ABCD-EFGH', storage: fakeStorage(), location, platform: 'android',
  })
  assert.equal(result.code, 'OPENS_OUTSIDE')
  assert.equal(result.target, 'http://10.0.0.4:10000/pair?code=ABCD-EFGH')
  assert.deepEqual(location.replaced, [])
})

test('on Android, a remembered IP gateway says why and opens only on Open in browser', () => {
  const clicks = new Map()
  const away = { hidden: true, addEventListener: (event, handler) => clicks.set(event, handler) }
  const { doc, nodes } = fakeDoc({ 'open-outside': away })
  const storage = fakeStorage(registryFor('http://192.168.1.10:10000'))
  const location = fakeLocation()

  start({ doc, view: android, storage, location })

  assert.deepEqual(location.replaced, [], 'a relaunch never opens it outside unasked')
  assert.equal(nodes.status.hidden, false)
  assert.match(nodes.status.textContent, /outside the app/)
  assert.equal(away.hidden, false)
  assert.equal(stored(storage).endpoints.length, 1, 'the gateway stays remembered')
  clicks.get('click')()
  assert.deepEqual(location.replaced, ['http://192.168.1.10:10000/#/companion'])
})

test('the platform is what Capacitor says, and web in a plain browser', () => {
  assert.equal(platformOf(android), 'android')
  assert.equal(platformOf(view), 'web')
  assert.equal(platformOf({ Capacitor: { getPlatform: () => { throw new Error('x') } } }), 'web')
})
