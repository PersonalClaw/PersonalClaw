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
