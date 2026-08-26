# Companion apps on your local network

A companion app is a phone or a second computer driving *your* gateway — the same dashboard,
rendered on a different screen. The only hard problem is the first thirty seconds: the other
device has to learn where this machine is, and then be allowed in.

Those are two separate problems, and PersonalClaw keeps them separate:

| Question | Answered by | Optional? |
|---|---|---|
| **Where is the gateway?** | you type the LAN address — or LAN discovery finds it for you | discovery is optional and **off by default** |
| **May this device in?** | the token link, or a single-use pairing code | never optional |

Discovery only ever answers the first question. It grants nothing, it carries no credential,
and turning it off never costs you access — it costs you typing. If you only ever use
PersonalClaw on the machine it runs on, you can stop reading here.

> Reaching your gateway from **outside** your home network is a different guide:
> [Reaching your dashboard from outside your home network](remote-access.md). Nothing here
> exposes anything to the internet.

---

## The path that always works: type the address

This needs no setup beyond letting the gateway listen on your LAN.

1. **Let the gateway off loopback.** By default it binds `127.0.0.1`, which no other device
   can reach. Bind it wider — the token stays on; it is your front door:
   ```bash
   PERSONALCLAW_BIND_HOST=0.0.0.0 personalclaw gateway --port 10000
   ```
2. **Find this machine's address.**
   ```bash
   personalclaw doctor        # prints the LAN/tailnet address it can see
   ```
3. **Mint a signed-in link and open it on the other device.**
   ```bash
   personalclaw token         # prints http://localhost:10000?token=…
   ```
   On the phone, open the same path with the LAN host substituted:
   `http://192.168.1.37:10000?token=…`

That URL is also what a QR code encodes, so scanning and typing are the same mechanism — one
is just kinder to your thumbs.

**Use a fresh token for each device.** A token becomes bound to the first address that uses
it, so a link you already opened on the machine will be refused from the phone
(`IP mismatch`). Run `personalclaw token` again and open *that* link on the phone first.

**A durable session instead of a long URL.** A token link is fine once; for a device you will
use every day, pair it. On the machine:

```bash
personalclaw auth enroll         # prints a single-use 8-character code, valid 5 minutes
```

The code redeems for a session lasting `auth.session_ttl`. It is worth exactly one session and
nothing else — single-use, short-lived, hashed at rest, and at most five outstanding at a time.
That bound is the point: an 8-character string read off one screen and typed into another should
not be usable twice, and cannot be.

> **Known rough edge.** Redeeming that code *from a browser you reached by IP address* is
> currently refused with `CSRF check failed: request origin not allowed` — the gateway's
> allowed-origin set covers its loopback names and its hostname, not its LAN address. Reading
> the dashboard over the LAN works; it is only this state-changing call that is rejected. Until
> that is settled, reach the gateway by hostname (`http://<machine-name>:PORT`) for the redeem
> step, or use the token link.

---

## The convenience: LAN discovery (optional, off by default)

With discovery on, the gateway announces itself on your local network as a standard
mDNS/DNS-SD service — the same mechanism that makes printers and speakers appear by name. A
companion app then lists it instead of asking you to type an address.

It ships **off**. Announcing a service on a network is a posture choice: on your own Wi-Fi it
is unremarkable, in a café or an office it tells every other device on that network that this
machine is here. That should be a decision, not a default.

### Turning it on

**Settings → Companion apps → LAN discovery**, or:

```bash
personalclaw config set companion.discovery_enabled true
personalclaw config set companion.instance_name "Living room Mac"
```

`instance_name` is just the label other devices show. Leave it empty and your machine's
hostname is used. It takes effect immediately — no restart.

### Finding it from the other device

```bash
personalclaw discover
```

```
Found 1 gateway:

  Living room Mac
    url:     http://192.168.1.37:10166
    pairing: required — run `personalclaw auth enroll` on that machine
             for a single-use code, then redeem it from this device.
```

`--json` prints the same thing machine-readably. Any DNS-SD browser sees it too, which is a
useful way to check what your network is actually being told:

```bash
dns-sd -B _personalclaw._tcp             # macOS
avahi-browse -r _personalclaw._tcp       # Linux
```

### Exactly what gets announced

Four fields, and that is the complete list:

| Field | Example | Why it is there |
|---|---|---|
| `name` | `Living room Mac` | the label you chose, so you can tell two gateways apart |
| `port` | `10166` | so a client can build a URL |
| `requires_pairing` | `1` | so a client can say "you'll need a code" before it knocks |
| `schema` | `1` | so a future client knows what it is reading |

