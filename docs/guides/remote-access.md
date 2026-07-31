# Reaching your dashboard from outside your home network

By default PersonalClaw is a **local** program: it binds to your machine, and you get in with a
token link (`personalclaw token`). That is the safest arrangement, and if you only ever use it at
home you should stop reading here — nothing in this guide makes a local install better.

This guide is for the other case: you are away from home and want to reach your own dashboard
from a laptop or phone.

> **The honest summary.** Exposing this to the internet moves it from "a program on my machine"
> to "a service someone can knock on". Everything below is about making that a deliberate,
> bounded decision rather than an accident. PersonalClaw does **not** terminate TLS itself and
> does not open a port for you — you bring a tunnel.

## The shape of it

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

## Step 1 — set a password

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

## Step 2 — declare the public URL

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

## Step 3 — 2FA (recommended if you are actually on the internet)

```bash
personalclaw auth totp setup     # prints a secret + otpauth:// URI, ONCE
```

Add it to any authenticator app, **verify a code works**, and only then require it (Settings →
Account → *Require a 2FA code*, or `auth.require_totp: true`).

Requiring a code before enrolling a secret would make sign-in impossible; `personalclaw auth
status` warns you about exactly that state, and the local token link remains the way to fix it.

## Step 4 — pairing a phone without typing your password into it

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

## Sessions, and ending them

Sessions survive a gateway restart (they are recorded under `~/.personalclaw/auth/`, mode 0600).
That is deliberate: being logged out by an unattended update, while away from home, is exactly
the situation this feature exists to prevent.

```bash
personalclaw auth revoke --all    # end every session, everywhere
```

Use that if a device is lost. It survives a restart too. Your password and 2FA enrollment are
untouched, so you just sign in again.

Sign-out from the UI revokes **that** session properly — the token dies, not just the cookie.

## Failed attempts

After `auth.lockout_threshold` failures (default 5) from one address, sign-in is refused for
`auth.lockout_window` (default 15m) and returns `Retry-After`. Wrong-code attempts on the pairing
form count too. Every attempt — success, failure, lockout — lands in the security event log
(`personalclaw security events`).

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

**The page loads but nothing updates.** The WebSocket is blocked. Confirm `public_url` matches
the host in your browser's address bar exactly, including port — the CSP is derived from it.

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
