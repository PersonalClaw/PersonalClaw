// SDK host map (A6) — the runtime backing for `@personalclaw/app-sdk`.
//
// A contributed app ships an ESM bundle that imports from `@personalclaw/app-sdk`.
// The built SDK stub re-exports from `window.__personalclaw_modules['@personalclaw/app-sdk']`;
// THIS module defines that map. It also shares the host's React singleton under
// `react` / `react-dom` so a contributed bundle never double-loads React (hooks
// across two React copies break).
//
// The hooks are backed by the same gateway the rest of web talks to:
//   useAppApi    — fetch scoped to the app's permissions.api allowlist (client-side
//                  enforcement; the gateway re-checks server-side in A5 — defense
//                  in depth). An undeclared path throws before the request.
//   useAppEvents — the shared /api/ws multiplexed socket, filtered to the app's
//                  permissions.events.
//   useTheme     — the resolved light/dark mode, live.
//   useNotify    — surface a toast (CustomEvent the shell renders).
//   useNavBadge  — set/clear a count badge on the app's nav tile.
//   useChatLauncher — open a PClaw chat session (A8 ChatEmbed extends this).
//
// Call `installAppSdk()` once at startup (from main.tsx) before any contributed
// page mounts.

import * as React from 'react'
import * as ReactDOM from 'react-dom'
import * as ReactDOMClient from 'react-dom/client'
import { useEffect, useRef, useState, createContext, useContext, createElement } from 'react'

// ── permission scope carried per app (mirrors manifest Permissions) ──
export interface AppPermissions {
  api?: string[]
  events?: string[]
  mcpTools?: string[]
  storage?: boolean
  network?: boolean
  memory?: string
  cron?: boolean
}

export interface AppContext {
  name: string
  permissions: AppPermissions
  /** Host layout bridge (set by AppFrame): contribute right-aligned header
   *  actions + open the standard shared detail panel, without drawing chrome.
   *  Typed loosely here to avoid a UI→SDK import cycle; the concrete shape is
   *  AppHost in pages/apps/AppFrame. */
  host?: {
    setHeaderActions: (actions: Array<{ id: string; label: string; icon?: string; variant?: 'primary' | 'secondary' | 'ghost'; onClick: () => void }>) => void
    openPanel: (spec: { title: string; icon?: string; render: (el: HTMLElement) => void | (() => void) }) => void
    closePanel: () => void
  }
}

const AppCtx = createContext<AppContext>({ name: '', permissions: {} })

/** Wraps a contributed app's mounted page so its hooks know which app they
 *  belong to (name + permission scope). The host mounts this around an app's
 *  page in A7. */
export function AppApiProvider({ app, children }: { app: AppContext; children: React.ReactNode }) {
  return <AppCtx.Provider value={app}>{children}</AppCtx.Provider>
}

// ── permission matching (mirrors apps/permissions.py _matches_any) ──
function matchesAny(value: string, patterns: string[] | undefined): boolean {
  if (!patterns) return false
  for (const pat of patterns) {
    if (pat === '*') return true
    if (pat.endsWith('*')) { if (value.startsWith(pat.slice(0, -1))) return true }
    else if (value === pat || value.startsWith(pat.replace(/\/$/, '') + '/')) return true
  }
  return false
}

export class AppPermissionError extends Error {
  constructor(message: string) { super(message); this.name = 'AppPermissionError' }
}

const SK = { 'X-Session-Key': 'dashboard:ui' }

// ── per-app identity token (untrusted-app sandbox, P1) ──
// Every app request carries a short-lived app-scoped token (minted from
// /api/apps/{name}/token) as `Authorization: Bearer`, layered on the owner cookie
// the browser attaches automatically. The gateway reads its `app` claim to scope
// the request to this app's declared permissions. Cached per app; re-minted lazily
// after expiry (or on a 401). Failure to mint is non-fatal — the owner cookie still
// authenticates; the request simply isn't app-scoped (fails closed at the gateway
// for anything outside the owner's own reach).
const _appTokens = new Map<string, { token: string; exp: number }>()

async function appToken(appName: string): Promise<string> {
  const cached = _appTokens.get(appName)
  const now = Date.now() / 1000
  if (cached && cached.exp - 30 > now) return cached.token
  try {
    const r = await fetch(`/api/apps/${encodeURIComponent(appName)}/token`, { method: 'POST', headers: { ...SK } })
    if (!r.ok) return ''
    const { token, expires_in } = await r.json()
    _appTokens.set(appName, { token, exp: now + (Number(expires_in) || 3600) })
    return token || ''
  } catch { return '' }
}

