import { describe, it, expect } from 'vitest'
import {
  EMPTY_REGISTRY,
  ENDPOINT_KEY_PREFIX,
  REGISTRY_STORAGE_KEY,
  activeEndpoint,
  addEndpoint,
  clearEndpointState,
  endpointKey,
  endpointScope,
  findEndpoint,
  loadRegistry,
  newEndpointId,
  parseEndpointKey,
  parseRegistry,
  removeEndpoint,
  saveRegistry,
  serializeRegistry,
  setActive,
  type CompanionEndpoint,
  type EndpointRegistry,
  type KeyValueStore,
} from './endpoints'

// ── What this suite is actually measuring ────────────────────────────────────────────────────
//
// A companion shell holds N paired gateways — N independent brains — inside ONE storage scope.
// (Measured, not assumed: `desktop/main.js:768` does `wc.loadURL(backendUrl)`, loading the SPA
// from the active gateway's own origin, and `backendUrl` at `:143` is a single string from that
// gateway's READY line; `grep -n partition desktop/main.js` finds nothing, so the SPA's own
// storage is already partitioned per-origin and cannot bleed. The shell's scope is the one that
// spans all N.) So the headline claim under test is not "keys are prefixed" — it is:
//
//     two endpoints writing THE SAME logical key into THE SAME store do not see each other.
//
// `writes the bare logical key and collides` below is the vacuity floor for exactly that claim:
// it asserts the hazard is REAL in this fake store, so the zero-bleed tests are demonstrating
// something the helper prevents rather than something the store would have done anyway.

/** A `Storage`-shaped fake. Insertion-ordered like the real thing, and `key(i)`/`length` are live
 *  views over the map — which is what makes the sweep-while-deleting bug in `clearEndpointState`
 *  reachable if the implementation stops snapshotting. Never touches real `localStorage`. */
function fakeStore(): KeyValueStore & { size(): number; raw(): Map<string, string> } {
  const m = new Map<string, string>()
  return {
    getItem: (k) => (m.has(k) ? (m.get(k) as string) : null),
    setItem: (k, v) => void m.set(k, v),
    removeItem: (k) => void m.delete(k),
    get length() {
      return m.size
    },
    key: (i) => [...m.keys()][i] ?? null,
    size: () => m.size,
    raw: () => m,
  }
}

const HOME: CompanionEndpoint = {
  id: 'ep_home00000a',
  label: 'Home laptop',
  base_url: 'http://claw.local:10000',
  kind: 'local',
  device_session_ref: 'nonce-home',
}
const WORK: CompanionEndpoint = {
  id: 'ep_work00000b',
  label: 'Work mini',
  base_url: 'https://work.example:10000',
  kind: 'remote',
  device_session_ref: 'nonce-work',
}

describe('registry round-trip', () => {
  it('round-trips the owner-fixed shape, kind and device_session_ref included', () => {
    const reg: EndpointRegistry = { active: WORK.id, endpoints: [HOME, WORK] }
    const back = parseRegistry(serializeRegistry(reg))
    expect(back).toEqual(reg)
    // Field-by-field, so a "field-for-field" coercion that silently drops one is caught.
    expect(back.endpoints[0]).toEqual({
      id: 'ep_home00000a',
      label: 'Home laptop',
      base_url: 'http://claw.local:10000',
      kind: 'local',
      device_session_ref: 'nonce-home',
    })
    expect(back.endpoints[1].kind).toBe('remote')
    expect(back.endpoints[1].device_session_ref).toBe('nonce-work')
    expect(back.active).toBe(WORK.id)
  })

  it('round-trips through a store under the one shell-global key', () => {
    const store = fakeStore()
    const reg = addEndpoint(addEndpoint(EMPTY_REGISTRY, HOME), WORK)
    saveRegistry(store, reg)
    expect(store.getItem(REGISTRY_STORAGE_KEY)).toContain('claw.local')
    expect(loadRegistry(store)).toEqual(reg)
    // The registry key is deliberately NOT namespaced — it is the thing that spans endpoints.
    expect(REGISTRY_STORAGE_KEY.startsWith(ENDPOINT_KEY_PREFIX)).toBe(false)
  })
})

