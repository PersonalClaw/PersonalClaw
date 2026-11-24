// ── What this browser keeps of the chat, beside the gateway ──────────────────────────────────
//
// Two stores in one IndexedDB database, `personalclaw` (#4012):
//   - `drafts`: what the owner is typing in each chat, keyed by the chat (`drafts.ts`);
//   - `outbox`: a send the gateway could not take, keyed by its `client_message_id`, waiting for
//     PersonalClaw to be back (`outbox.ts`).
// Both live in this browser and never in the home. A browser that keeps no IndexedDB (a private
// window that refuses it, a test without one) keeps nothing: every read is empty and every write
// a no-op, said once in the console, and the composer works as it did before.
//
// The adapter is the whole of IndexedDB this app touches. A test swaps in the in-memory backend
// (`setStoreForTests`), whose reads and writes are the same promises.

export type StoreName = 'drafts' | 'outbox'

export const DB_NAME = 'personalclaw'
export const DB_VERSION = 1
const STORES: readonly StoreName[] = ['drafts', 'outbox']

export interface StoreBackend {
  get<T>(store: StoreName, key: string): Promise<T | undefined>
  put<T>(store: StoreName, key: string, value: T): Promise<void>
  delete(store: StoreName, key: string): Promise<void>
  /** Every value in *store*, with its key, in key order. */
  entries<T>(store: StoreName): Promise<[string, T][]>
  clear(store: StoreName): Promise<void>
}

/** Keeps nothing: what a browser with no IndexedDB has. */
const NOTHING: StoreBackend = {
  get: async () => undefined,
  put: async () => {},
  delete: async () => {},
  entries: async () => [],
  clear: async () => {},
}

let said = false
function keepsNothing(why: unknown): StoreBackend {
  if (!said) {
    said = true
    console.warn('This browser keeps no drafts or waiting sends: IndexedDB is not available.', why)
  }
  return NOTHING
}

function asPromise<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function opened(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      for (const name of STORES) {
        if (!request.result.objectStoreNames.contains(name)) request.result.createObjectStore(name)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
    request.onblocked = () => reject(new Error('the database is held open by an older tab'))
  })
}

/** The IndexedDB backend: one connection, opened on first use and kept. */
function indexedDbBackend(): StoreBackend {
  let db: Promise<IDBDatabase> | null = null
  const store = async (name: StoreName, mode: IDBTransactionMode) => {
    db ??= opened()
    return (await db).transaction(name, mode).objectStore(name)
  }
  return {
    async get<T>(name: StoreName, key: string) {
      return (await asPromise((await store(name, 'readonly')).get(key))) as T | undefined
    },
    async put<T>(name: StoreName, key: string, value: T) {
      await asPromise((await store(name, 'readwrite')).put(value, key))
    },
    async delete(name: StoreName, key: string) {
      await asPromise((await store(name, 'readwrite')).delete(key))
    },
    async entries<T>(name: StoreName) {
      const s = await store(name, 'readonly')
      const [keys, values] = await Promise.all([asPromise(s.getAllKeys()), asPromise(s.getAll())])
      return keys.map((k, i) => [String(k), values[i] as T] as [string, T])
    },
    async clear(name: StoreName) {
      await asPromise((await store(name, 'readwrite')).clear())
    },
  }
}

/** An in-memory backend with the same promises, for tests. */
export function memoryBackend(): StoreBackend {
  const maps = new Map<StoreName, Map<string, unknown>>(STORES.map((s) => [s, new Map()]))
  const of = (s: StoreName) => maps.get(s)!
  return {
    get: async <T,>(s: StoreName, k: string) => structuredClone(of(s).get(k)) as T | undefined,
    put: async <T,>(s: StoreName, k: string, v: T) => { of(s).set(k, structuredClone(v)) },
    delete: async (s: StoreName, k: string) => { of(s).delete(k) },
    entries: async <T,>(s: StoreName) =>
      [...of(s).entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([k, v]) => [k, structuredClone(v) as T] as [string, T]),
    clear: async (s: StoreName) => { of(s).clear() },
  }
}

let backend: StoreBackend | null = null

/** Said on the window when a draft is written or goes, so the chat list's "Draft" follows. */
export const DRAFTS_CHANGED = 'personalclaw:drafts-changed'

function announcing(inner: StoreBackend): StoreBackend {
  const said = (s: StoreName) => {
    if (s === 'drafts' && typeof window !== 'undefined') window.dispatchEvent(new Event(DRAFTS_CHANGED))
  }
  return {
    get: inner.get.bind(inner),
    entries: inner.entries.bind(inner),
    put: async (s, k, v) => { await inner.put(s, k, v); said(s) },
    delete: async (s, k) => { await inner.delete(s, k); said(s) },
    clear: async (s) => { await inner.clear(s); said(s) },
  }
}

/** The backend this page keeps its drafts and waiting sends in. */
export function browserStore(): StoreBackend {
  if (backend) return backend
  try {
    backend = typeof indexedDB === 'undefined' ? keepsNothing('no indexedDB') : announcing(guarded(indexedDbBackend()))
  } catch (err) {
    backend = keepsNothing(err)
  }
  return backend
}

/** A backend whose failures (a quota, a database a browser refuses) read as keeping nothing,
 *  rather than as an error the composer has to handle. */
function guarded(inner: StoreBackend): StoreBackend {
  const soft = <A extends unknown[], R>(fn: (...a: A) => Promise<R>, empty: R) =>
    async (...a: A): Promise<R> => {
      try { return await fn(...a) } catch (err) { keepsNothing(err); return empty }
    }
  return {
    get: soft(inner.get.bind(inner), undefined) as StoreBackend['get'],
    put: soft(inner.put.bind(inner), undefined) as StoreBackend['put'],
    delete: soft(inner.delete.bind(inner), undefined),
    entries: soft(inner.entries.bind(inner), []) as StoreBackend['entries'],
    clear: soft(inner.clear.bind(inner), undefined),
  }
}

/** Use *b* (a `memoryBackend()`) as the page's store; `null` goes back to IndexedDB. */
export function setStoreForTests(b: StoreBackend | null): void {
  backend = b && announcing(b)
  said = false
}

/** Every draft and every waiting send goes: on sign-out. Nothing this browser kept of a chat
 *  outlives the sign-in it was kept under. */
export async function forgetEverythingKept(): Promise<void> {
  const store = browserStore()
  await Promise.all([store.clear('drafts'), store.clear('outbox')])
}