async function appAuthHeaders(appName: string): Promise<Record<string, string>> {
  const t = await appToken(appName)
  return t ? { ...SK, Authorization: `Bearer ${t}` } : { ...SK }
}

export interface AppApiClient {
  backendBase: string
  get: <T>(path: string) => Promise<T>
  post: <T>(path: string, body?: unknown) => Promise<T>
  put: <T>(path: string, body?: unknown) => Promise<T>
  patch: <T>(path: string, body?: unknown) => Promise<T>
  del: <T>(path: string) => Promise<T>
  can: (path: string) => boolean
}

/** IMPERATIVE API client scoped to the app's declared `permissions.api` —
 *  usable from ANYWHERE (event handlers, imperative `mount(el, ctx)` apps), no
 *  React render context needed. This is the primitive; pass the app identity
 *  (the `ctx` your mount function receives). An app's own backend
 *  (`/apps/<name>/api/*`) is always reachable; any other path must be declared
 *  or the call throws (and the gateway rejects it too — A5). */
export function createAppApi(app: AppContext): AppApiClient {
  function allowed(path: string): boolean {
    // Match on the pathname only — the server-side check (permissions middleware)
    // sees request.path with no query string, so `/api/tasks?limit=100` must match
    // a declared `/api/tasks` here too (it reaches the gateway either way; the
    // client check must not be STRICTER than the server's).
    const pathname = path.split(/[?#]/, 1)[0]
    if (pathname.startsWith(`/apps/${app.name}/api`)) return true
    return matchesAny(pathname, app.permissions.api)
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    if (!allowed(path)) {
      throw new AppPermissionError(
        `app "${app.name}" is not permitted to access ${path} — declare it in permissions.api`,
      )
    }
    const init: RequestInit = { method, headers: await appAuthHeaders(app.name) }
    if (body !== undefined) {
      init.headers = { ...init.headers, 'Content-Type': 'application/json' }
      init.body = JSON.stringify(body)
    }
    const r = await fetch(path, init)
    if (!r.ok) {
      const text = await r.text().catch(() => '')
      let msg = text || `HTTP ${r.status}`
      try { const p = JSON.parse(text); if (p?.error) msg = p.error } catch { /* not JSON */ }
      throw new Error(msg)
    }
    const ct = r.headers.get('Content-Type') || ''
    return (ct.includes('application/json') ? await r.json() : await r.text()) as T
  }

  return {
    backendBase: `/apps/${app.name}/api`,
    get: <T,>(path: string) => request<T>('GET', path),
    post: <T,>(path: string, body?: unknown) => request<T>('POST', path, body),
    put: <T,>(path: string, body?: unknown) => request<T>('PUT', path, body),
    patch: <T,>(path: string, body?: unknown) => request<T>('PATCH', path, body),
    del: <T,>(path: string) => request<T>('DELETE', path),
    can: allowed,
  }
}

/** React-hook convenience over {@link createAppApi} for component-based apps —
 *  reads app identity from context, so call it during render. Imperative
 *  `mount(el, ctx)` apps should call `createAppApi(ctx)` directly instead. */
export function useAppApi(): AppApiClient {
  return createAppApi(useContext(AppCtx))
}

export interface AgentTaskResult {
  id: string
  done: boolean
  result?: string
  error?: string
  turns?: number
  elapsed?: number
}

export interface AgentTaskClient {
  start: (task: string, opts?: { agent?: string; maxTurns?: number }) => Promise<string>
  poll: (id: string) => Promise<AgentTaskResult>
  run: (task: string, opts?: { agent?: string; maxTurns?: number; signal?: AbortSignal }) => Promise<AgentTaskResult>
}

/** IMPERATIVE background-agent client — usable from ANYWHERE (event handlers,
 *  imperative `mount(el, ctx)` apps, async callbacks), no React render context
 *  required. This is the primitive; pass the app name (e.g. from the `ctx` your
 *  mount function receives). Requires the app's `agent` permission.
 *  `run(task)` starts a headless agent and polls to completion. Use ChatEmbed
 *  instead when you want to show the user a live chat UI. */
export function createAgentTask(appName: string): AgentTaskClient {
  async function start(task: string, opts?: { agent?: string; maxTurns?: number }): Promise<string> {
    const r = await fetch(`/api/apps/${encodeURIComponent(appName)}/agent-run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await appAuthHeaders(appName)) },
      body: JSON.stringify({ task, agent: opts?.agent, max_turns: opts?.maxTurns }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error || `HTTP ${r.status}`)
    return (await r.json()).id as string
  }

  async function poll(id: string): Promise<AgentTaskResult> {
    const r = await fetch(`/api/apps/${encodeURIComponent(appName)}/agent-run/${encodeURIComponent(id)}`, { headers: await appAuthHeaders(appName) })
    if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error || `HTTP ${r.status}`)
    return await r.json() as AgentTaskResult
  }

  async function run(task: string, opts?: { agent?: string; maxTurns?: number; signal?: AbortSignal }): Promise<AgentTaskResult> {
    const id = await start(task, opts)
    for (;;) {
      if (opts?.signal?.aborted) throw new Error('aborted')
      const res = await poll(id)
      if (res.done) return res
      await new Promise((r) => setTimeout(r, 1500))
    }
  }

  return { start, poll, run }
}

/** React-hook convenience over {@link createAgentTask} for component-based apps:
 *  reads the app identity from context, so it must be called during render (like
 *  any hook). Imperative `mount(el, ctx)` apps should call
 *  `createAgentTask(ctx.name)` directly instead — it works outside React. */
export function useAgentTask(): AgentTaskClient {
  const app = useContext(AppCtx)
  return createAgentTask(app.name)
}

interface WsEnvelope { type: string; data: Record<string, unknown> }

/** IMPERATIVE event subscription — usable from anywhere (imperative
 *  `mount(el, ctx)` apps included). Opens the gateway WS, delivers only events
 *  matching the app's declared `permissions.events`, reconnects with backoff.
 *  Returns an unsubscribe function (call it from your app's teardown). */
export function createAppEvents(app: AppContext, onEvent: (e: WsEnvelope) => void): () => void {
  const events = app.permissions.events
  let ws: WebSocket | null = null
  let closed = false
  let timer: number | undefined
  let retry = 0
  const connect = async () => {
    if (closed) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    // Attach the app-scoped token via query param — the WS handshake can't set an
    // Authorization header. The gateway scopes this connection's event stream to
    // the app's permissions.events (server-side filter in broadcast_ws).
    const t = await appToken(app.name)
    if (closed) return
    const qs = t ? `?app_token=${encodeURIComponent(t)}` : ''
    ws = new WebSocket(`${proto}://${location.host}/api/ws${qs}`)
    ws.onmessage = (ev) => {
      try {
        const env = JSON.parse(ev.data) as WsEnvelope
        if (matchesAny(env.type, events)) onEvent(env)
      } catch { /* ignore non-JSON */ }
    }
    ws.onclose = () => {
      if (closed) return
      retry = Math.min(retry + 1, 6)
      timer = window.setTimeout(connect, 250 * 2 ** retry)
    }
  }
  void connect()
  return () => { closed = true; if (timer) clearTimeout(timer); ws?.close() }
}

/** React-hook convenience over {@link createAppEvents} for component-based apps.
 *  Imperative apps call `createAppEvents(ctx, onEvent)` and keep the returned
 *  unsubscribe to call on teardown. */
export function useAppEvents(onEvent: (e: WsEnvelope) => void) {
  const app = useContext(AppCtx)
  const cb = useRef(onEvent); cb.current = onEvent
  const events = app.permissions.events
  useEffect(() => createAppEvents(app, (e) => cb.current(e)), [events])  // eslint-disable-line react-hooks/exhaustive-deps
}

/** The stable, theme-aware color contract an app may rely on. Names are
 *  vendor-neutral and resolve to the host's real tokens (which flip in light
 *  mode), so an app NEVER guesses an internal CSS variable name. Spread
 *  `cssVars` onto a wrapper element and reference `var(--app-…)` in your styles,
 *  or read the concrete hex values from `colors`. */
export interface AppTheme {
  mode: 'dark' | 'light'
  colors: {
    canvas: string; surface: string; surfaceHigh: string
    onSurface: string; onSurfaceLow: string; border: string
    primary: string; onPrimary: string
    ok: string; warn: string; danger: string
  }
  /** Ready-to-spread CSS custom properties: {'--app-surface': '#…', …}. */
  cssVars: Record<string, string>
}

// Map of app-facing color name → the host token it resolves to. The host token
// flips automatically in light mode, so apps inherit theme correctness for free.
const _APP_COLOR_TOKENS: Record<string, string> = {
  canvas: '--color-canvas',
  surface: '--color-surface',
  surfaceHigh: '--color-surface-high',
  onSurface: '--color-on-surface',
  onSurfaceLow: '--color-on-surface-low',
  border: '--color-outline-variant',
  primary: '--color-primary',
  onPrimary: '--color-on-primary',
  ok: '--color-ok',
  warn: '--color-warn',
  danger: '--color-danger',
}

function _readAppTheme(): AppTheme {
  const cs = getComputedStyle(document.documentElement)
  const mode: 'dark' | 'light' = document.documentElement.classList.contains('light') ? 'light' : 'dark'
  const colors = {} as AppTheme['colors']
  const cssVars: Record<string, string> = {}
  for (const [name, token] of Object.entries(_APP_COLOR_TOKENS)) {
    const val = cs.getPropertyValue(token).trim() || ''
    ;(colors as Record<string, string>)[name] = val
    cssVars[`--app-${name.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase())}`] = `var(${token})`
  }
  return { mode, colors, cssVars }
}

/** IMPERATIVE one-shot read of the host's resolved theme (mode + colors +
 *  cssVars). Callable from anywhere — imperative apps that re-paint on theme
 *  change pair this with a MutationObserver on <html> class. */
export function readAppTheme(): AppTheme {
  return _readAppTheme()
}

/** The host's resolved theme, live. Returns the mode PLUS a stable color
 *  contract (`colors` hex values + `cssVars` to spread) so contributed pages are
 *  theme-correct in both light and dark without guessing host internals.
 *  Re-reads whenever the host flips the `.light` class on <html>. */
export function useTheme(): AppTheme {
  const [theme, setTheme] = useState<AppTheme>(_readAppTheme)
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(_readAppTheme()))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return theme
}

/** IMPERATIVE: surface a toast. Callable from anywhere (no context needed). */
export function notify(message: string, level: 'info' | 'success' | 'error' = 'info'): void {
  try { window.dispatchEvent(new CustomEvent('ne:toast', { detail: { message, level } })) }
  catch { /* SSR guard */ }
}

/** Surface a toast from a contributed app. Dispatches `ne:toast`; the shell
 *  renders it (ShellCorners listens). */
export function useNotify() {
  return notify
}

/** IMPERATIVE: set or clear a count badge on an app's nav tile. Callable from
 *  anywhere; pass the app name (e.g. ctx.name). */
export function setNavBadge(appName: string, count: number | null): void {
  try {
    window.dispatchEvent(new CustomEvent('ne:nav-badge', { detail: { app: appName, count } }))
  } catch { /* SSR guard */ }
}

/** React-hook convenience over {@link setNavBadge} — reads the app name from
 *  context. Imperative apps call `setNavBadge(ctx.name, n)` directly. */
export function useNavBadge() {
  const app = useContext(AppCtx)
  return (count: number | null) => setNavBadge(app.name, count)
}

/** IMPERATIVE: open a PClaw chat session (navigates the host to a full chat
 *  view). Callable from anywhere. For an in-place embed, use {@link ChatEmbed}. */
export function launchChat(opts?: { agent?: string; prompt?: string; session?: string }): void {
  try {
    window.dispatchEvent(new CustomEvent('ne:launch-chat', { detail: opts ?? {} }))
  } catch { /* SSR guard */ }
}

/** Open a PClaw chat session from a contributed app — navigates the host to a
 *  full chat view. For an in-place embed, use {@link ChatEmbed}. */
export function useChatLauncher() {
  return launchChat
}

/** Embed a live PClaw chat session inside a contributed app's own UI (A8).
 *  Renders the host chat route in a sandboxed iframe — full isolation, the app
 *  never touches the chat internals. Resumes `session` if given, else starts a
 *  fresh session optionally seeded with `prompt`. Inherits the host theme via
 *  the same-origin route. */
export function ChatEmbed(props: {
  session?: string
  prompt?: string
  agent?: string
  className?: string
  style?: React.CSSProperties
}) {
  const { session, prompt, agent, className, style } = props
  const qs = new URLSearchParams()
  if (prompt) qs.set('seed', prompt)
  if (agent) qs.set('agent', agent)
  qs.set('embed', '1')  // render just the chat surface, not the whole app shell
  const q = qs.toString()
  const base = session ? `chat/${encodeURIComponent(session)}` : 'chat/new'
  const src = `${location.origin}/#/${base}?${q}`
  return createElement('iframe', {
    src,
    title: 'PersonalClaw chat',
    className,
    style: { border: 'none', width: '100%', height: '100%', ...style },
    sandbox: 'allow-scripts allow-same-origin allow-forms',
  })
}

let installed = false

/** Define `window.__personalclaw_modules` so a contributed ESM bundle resolves
 *  `@personalclaw/app-sdk` (and shares the host React). Idempotent. */
export function installAppSdk(): void {
  if (installed) return
  installed = true
  const w = window as unknown as { __personalclaw_modules?: Record<string, unknown> }
  w.__personalclaw_modules = {
    ...(w.__personalclaw_modules || {}),
    'react': React,
    'react-dom': ReactDOM,
    // react-dom/client (createRoot) — contributed apps that own their render tree (e.g.
    // the Minutes app's imperative mount) import from here, so the host must expose it.
    'react-dom/client': ReactDOMClient,
    '@personalclaw/app-sdk': {
      AppApiProvider,
      AppPermissionError,
      useAppApi,
      useAppEvents,
      useTheme,
      useNotify,
      useNavBadge,
      setNavBadge,
      useChatLauncher,
      launchChat,
      useAgentTask,
      createAgentTask,
      createAppApi,
      createAppEvents,
      notify,
      readAppTheme,
      ChatEmbed,
    },
  }
}

/** Build a blob-URL ESM module that re-exports the host-provided module *spec* (off
 *  `window.__personalclaw_modules`) as named + default exports. This is how a contributed
 *  bundle's bare specifiers (`react`, `@personalclaw/app-sdk`, …) are resolved: the app
 *  loader rewrites each bare import to one of these blob URLs (see loadContributedModule),
 *  so every app shares the host's single React + the real SDK with nothing bundled in.
 *  A document-level import map can't be used — it's added too late (after the host's own
 *  module scripts have loaded), so per-bundle rewriting is the timing-independent path. */
const _shimUrls = new Map<string, string>()

export function appModuleShimUrl(spec: string): string | null {
  const w = window as unknown as { __personalclaw_modules?: Record<string, unknown> }
  const mod = w.__personalclaw_modules?.[spec] as Record<string, unknown> | undefined
  if (!mod) return null
  const cached = _shimUrls.get(spec)
  if (cached) return cached
  const keys = Object.keys(mod).filter((k) => /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(k) && k !== 'default')
  const named = keys.map((k) => `export const ${k} = __m[${JSON.stringify(k)}];`).join('\n')
  const code = `const __m = window.__personalclaw_modules[${JSON.stringify(spec)}];\n${named}\nexport default (__m.default !== undefined ? __m.default : __m);`
  const url = URL.createObjectURL(new Blob([code], { type: 'text/javascript' }))
  _shimUrls.set(spec, url)
  return url
}

/** Load a contributed app's ESM bundle from *src*, resolving its bare import specifiers
 *  (react / react-dom / react-dom/client / @personalclaw/app-sdk / lucide-react) to
 *  host-provided blob shims by rewriting the bundle text, then importing the rewrite.
 *  Native `import(src)` alone can't resolve those bare specifiers (no import map), so we
 *  fetch → rewrite → blob-import. Falls back to a direct import if the fetch/rewrite
 *  fails (e.g. a bundle that already uses relative specifiers). */
export async function loadContributedModule(src: string): Promise<Record<string, unknown>> {
  let text: string
  try {
    const r = await fetch(src)
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    text = await r.text()
  } catch {
    return import(/* @vite-ignore */ src) as Promise<Record<string, unknown>>
  }
  // Rewrite `from "<spec>"` / `from '<spec>'` for each known bare specifier to its shim URL.
  const specs = ['react-dom/client', 'react-dom', 'react', '@personalclaw/app-sdk', '@personalclaw/app-sdk/ui', 'lucide-react']
  for (const spec of specs) {
    const shim = appModuleShimUrl(spec === '@personalclaw/app-sdk/ui' ? '@personalclaw/app-sdk' : spec)
    if (!shim) continue
    const re = new RegExp(`(from\\s*['"])${spec.replace(/[/\\.]/g, '\\$&')}(['"])`, 'g')
    text = text.replace(re, `$1${shim}$2`)
  }
  const blobUrl = URL.createObjectURL(new Blob([text], { type: 'text/javascript' }))
  try {
    return (await import(/* @vite-ignore */ blobUrl)) as Record<string, unknown>
  } finally {
    URL.revokeObjectURL(blobUrl)
  }
}
