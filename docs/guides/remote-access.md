# Reaching your dashboard from outside your home network

By default PersonalClaw is a **local** program: it binds to your machine, and you get in with a
token link (`personalclaw token`). That is the safest arrangement, and if you only ever use it at
home you should stop reading here — nothing in this guide makes a local install better.

This guide is for the other case: you are away from home — on your phone, on cell data — and want
to reach your own dashboard. There are two shapes of answer, and **you almost certainly want the
first one:**

1. **A private network (a tailnet).** Your phone and your gateway join the same encrypted overlay
   network and talk directly. Nothing is exposed to the public internet; there is no TLS
   certificate to obtain and no port to open. This is the recommended path for personal use.
2. **A public tunnel.** You deliberately publish the dashboard to the internet behind TLS and a
   password. More moving parts, a larger attack surface — the right choice only when you cannot
   put both devices on a tailnet.

---

## The simplest path: a tailnet (recommended)

A tailnet (Tailscale is the common one) is a private, end-to-end-encrypted network your devices
join. Once your phone and your gateway are both on it, the phone reaches the gateway at a stable
`100.x.y.z` address **as if they were on the same LAN** — over the tailnet's own encryption, with
nothing published to the public internet.

This works **today**, unchanged, with the default `AUTH_MODE=local_token`. You do not set
`dashboard.public_url`, you do not obtain a TLS certificate, and you do not port-forward anything.

```
your phone ──(tailnet, encrypted)──▶ your gateway at 100.x.y.z:PORT
   both devices are members of the same tailnet; no public exposure
```

**Steps**

1. **Put the gateway on the tailnet.** Install Tailscale on the machine running PersonalClaw and
   bring it up:
   ```bash
   tailscale up
   tailscale ip -4          # prints this machine's tailnet address, e.g. 100.101.102.103
   ```
2. **Put your phone on the same tailnet.** Install the Tailscale app, sign in to the same account,
   and confirm both devices appear in your device list.
3. **Let the gateway listen on the tailnet interface.** By default the gateway binds loopback
   only. Bind it so the tailnet can reach it (token auth stays on — the token is the front door):
   ```bash
   PERSONALCLAW_BIND_HOST=0.0.0.0 personalclaw gateway --port 10000
   ```
4. **Open it on your phone with the signed-in link.** On the machine, mint a token URL and rewrite
   the host to the tailnet address:
   ```bash
   personalclaw token          # prints http://localhost:10000?token=…
   ```
   On the phone, open `http://100.101.102.103:10000?token=…` (same path and token, tailnet host).

**`personalclaw doctor` does this detection for you.** It now finds the tailnet interface and
prints the phone-usable base URL directly:

```
Remote access
  remote:      ✅ tailnet 100.101.102.103 — open http://100.101.102.103:10000 on your phone
               (run: personalclaw token, for the signed-in link)
```

If instead it says `local-only`, your gateway isn't on a tailnet yet — walk back through the steps
above. If it says the bind is **exposed beyond loopback with auth OFF**, fix that first (see the
anti-patterns below) before going any further.

**Why this is the safe default.** The tailnet is already a private encrypted network, so the two
risks the public-tunnel path spends effort mitigating — cleartext on the wire, and a stranger
finding your URL — simply do not arise. There is no public listener to find. The token link is
still your authentication, and everything under *What this does not protect you from* still
applies.

---

## The other path: expose to the internet (public tunnel)

Use this **only** when you cannot put both devices on a tailnet. It publishes the dashboard to the
public internet, so it needs TLS, a password, and the hardening below.