describe('zero state bleed inside ONE storage scope', () => {
  it('two endpoints write the same logical key and each reads back its own', () => {
    const store = fakeStore()
    const home = endpointScope(store, HOME.id)
    const work = endpointScope(store, WORK.id)

    // The same logical key, twice, in ONE store — the shell's actual situation.
    home.set('inbox', 'home-inbox')
    work.set('inbox', 'work-inbox')

    expect(home.get('inbox')).toBe('home-inbox')
    expect(work.get('inbox')).toBe('work-inbox')
    // Both survived: nothing overwrote anything.
    expect(store.size()).toBe(2)

    // Neither scope can even address the other's slot: `id` is not an argument of `get`.
    home.set('inbox', 'home-inbox-v2')
    expect(work.get('inbox')).toBe('work-inbox')
    work.set('inbox', 'work-inbox-v2')
    expect(home.get('inbox')).toBe('home-inbox-v2')

    // ...and neither can SEE the other's key.
    expect(home.logicalKeys()).toEqual(['inbox'])
    expect(work.logicalKeys()).toEqual(['inbox'])
  })

  it('keeps sessions, settings and prefs separate per endpoint', () => {
    const store = fakeStore()
    const home = endpointScope(store, HOME.id)
    const work = endpointScope(store, WORK.id)
    for (const k of ['sessions', 'settings', 'prefs']) {
      home.set(k, `home:${k}`)
      work.set(k, `work:${k}`)
    }
    expect([...home.logicalKeys()].sort()).toEqual(['prefs', 'sessions', 'settings'])
    for (const k of ['sessions', 'settings', 'prefs']) {
      expect(home.get(k)).toBe(`home:${k}`)
      expect(work.get(k)).toBe(`work:${k}`)
    }
    expect(store.size()).toBe(6)
  })

  it('writes the bare logical key and collides — the vacuity floor', () => {
    // 🔑 THE HAZARD IS REAL. Same fake store, namespacing BYPASSED: write the logical key
    // directly, as a shell that forgot to namespace would. One slot, last write wins, the first
    // endpoint's value gone. Without this assertion the zero-bleed tests above could be passing
    // on a property the store had all along.
    const store = fakeStore()
    store.setItem('inbox', 'home-inbox')
    store.setItem('inbox', 'work-inbox')
    expect(store.size()).toBe(1)
    expect(store.getItem('inbox')).toBe('work-inbox')
    expect(store.getItem('inbox')).not.toBe('home-inbox')

    // The same two writes THROUGH the helper: two slots, both intact.
    const viaHelper = fakeStore()
    endpointScope(viaHelper, HOME.id).set('inbox', 'home-inbox')
    endpointScope(viaHelper, WORK.id).set('inbox', 'work-inbox')
    expect(viaHelper.size()).toBe(2)
    expect(endpointScope(viaHelper, HOME.id).get('inbox')).toBe('home-inbox')
  })
})

describe('forgetting one endpoint', () => {
  it("clearing A's namespaced state leaves B's intact", () => {
    const store = fakeStore()
    const home = endpointScope(store, HOME.id)
    const work = endpointScope(store, WORK.id)
    for (const k of ['inbox', 'sessions', 'settings']) {
      home.set(k, `home:${k}`)
      work.set(k, `work:${k}`)
    }
    // An unrelated shell-global key must also survive a per-endpoint purge.
    saveRegistry(store, { active: HOME.id, endpoints: [HOME, WORK] })

    clearEndpointState(store, HOME.id)

    expect(home.logicalKeys()).toEqual([])
    for (const k of ['inbox', 'sessions', 'settings']) {
      expect(home.get(k)).toBeNull()
      expect(work.get(k)).toBe(`work:${k}`) // revoking one gateway breaks only that entry
    }
    expect(loadRegistry(store).endpoints).toHaveLength(2)
  })

  it('sweeps every one of A’s keys even though removal shifts the store’s indices', () => {
    // If the implementation iterated `store.key(i)` while deleting, it would skip alternate keys.
    const store = fakeStore()
    const home = endpointScope(store, HOME.id)
    for (let i = 0; i < 8; i++) home.set(`k${i}`, String(i))
    endpointScope(store, WORK.id).set('k0', 'keep')
    expect(store.size()).toBe(9)
    home.clear()
    expect(store.size()).toBe(1)
    expect([...store.raw().keys()]).toEqual([endpointKey(WORK.id, 'k0')])
  })
})