Plus the service address, which is how any network service is reachable at all.

**No token, no session, no content — ever.** A discovery record is a broadcast: unauthenticated,
readable by every device on the network, and cached by some of them. So the code that builds
it works from a fixed list of four keys and cannot express a fifth; a test asserts that the
serialized packet contains none of the gateway's credentials. The dashboard shows you the
record verbatim under **Settings → Companion apps** — what you see there is what your network
receives.

---

## What discovery does *not* protect you from

Worth being precise, because "discovery" sounds like a security feature and is not one.

- **It is not access control.** Discovery says *where*; the token or the pairing code decides
  *whether*. Turning discovery on does not make your gateway more reachable — a gateway bound
  to your LAN was already reachable at that address by anything that scanned for it. Turning
  discovery off does not make it less reachable, either.
- **It tells the network you exist.** That is its whole job. On a network you do not control,
  the honest mitigation is to leave it off — which is the default.
- **It is unauthenticated in both directions.** Anything on the network can claim to be a
  PersonalClaw gateway. A client that finds one still has to pair with it, and pairing requires
  a code minted on the real machine — so a lookalike gets you a failed pairing, not a session.
  Check the URL a client offers you before you type a code into it.
- **It does not cross networks.** Multicast stops at your subnet. From outside your home
  network you want [remote access](remote-access.md), not discovery.

---

## When it does not work

Discovery is a convenience layer over a path that always works, so every failure below has
the same fallback: **type the address**.

| What you see | What is happening |
|---|---|
| Settings says *"bound to loopback only"* | The gateway is on `127.0.0.1`, so nothing on your network could reach it, and advertising an unreachable address would be a lie. Nothing is announced. Start the gateway with `PERSONALCLAW_BIND_HOST=0.0.0.0`. |
| Settings says *"no local-network address"* | The machine has no LAN address to publish — usually no network, or an interface that is up but unaddressed. |
| `personalclaw discover` finds nothing | Discovery may be off on the other machine; or the network filters multicast. Guest and corporate Wi-Fi very often do, and client isolation blocks device-to-device traffic entirely. Nothing is broken — type the address. |
| It worked, then stopped after a restart | Give it a few seconds. A stopping gateway withdraws its record immediately, and a starting one re-announces a small burst. |
| The URL loads but a token is refused with `IP mismatch` | That token was already used from another address. Mint a fresh one with `personalclaw token` and open it on this device first. |
| `CSRF check failed: request origin not allowed` while redeeming a code | See the rough edge above — reach the gateway by hostname for that step. |

---

## Writing a companion app

Native wrappers live in their own plans (desktop, mobile); this is the contract they share.

1. **Find a gateway, or accept a typed URL.** Both, always — discovery is never a
   precondition. `personalclaw.companion.discovery.resolve()` is the Python implementation of
   the client half if you are building in-process; over the wire it is a plain DNS-SD browse
   for `_personalclaw._tcp.local.`
2. **Pair once.** Ask the user for the code from `personalclaw auth enroll` and redeem it for
   a durable session. Do not invent a second credential type.
3. **Render the served dashboard.** There is no separate companion API — a companion loads the
   same SPA from the gateway it paired with. Keeping one frontend is why the phone never lags
   behind the desktop by a release.

The rest of this guide is the precise version of that contract — the part a desktop shell and
a phone shell must not answer differently.

---

## The shared client contract

Two wrappers around one product will drift if either of them gets to decide something the
other also decides. So this section decides it once.

It is deliberately small, and for a good reason: almost everything a companion needs is
already settled by the fact that it renders the gateway's own dashboard. The SPA brings its
own routing, its own caching and its own reconnect behaviour. The genuinely new state — the
only state a wrapper owns — is **the list of gateways it has paired with**.

### One client, several gateways