> **The honest summary.** Exposing this to the internet moves it from "a program on my machine"
> to "a service someone can knock on". Everything below is about making that a deliberate,
> bounded decision rather than an accident. PersonalClaw does **not** terminate TLS itself and
> does not open a port for you — you bring a tunnel. (Tailscale can also do this via *Tailscale
> Serve/Funnel* — but if you're using Tailscale, prefer the private-tailnet path above.)

### The shape of it

```
your phone ──https──▶ your tunnel (terminates TLS) ──http──▶ PersonalClaw on 127.0.0.1
                      cloudflared / Tailscale / Traefik
```

Three things you provide, in order:

1. **A tunnel that terminates TLS.** cloudflared, Tailscale Serve, Traefik, Caddy, nginx — any
   of them. Plain http across the internet would put your session cookie on the wire in
   cleartext, so TLS is a precondition, not a nice-to-have.
2. **A password**, so a stranger who finds the URL cannot walk in.
3. **`dashboard.public_url`**, which is how you tell PersonalClaw it is exposed. That single
   setting is what turns on the hardening described below.

### Step 1 — set a password

```bash
personalclaw auth set-password            # prompts twice, never echoes
personalclaw auth enable                  # start offering the sign-in page
personalclaw auth status                  # confirm both
```

Minimum 12 characters. Length beats symbols — a passphrase you can remember is better than a
short string you keep in a note app. The password is stored as an argon2id hash under
`~/.personalclaw/auth/credentials.json` (mode 0600) and never leaves your machine.

**Your token link keeps working.** Sign-in is an *additional* front door, never a replacement.
If you forget the password, walk to the machine and run `personalclaw token`.

### Step 2 — declare the public URL

In `~/.personalclaw/config.json`:

```json
{
  "dashboard": {
    "public_url": "https://pc.example.com",
    "trusted_proxies": ["127.0.0.1"]
  }
}
```

Setting `public_url` changes three things:

| | Effect | Why |
|---|---|---|
| Session cookie | gains `Secure` | so the browser refuses to send it over plain http |
| WebSocket CSP | allows `wss://<your-host>` | otherwise the dashboard loads and then silently receives nothing |
| `X-Forwarded-*` | honored **only** from `trusted_proxies` | see below |

`trusted_proxies` is the address your tunnel connects **from**, as PersonalClaw sees it — usually
`127.0.0.1` for a local tunnel daemon, or the container/bridge address in Docker (e.g.
`172.18.0.0/16`). Single addresses and CIDR blocks both work.

**Why this list matters.** Your tunnel tells PersonalClaw the real client address via a header;
without it every request looks like it came from the tunnel. But any process that can reach the
gateway could *claim* to be your tunnel and set that header to anything — which would let it move
a session's IP binding. So on an exposed instance the header is believed **only** from a peer you
named here. Leave it empty and no forwarded header is trusted at all (safe, but sessions bind to
the tunnel's address rather than the real client's).

`public_url` is deliberately **not** editable from the Settings UI — widening a network surface
should be a deliberate file edit, not a click.

### Step 3 — 2FA (recommended if you are actually on the internet)

```bash
personalclaw auth totp setup     # prints a secret + otpauth:// URI, ONCE
```

Add it to any authenticator app, **verify a code works**, and only then require it (Settings →
Account → *Require a 2FA code*, or `auth.require_totp: true`).

Requiring a code before enrolling a secret would make sign-in impossible; `personalclaw auth
status` warns you about exactly that state, and the local token link remains the way to fix it.

### Step 4 — pairing a phone without typing your password into it

Typing a long passphrase into a phone keyboard, over a tunnel, in public, is the worst place to
spend your password. Instead:

```bash
personalclaw auth enroll          # prints e.g.  7K4M-QP93
```

On the phone, open your public URL, tap **Use a device code instead**, and enter it. The code:

- works **once**;
- expires in **5 minutes**;
- is stored only as a hash, so nobody can read it back off the disk;
- mints an ordinary session with the same lifetime as a password sign-in.

Losing a code costs nothing — run the command again. `personalclaw auth enroll --clear`
invalidates any outstanding ones.

### Sessions, and ending them

Sessions survive a gateway restart (they are recorded under `~/.personalclaw/auth/`, mode 0600).
That is deliberate: being logged out by an unattended update, while away from home, is exactly
the situation this feature exists to prevent.

```bash
personalclaw auth revoke --all    # end every session, everywhere
```

Use that if a device is lost. It survives a restart too. Your password and 2FA enrollment are
untouched, so you just sign in again.

Sign-out from the UI revokes **that** session properly — the token dies, not just the cookie.

### Failed attempts

After `auth.lockout_threshold` failures (default 5) from one address, sign-in is refused for
`auth.lockout_window` (default 15m) and returns `Retry-After`. Wrong-code attempts on the pairing
form count too. Every attempt — success, failure, lockout — lands in the security event log
(`personalclaw security events`).

## Anti-patterns — do not do these

- **Never port-forward your router straight to the gateway.** Opening a port on your home router
  to `PORT` on the machine puts an unencrypted `http://` listener on the public internet with no
  TLS and no tunnel in front of it. That is the worst of both paths: cleartext cookies on the wire
  *and* a public address anyone can scan. Use a tailnet (encrypted, private) or a TLS-terminating
  tunnel — never a bare forwarded port.
- **`AUTH_MODE=none` cannot be exposed — and that is a feature, not a limitation.** When auth is
  off, the gateway is *forced* to bind loopback (`127.0.0.1`) no matter what you set; an
  unauthenticated gateway can never reach a non-loopback interface. If you want remote access, keep
  the default `local_token` (the tailnet path) or set a password (the tunnel path). Don't reach for
  `none`.
- **Never expose over plain http.** Publishing an `http://` (not `https://`) URL to the internet
  leaks your session cookie on the wire — see the `Secure`-cookie note under *What this does not
  protect you from*. A tailnet sidesteps this entirely (it is encrypted end to end); a public
  tunnel must terminate TLS.

If `personalclaw doctor` reports `remote: ❌ bound beyond loopback with auth OFF`, you have hit the
first two anti-patterns at once (a `PERSONALCLAW_BIND_HOST` override with `AUTH_MODE=none`). Fix it
before exposing anything: either bind loopback again, or set a password and use one of the two
supported paths.

## What this does not protect you from

Being straight about the limits, because a false sense of safety is worse than none:

- **A weak password.** Rate limiting slows online guessing; it does nothing about a password that
  appears in a breach list. Use a passphrase.
- **A compromised device.** A session cookie on a stolen unlocked phone is a session. Revoke.
- **Your tunnel provider.** Anything terminating your TLS can see your traffic. That is inherent
  to the arrangement, not specific to PersonalClaw.
- **The agent's own reach.** Sign-in controls who reaches the *dashboard*. What the agent may do
  once inside is governed separately — see [SECURITY.md](../../SECURITY.md), the approval modes,
  and the autonomy guardrails.
- **Plain-http exposure.** If you set an `http://` public URL, the cookie will not carry `Secure`
  (setting it would break sign-in entirely) and your session is exposed on the wire. Don't.

## Troubleshooting

**My phone can't reach the tailnet address.** Confirm both devices are up on the *same* tailnet
(`tailscale status` on the machine lists your phone), that the gateway was started with
`PERSONALCLAW_BIND_HOST=0.0.0.0` (it binds loopback-only by default), and that `personalclaw
doctor` prints `remote: ✅ tailnet …`. If doctor says `local-only`, the gateway isn't on the
tailnet yet.

**The page loads but nothing updates.** The WebSocket is blocked, and there are two separate
reasons it can be — check both:

1. Confirm `public_url` matches the host in your browser's address bar exactly, including port —
   the CSP is derived from it.
2. **Also set `dashboard.url` to the same public URL.** `public_url` puts `wss://<host>` in the
   Content-Security-Policy, so the browser is *allowed* to open the socket, but it is
   `dashboard.url` that adds your public origin to the origin allowlist the gateway checks on the
   upgrade. With only `public_url` set the browser opens the socket and the gateway refuses it,
   which looks identical to a CSP problem. State-changing requests return `403` for the same
   reason.

(Both apply to the public-tunnel path; the tailnet path needs no `public_url`.)

**I get signed out immediately, or the cookie never appears.** An `https` `public_url` sets
`Secure`, so the cookie is dropped over plain http. Either serve the dashboard over TLS or unset
`public_url`.

**Everything says "not enabled".** `personalclaw auth status`. Enabling sign-in requires a
password first, by design.

**I am locked out and away from home.** Wait out the window (default 15m). If you are truly
stuck, you need local access — that is the intended floor: nothing remote can override the
lockout.

## Related

- [containers.md](containers.md) — Docker/compose, including the unattended
  `PERSONALCLAW_LOGIN_USER` / `PERSONALCLAW_LOGIN_PASSWORD` seed.
- [SECURITY.md](../../SECURITY.md) — threat model and reporting.