describe('key encoding is unambiguous', () => {
  it("distinguishes {id:'a', key:'b:c'} from {id:'a:b', key:'c'} — a naive id+':'+key would not", () => {
    // The naive form both would produce:
    expect('a' + ':' + 'b:c').toBe('a:b:c')
    expect('a:b' + ':' + 'c').toBe('a:b:c') // ← collision, in the mechanism meant to prevent one

    const one = endpointKey('a', 'b:c')
    const two = endpointKey('a:b', 'c')
    expect(one).not.toBe(two)
    expect(parseEndpointKey(one)).toEqual({ id: 'a', logicalKey: 'b:c' })
    expect(parseEndpointKey(two)).toEqual({ id: 'a:b', logicalKey: 'c' })
  })

  it('two colliding-under-naive endpoints do not bleed in one store', () => {
    const store = fakeStore()
    // Distinct endpoints whose ids differ only by a separator boundary.
    endpointScope(store, 'a').set('b:c', 'first')
    endpointScope(store, 'a:b').set('c', 'second')
    expect(store.size()).toBe(2)
    expect(endpointScope(store, 'a').get('b:c')).toBe('first')
    expect(endpointScope(store, 'a:b').get('c')).toBe('second')
    // Neither sees the other's logical key.
    expect(endpointScope(store, 'a').logicalKeys()).toEqual(['b:c'])
    expect(endpointScope(store, 'a:b').logicalKeys()).toEqual(['c'])
    // A purge of one is still surgical.
    clearEndpointState(store, 'a')
    expect(endpointScope(store, 'a:b').get('c')).toBe('second')
  })

  it('is injective across a spread of adversarial id/key pairs', () => {
    const pairs: Array<[string, string]> = [
      ['', 'inbox'],
      ['a', ''],
      ['a', 'b'],
      ['a', ':b'],
      ['a:', 'b'],
      [':', ':'],
      ['1', '2:3'],
      ['1:2', '3'],
      ['ep:3:x', 'y'],
      ['x', 'ep:1:y:z'],
    ]
    const seen = new Map<string, [string, string]>()
    for (const [id, key] of pairs) {
      const encoded = endpointKey(id, key)
      expect(seen.has(encoded), `collision: ${JSON.stringify(seen.get(encoded))} vs [${id},${key}]`).toBe(false)
      seen.set(encoded, [id, key])
      expect(parseEndpointKey(encoded)).toEqual({ id, logicalKey: key })
    }
    expect(seen.size).toBe(pairs.length)
  })

  it('ignores keys that are not ours', () => {
    for (const k of ['cache:sessions', REGISTRY_STORAGE_KEY, 'ep:', 'ep::x', 'ep:x:y', 'ep:9:ab:c', 'ep:2:ab']) {
      expect(parseEndpointKey(k)).toBeUndefined()
    }
  })

  it("does not let one endpoint's sweep reach a foreign key", () => {
    const store = fakeStore()
    store.setItem('cache:sessions', 'spa-cache')
    store.setItem(REGISTRY_STORAGE_KEY, '{}')
    endpointScope(store, HOME.id).set('inbox', 'x')
    clearEndpointState(store, HOME.id)
    expect(store.getItem('cache:sessions')).toBe('spa-cache')
    expect(store.getItem(REGISTRY_STORAGE_KEY)).toBe('{}')
    expect(store.size()).toBe(2)
  })
})