A companion may hold more than one paired gateway. A work brain and a personal brain is the
common case, and they are unrelated machines that merely happen to sit in one app's list.
That list is the **only** sanctioned multi-instance mechanism in PersonalClaw. Nothing is
shared between the gateways in it — see [No hub, ever](#no-hub-ever) below, which is a
standing ruling and not a current limitation.

### The endpoint registry

A client stores exactly one thing: a list of endpoints plus a pointer at the active one.

```jsonc
{
  "active": "home-laptop",               // the id of the endpoint currently loaded
  "endpoints": [
    { "id": "home-laptop",               // stable, client-minted; never sent to a gateway
      "label": "Home laptop",            // what the switcher shows
      "base_url": "http://claw.local:10000",
      "kind": "local",                   // "local" | "remote"
      "device_session_ref": "<nonce>" }  // which session row, on THAT gateway
  ]
}
```

Four notes, each of which someone would otherwise get wrong:

- **`id` is the client's own.** It exists so the shell has a stable key to namespace state
  under, and it survives the user relabelling or re-pairing an endpoint. No gateway ever sees
  it or needs to.
- **`base_url` is an origin to navigate to, not a prefix to prepend.** More on this below —
  it is the single most important thing in this section.
- **`kind`** is `local` for a gateway on your own network and `remote` for one reached through
  the [remote access](remote-access.md) path. It changes nothing about authentication; it
  exists so a shell can label a connection honestly.
- **`label`** defaults to the gateway's own `companion.instance_name` (the same field the
  discovery record publishes, above), falling back to its hostname. Let the gateway name
  itself — a user who renames "Living room Mac" once should not have to rename it again in
  every wrapper.

### Where the registry lives, and why the dashboard cannot hold it

**The registry belongs to the shell, in the shell's own storage, outside any gateway's
origin.** This is not a preference. The served dashboard is structurally incapable of holding
it:

- **The dashboard is per-gateway by construction.** A shell loads the SPA *from* a gateway:
  `desktop/main.js:768` is a bare `wc.loadURL(backendUrl)`, and `backendUrl` is a single value
  resolved from that one gateway's READY line (`desktop/main.js:143`). One shell window is
  looking at one gateway, always.
- **The SPA has no base-URL concept at all.** Its API client speaks root-relative `/api` paths
  on the same origin (`web/src/lib/api.ts:1-3`), and the WebSocket is built from
  `location.host` (`web/src/lib/useChatSocket.ts:32`). There is no variable to re-point. A
  bundle served by gateway A can only ever talk to gateway A.

So a registry kept inside the dashboard would be a registry each gateway held a separate copy
of, listing itself — which is not a registry.

### Namespacing: exactly one place can bleed

The good news first. Because the SPA is origin-bound, **browser origin isolation already
partitions everything it stores.** Two gateways on two origins get two separate `localStorage`
and `sessionStorage` buckets for free — including the `cache:`-prefixed sessionStorage mirror
at `web/src/lib/data/store.ts:50`, and the other 47 non-test files under `web/src` that touch
web storage. A wrapper does **not** need to namespace, wrap, or patch any of that, and should
not try.

The bad news, and the whole point of this subsection: **the shell's own storage is a single
scope spanning all N gateways.** `desktop/main.js` declares no `partition`, so the default
session applies. That single scope is the one and only place two brains can bleed into each
other.

**The rule: everything the shell itself persists is keyed by endpoint `id`.** Window bounds
and zoom, the last route, notification state, badge counts, cached avatars, any wrapper-side
prefs — all of it goes under the endpoint's `id`, never under a global key. The registry
itself (`active` plus the list) is the sole exception, because it is what the ids belong to.

If you find yourself writing a global key in a wrapper, you have found a state-bleed bug
before it shipped.

### Switching gateways

Switching is two steps, in this order:

1. Re-point `active` to the target endpoint's `id`.
2. Load that endpoint's `base_url`. Not a re-configuration — a navigation to a different
   origin.

What the user sees: the switcher shows every paired gateway with the active one marked; they
pick another; the dashboard reloads as that gateway's dashboard, already signed in, because
the device session for it is already held. It should feel like switching accounts in a mail
app, not like reconnecting.

What must **not** happen, in any wrapper:

- **No aggregation.** No merged inbox, no combined search, no unified notification count, no
  "all gateways" view. One gateway is in view at a time.
- **No cross-endpoint carry-over.** Do not hand the target endpoint state read from the
  previous one — that is exactly the bleed the namespacing rule exists to prevent.
- **No re-pairing on switch.** An endpoint already in the list is already paired. If it is not,
  that is a revocation to report, not a pairing flow to re-enter.

### Device sessions are per-gateway and never federate

Each entry's `device_session_ref` names a row in **that gateway's own session store**. There is
no shared identity across gateways, and no gateway knows the others exist.

The consequence a wrapper must get right: **revoking a device session breaks exactly one
entry.** The other endpoints keep working, untouched. A shell that reacts to one endpoint's
`401` by clearing its whole registry has turned one revocation into a full re-pair of every
gateway. Surface it as "this gateway needs pairing again" on that row, and leave the rest
alone.