describe('parseRegistry is total', () => {
  it('malformed JSON resolves to the empty registry', () => {
    for (const raw of ['', '{', 'not json', '[]', 'null', '3', '"a"', undefined, null]) {
      expect(parseRegistry(raw)).toEqual(EMPTY_REGISTRY)
    }
  })

  it('a missing active adopts the first endpoint', () => {
    const reg = parseRegistry(JSON.stringify({ endpoints: [HOME, WORK] }))
    expect(reg.active).toBe(HOME.id)
    expect(activeEndpoint(reg)).toEqual(HOME)
  })

  it('an active naming an absent id is re-pointed, not left dangling', () => {
    const reg = parseRegistry(JSON.stringify({ active: 'ep_ghost', endpoints: [WORK] }))
    expect(reg.active).toBe(WORK.id)
    expect(activeEndpoint(reg)).toEqual(WORK)
  })

  it('an active naming an absent id with no endpoints at all resolves to empty', () => {
    expect(parseRegistry(JSON.stringify({ active: 'ep_ghost', endpoints: [] }))).toEqual(EMPTY_REGISTRY)
  })

  it('duplicate ids collapse to the first occurrence', () => {
    const shadow = { ...WORK, id: HOME.id, label: 'impostor' }
    const reg = parseRegistry(JSON.stringify({ active: HOME.id, endpoints: [HOME, shadow] }))
    expect(reg.endpoints).toHaveLength(1)
    expect(reg.endpoints[0].label).toBe('Home laptop') // first wins — its state was the one written
    expect(reg.active).toBe(HOME.id)
  })

  it('drops rows that cannot be endpoints and keeps the ones that can', () => {
    const reg = parseRegistry(
      JSON.stringify({ active: WORK.id, endpoints: [null, 'x', 42, [], { label: 'no id' }, WORK] }),
    )
    expect(reg.endpoints).toEqual([WORK])
    expect(reg.active).toBe(WORK.id)
  })

  it('coerces an unrecognized kind to the LESS privileged remote', () => {
    const reg = parseRegistry(JSON.stringify({ endpoints: [{ ...HOME, kind: 'admin' }, { ...WORK, kind: 7 }] }))
    expect(reg.endpoints.map((e) => e.kind)).toEqual(['remote', 'remote'])
    // `local` still survives when it is actually declared.
    expect(parseRegistry(JSON.stringify({ endpoints: [HOME] })).endpoints[0].kind).toBe('local')
  })

  it('fills absent optional strings rather than yielding undefined fields', () => {
    const reg = parseRegistry(JSON.stringify({ endpoints: [{ id: 'ep_bare' }] }))
    expect(reg.endpoints[0]).toEqual({
      id: 'ep_bare',
      label: '',
      base_url: '',
      kind: 'remote',
      device_session_ref: '',
    })
  })

  it('a store whose getItem throws resolves instead of propagating', () => {
    const hostile: KeyValueStore = {
      getItem: () => {
        throw new Error('SecurityError: storage is disabled')
      },
      setItem: () => {},
      removeItem: () => {},
      length: 0,
      key: () => null,
    }
    expect(loadRegistry(hostile)).toEqual(EMPTY_REGISTRY)
  })
})

describe('add / remove / setActive', () => {
  it('adding mints an id and makes the new endpoint active', () => {
    const reg = addEndpoint(EMPTY_REGISTRY, {
      label: 'Home laptop',
      base_url: 'http://claw.local:10000',
      kind: 'local',
      device_session_ref: 'n1',
    })
    expect(reg.endpoints).toHaveLength(1)
    expect(reg.endpoints[0].id).toMatch(/^ep_[a-z0-9]{12}$/)
    expect(reg.active).toBe(reg.endpoints[0].id)
  })

  it('re-adding a known id replaces the row in place and preserves the id', () => {
    const two = addEndpoint(addEndpoint(EMPTY_REGISTRY, HOME), WORK)
    const moved = addEndpoint(two, { ...HOME, base_url: 'http://100.64.0.2:10000', label: 'Home (VPN)' })
    expect(moved.endpoints).toHaveLength(2)
    expect(moved.endpoints[0].id).toBe(HOME.id) // same brain, new address → same namespace
    expect(moved.endpoints[0].base_url).toBe('http://100.64.0.2:10000')
    expect(moved.endpoints[1]).toEqual(WORK) // position preserved
    expect(moved.active).toBe(HOME.id)
  })

  it('removing the active endpoint falls back to the first survivor', () => {
    const reg = { active: HOME.id, endpoints: [HOME, WORK] }
    const after = removeEndpoint(reg, HOME.id)
    expect(after.endpoints).toEqual([WORK])
    expect(after.active).toBe(WORK.id)
    expect(activeEndpoint(after)).toEqual(WORK)
  })

  it('removing the last endpoint leaves an empty, non-dangling active', () => {
    const after = removeEndpoint({ active: WORK.id, endpoints: [WORK] }, WORK.id)
    expect(after).toEqual(EMPTY_REGISTRY)
    expect(activeEndpoint(after)).toBeUndefined()
  })

  it('removing a non-active endpoint leaves the pointer alone; an unknown id is a no-op', () => {
    const reg = { active: HOME.id, endpoints: [HOME, WORK] }
    expect(removeEndpoint(reg, WORK.id)).toEqual({ active: HOME.id, endpoints: [HOME] })
    expect(removeEndpoint(reg, 'ep_ghost')).toBe(reg)
  })

  it('removing an endpoint does NOT purge its state — forget and wipe are separate calls', () => {
    const store = fakeStore()
    endpointScope(store, HOME.id).set('inbox', 'x')
    saveRegistry(store, removeEndpoint({ active: HOME.id, endpoints: [HOME, WORK] }, HOME.id))
    expect(endpointScope(store, HOME.id).get('inbox')).toBe('x')
    clearEndpointState(store, HOME.id)
    expect(endpointScope(store, HOME.id).get('inbox')).toBeNull()
  })

  it('setActive re-points to a known id and refuses an unknown one', () => {
    const reg = { active: HOME.id, endpoints: [HOME, WORK] }
    expect(setActive(reg, WORK.id).active).toBe(WORK.id)
    expect(setActive(reg, WORK.id).endpoints).toEqual(reg.endpoints) // switch changes the pointer only
    expect(setActive(reg, 'ep_ghost')).toBe(reg)
  })

  it('findEndpoint locates by id and misses cleanly', () => {
    const reg = { active: HOME.id, endpoints: [HOME, WORK] }
    expect(findEndpoint(reg, WORK.id)).toEqual(WORK)
    expect(findEndpoint(reg, 'ep_ghost')).toBeUndefined()
  })
})

describe('newEndpointId', () => {
  it('is opaque, URL-safe, and needs no escaping in a storage key', () => {
    const id = newEndpointId()
    expect(id).toMatch(/^ep_[a-z0-9]{12}$/)
    expect(parseEndpointKey(endpointKey(id, 'inbox'))).toEqual({ id, logicalKey: 'inbox' })
  })

  it('is not derived from base_url — two endpoints on one host get distinct ids', () => {
    const a = addEndpoint(EMPTY_REGISTRY, { ...HOME, id: undefined })
    const b = addEndpoint(a, { ...WORK, id: undefined, base_url: HOME.base_url })
    expect(b.endpoints[0].base_url).toBe(b.endpoints[1].base_url)
    expect(b.endpoints[0].id).not.toBe(b.endpoints[1].id)
  })

  it('accepts an injected rand, and does not collide over many draws', () => {
    let n = 0
    expect(newEndpointId(() => (n++ % 36) / 36)).toBe('ep_abcdefghijkl')
    const ids = new Set(Array.from({ length: 2000 }, () => newEndpointId()))
    expect(ids.size).toBe(2000)
  })

  it('survives a rand that returns exactly 1', () => {
    expect(newEndpointId(() => 1)).toBe('ep_aaaaaaaaaaaa')
  })
})