**Carry a device session as the session cookie — never through the `?token=` query parameter.**
This one is measured, not stylistic:

- The query-param path **binds** the token to the first client IP it sees
  (`bind_token_ip`, `dashboard/token_auth.py:602`, called on first query-param use at
  `dashboard/token_auth.py:1075`) and then denies on mismatch with `IP mismatch`
  (`check_token_ip`, `:607`, enforced at `:1062`).
- Cookie-borne requests skip that check entirely — *"the cookie itself is the credential, and
  IP validation behind a proxy is unreliable"* (`dashboard/token_auth.py:1059-1061`).

A phone changes IP every time it moves between cell and Wi-Fi. A query-param device session
would therefore die on every network change, while a cookie-borne one is untouched. Pairing
completion sets the cookie on the response, and a native shell — Electron or a Capacitor
WebView — holds it exactly like any browser does. There is nothing to implement here beyond
*not* reaching for the query parameter because it was the shape you saw in the token link.

### Reaching a remote gateway over `wss://`

A `remote` endpoint is your own gateway reached through the [remote access](remote-access.md)
tunnel. The tunnel terminates TLS, so the socket is `wss://` rather than `ws://`.

**Nothing is in the middle.** The connection is your client to your gateway and stops there.
There is no relay, no broker, no account, and no cloud tier that sees your traffic — the tunnel
is the owner's own, and the gateway is the only server in the path. That is a property of the
design, not a setting: no first-party code anywhere knows how to forward a companion's request
to a third host.

#### Building the socket URL

Two different jobs, and mixing them up is the common bug:

- **A WebView shell** loads the served dashboard from `base_url`. The SPA then opens its own
  socket *origin-relative* (`${proto}://${location.host}/api/ws`), so the shell must not build a
  URL at all. This is why the rule below says to load `base_url` as an origin and never prepend
  it to an API path.
- **A native client** — one that speaks to the gateway directly instead of hosting the SPA — has
  no document and therefore no `location` to be relative to. It has only the registry row, so it
  must build the URL, and it should use the shared helper rather than its own string work:

```ts
import { endpointSocket, endpointSocketUrl } from './lib/endpoints'

endpointSocketUrl('https://pc.example.com')   // 'wss://pc.example.com/api/ws'
endpointSocketUrl('http://claw.local:10000')  // 'ws://claw.local:10000/api/ws'
endpointSocket(activeEndpoint(registry))      // same, from a registry row
```

The scheme is derived from the endpoint's **own** URL, so a remote row behind TLS gets `wss://`
without anything having to remember that "remote implies TLS". Nothing consults `kind` — that
field says whether your shell spawned the gateway, which is a lifecycle fact, not a transport
one. An unparseable `base_url`, a bare host, or a scheme that is not `http`/`https` returns
`undefined` rather than a guess: show "this endpoint is misconfigured" on that row, because a
guessed scheme dials a socket somewhere you did not intend.

#### How the gateway authenticates it

The device session rides as the **session cookie**, exactly as on the LAN — see the query-param
warning above, which matters more here because a phone changes IP whenever it moves between
cell and Wi-Fi. The URL carries no credential.

The part worth knowing, because it is the one place a native client is treated differently from
a browser: **a native client sends no `Origin` header**, since it has no document origin to
send. The gateway admits an `Origin`-less `/api/ws` upgrade **only** when the session that
authorized it is a paired device session. Concretely:

| Client | `Origin` | Result |
|---|---|---|
| Native shell, paired device session | absent | upgrade proceeds |
| Any client, ordinary browser session | absent | refused |
| Any client, any session | present but not in the allowlist | refused |

Two things follow, and a wrapper author should not try to work around either:

- **The allowed-origin list is unchanged.** Pairing a device does not add an origin, so it buys
  no help presenting one. If your client *does* send an `Origin`, it must be in the list like
  everyone else.
- **Pairing is the opt-in.** There is no config flag to enable this and nothing to turn on. A
  session the owner deliberately paired is what vouches for the connection, which is also why
  revoking that device from Settings → Devices closes it immediately and closes nothing else.

#### Known rough edge: a WebView over the tunnel needs `dashboard.url` too

If your shell loads the dashboard **in a WebView** through the tunnel, the page's origin is your
public URL, and `dashboard.public_url` alone does not admit it: it adds `wss://<host>` to the
Content-Security-Policy but does not add `https://<host>` to the CSRF/WebSocket origin
allowlist. The dashboard then renders and never receives an event, and state-changing requests
return `403`.

Until that is reconciled, set **both** fields to the same public URL:

```jsonc
{ "dashboard": { "url": "https://pc.example.com",
                 "public_url": "https://pc.example.com" } }
```

`dashboard.url` is the field that widens the origin allowlist (and it refuses to do so unless
token auth is active). A native client that sends no `Origin` is unaffected by this and needs
only `public_url`.

### Reconnecting: reuse the contract, do not write one

A wrapper inherits reconnect behaviour by loading the served dashboard, and that behaviour is
already specified in code:

- The socket reconnects with **capped exponential backoff** — `retry` climbs to a ceiling of 6
  and the next attempt is scheduled at `250 * 2 ** retry` ms
  (`web/src/lib/useChatSocket.ts:45-46`).
- The catch-up callback fires **only after a real connection existed**, guarded by `everOpened`
  (`web/src/lib/useChatSocket.ts:36`), so a first-load failure is not reported as a dropped
  connection.
- Degraded UI is whatever the dashboard already renders in that state. It is the same contract
  the rest of the product uses; a companion is not a special case.

**Do not add a wrapper-side retry loop.** A second timer racing the SPA's own produces
duplicated catch-up fetches and a connection indicator that disagrees with the page. A shell's
legitimate job here is narrower: notice that the machine is unreachable at all (DNS or TCP
failure against `base_url`), and say so on that endpoint's row in the switcher.

#### If you open the socket yourself: two things a dying tunnel does

A shell that hosts the SPA inherits all of the above and can stop reading here. A shell that
opens the socket itself has no SPA to inherit from, and two behaviours were measured against a
real TLS tunnel being killed under a live session
(`tests/test_ca7_wss_tunnel_e2e.py`):

- **The drop arrives in one of two shapes, and you must handle both.** The read either returns a
  close/error message or it *raises* a connection-reset error — the latter when the client's
  autoping tries to answer a ping on the transport that has just died. Handling only the message
  shape turns an ordinary tunnel restart into an unhandled error. Neither shape is slow; if
  neither arrives, you are looking at the hang this contract exists to prevent, not a quiet link.
- **Your device session survives the drop — keep it.** Reconnecting does not mean re-pairing. The
  session is cookie-borne, and the cookie path deliberately skips IP binding
  (`src/personalclaw/dashboard/token_auth.py:1072` reads `if not from_cookie and not
  check_token_ip(...)`), so the same session still authenticates after a tunnel restart has moved
  your apparent address — which is exactly what happens when a phone changes network. This is the
  concrete reason the guide forbids `?token=` for a companion: that path *is* IP-bound, so a shell
  that authenticates with it will find each reconnect refused with an IP mismatch.

### No hub, ever

This is an owner ruling, quoted from the plan of record rather than summarised, because it is
the rule most likely to be re-litigated by someone adding "just one" convenience:

> **No hub in core, ever. No gateway-to-gateway anything.** Gateways never discover, sync
> with, or proxy for each other; no shared identity, no cross-gateway search, no aggregated
> inbox in core or in the shells. A future "hub" could only ever be a third-party app running
> against gateways the user pairs it with — explicitly out of every first-party plan's scope.

Read it as a design boundary, not a missing feature. The N gateways in a client's registry are
N independent machines; the client is the only thing that knows they are related, and it knows
it only as a list. Multi-gateway support is a *client* affordance from end to end, which is
why it costs the gateway nothing.

### What a wrapper must implement

A desktop or mobile author can work from this list without deciding anything else:

1. **Persist the registry** — `{active, endpoints[]}` in the shell's own storage, in the shape
   above.
2. **Add an endpoint** by discovery or a typed URL, then pair once for a device session
   (cookie-borne). Store the returned reference as `device_session_ref`.
3. **Key every shell-side value by endpoint `id`.** No global keys except the registry itself.
4. **Load `base_url` as an origin.** Never prepend it to an API path; there is nothing in the
   SPA to prepend it to.
5. **Ship a switcher** that lists all endpoints, marks the active one, and on selection
   re-points `active` and navigates. No aggregate view.
6. **Show per-endpoint health** — reachable, unreachable, or needs-pairing-again — on the
   switcher rows. One endpoint's failure never touches another's entry.
7. **Inherit reconnect from the SPA.** Add no retry loop of your own. If you open the socket
   yourself rather than hosting the SPA, build its URL with `endpointSocketUrl` — never by
   string-concatenating a scheme.
8. **Label endpoints from `companion.instance_name`**, falling back to the hostname, and let
   the user override locally.

If a wrapper needs something not on this list, that is a change to this document first.
