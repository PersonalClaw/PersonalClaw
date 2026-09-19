# MCP end-user identity propagation without raw-token passthrough — design note

**Status:** spike outcome, awaiting owner ratification. **No code ships with this note.**

This note answers one question: how does a shared MCP server learn *which end user* a
request is for, when the MCP authorization spec forbids forwarding the end user's raw
token? It exists because the ecosystem prescribes "propagate the end-user identity" and
then hand-waves the mechanism — so the mechanism is what this note pins down, measured
against what PersonalClaw actually ships rather than against an assumed architecture.

Every claim below cites a `file:line` anchor verified against `origin/main` at
`c07f7549a`. Where a shipped document and the code disagree, the code wins and §9 says so.

---

## 0. The answer, in one paragraph

**You do not exchange a token. You never move a credential across the boundary at all.**
The pattern that works for a small team without an enterprise identity provider is an
**out-of-band pairing ceremony** that binds an opaque, channel-scoped sender identifier to
a local trust decision, plus a **separately-issued, locally-minted per-integration bearer**
that is stored only as a hash and revoked individually. What then crosses the boundary is a
*resolved identity* — a verdict — not a token that a downstream service must re-validate.
PersonalClaw already ships both halves of that pattern. It ships them in two modules that
**never touch each other** (§3.1), and it has no end user to propagate in the first place,
because it is single-owner by design (§2.2). So the recommendation is to **adopt the
discipline as written doctrine now and defer the mechanism** (§5) — not to build an
identity-propagation layer for a tenancy that does not exist.

---

## 1. What the constraint actually forbids

The research finding this spike resolves (F11) pairs a vendor reference architecture with
the vendor-neutral MCP authorization specification. The architecture says the shared MCP
server must use a propagated end-user identity to enforce fine-grained access control on
the backend; the specification adds the hard constraint — a server must validate
**audience-bound** tokens, and **passthrough of a raw token is prohibited**. Propagation
must therefore use an exchanged or separately-issued credential.

Three consequences matter for this note, and only the third is about tokens at all:

1. **Audience binding is the real requirement.** A token minted for audience *A* must not
   be accepted by service *B*. This is what makes passthrough unsafe: a forwarded token is
   by construction bound to the wrong audience, so the receiver cannot tell an authorized
   call from a replay of someone else's credential.
2. **The prohibition is on the *credential*, not on the *identity*.** Propagating "this
   request is for user `u`" is required. Propagating "here is user `u`'s bearer" is
   forbidden. Conflating the two is the mistake the prohibition exists to prevent.
3. **Exchange is one way to satisfy it, not the only way.** A separately-issued credential
   satisfies audience binding trivially, because it was minted for exactly one audience and
   never existed anywhere else.

The industrial pattern that satisfies this at scale is well established and worth naming
because it shows the shape rather than a vendor API: a *trusted issuer* mints a
short-lived, signed security context carrying the authenticated subject; intermediate
services propagate that context; the data custodian validates it and authorizes on it. The
end user's original credential never leaves the authentication boundary. Note what the
pattern requires to exist before it can be built: an issuer, a subject, and a validator.
§4.1 shows PersonalClaw has none of the three.

---

## 2. What ships today at the inbound MCP boundary

### 2.1 Two bearers, one of which is an identity

The inbound MCP surface is a POST-only JSON-RPC endpoint. Routes are registered at
`src/personalclaw/inbound/mcp_http.py:535-536`, and the GET handler exists only to refuse
with 405 — there is no SSE transport (`src/personalclaw/inbound/mcp_http.py:133`). The
surface is stateless: it issues no `Mcp-Session-Id`
(`src/personalclaw/inbound/mcp_http.py:53-55`).

`handle_mcp` (`src/personalclaw/inbound/mcp_http.py:144`) runs a fixed admission order —
enablement, peer, token, rate, concurrency, body size, parse — declared in its own module
docstring at `src/personalclaw/inbound/mcp_http.py:12-13`. Two of those steps carry
identity:

**The peer check** reads the *transport* peer address, never a header
(`src/personalclaw/inbound/auth.py:161-176`), and is called at
`src/personalclaw/inbound/mcp_http.py:234`, implemented at
`src/personalclaw/inbound/auth.py:183`. A non-loopback caller additionally needs
`allow_remote` plus an exact `Host` match against the configured public URL
(`src/personalclaw/inbound/auth.py:211-218`). Reading the peer from the socket rather than
from `X-Forwarded-For` is the correct choice and is worth stating: a header-derived peer is
attacker-controlled, so it is an assertion, not an identity.

**The bearer** is read at `src/personalclaw/inbound/mcp_http.py:239-241` and resolved
against two different authorities at `src/personalclaw/inbound/mcp_http.py:247-250`:

- `clients_mod.lookup_by_token(presented, SURFACE)` →
  `src/personalclaw/inbound/clients.py:261`. This resolves the bearer to a **registered
  integration** — an identity.
- `auth.verify_bearer(SURFACE, presented)` →
  `src/personalclaw/inbound/auth.py:151-158`, comparing against the per-surface token whose
  env key is derived at `src/personalclaw/inbound/auth.py:45-52`. This resolves to **the
  surface itself** — not an identity, just a door key.

That second path is the one the module docstring of the client registry warns about: with
only a surface token "every holder of it is the same principal, so revoking one integration
means rotating the credential of every other"
(`src/personalclaw/inbound/clients.py:1-8`). The client registry is the fix, and it is
already the better-behaved half of this boundary:

| property | anchor |
|---|---|
| Token is **locally minted**, returned once, never recoverable | `src/personalclaw/inbound/clients.py:211`, `src/personalclaw/inbound/clients.py:199-203` |
| Only a `sha256` hash is persisted — the registry cannot hand back a credential | `src/personalclaw/inbound/clients.py:60-62`, `src/personalclaw/inbound/clients.py:18-20` |
| Store is `0600` via `atomic_write` | `src/personalclaw/inbound/clients.py:186` |
| Lookup is constant-time and does **not** break on a hit | `src/personalclaw/inbound/clients.py:274-277` |
| Corrupt registry reads as empty — **fail-closed**, no client authenticates | `src/personalclaw/inbound/clients.py:113-126` |
| An empty surface list means *none*, not *all* | `src/personalclaw/inbound/clients.py:98-106` |
| Bindings are pins; a disagreeing argument is a 403, never a substitution | `src/personalclaw/inbound/clients.py:287-318`, enforced at `src/personalclaw/inbound/mcp_http.py:423` |
| The pinned set is data, so a new binding cannot stay un-enforced | `src/personalclaw/inbound/clients.py:50` |
| A binding narrows and never widens | `src/personalclaw/inbound/clients.py:321-329` |
| Revocation is per-integration | `src/personalclaw/inbound/clients.py:232-240` |

The sentence that governs the whole design is at
`src/personalclaw/inbound/clients.py:14-15`: "the binding is the authority, the argument is
an assertion, and an assertion never wins." That is audience binding, enforced
server-side, without a token format.

### 2.2 What the boundary does not carry: an end user

There is no end-user identity anywhere on this path, and this is a design decision rather
than an omission.

- `InboundClient` (`src/personalclaw/inbound/clients.py:65-96`) has fields for
  `client_id`, `label`, `token_hash`, `surfaces`, `agent`, `tools`, `scope`, `upstream` —
  and **no** user, subject, or end-user field.
- `client_id` is a minted opaque hex (`src/personalclaw/inbound/clients.py:213`) used for
  three bookkeeping purposes only: rate-cap keying
  (`src/personalclaw/inbound/mcp_http.py:263`,
  `src/personalclaw/inbound/mcp_http.py:285`), the audit line
  (`src/personalclaw/inbound/audit.py:46`), and fence provenance
  (`src/personalclaw/inbound/framing.py:69-83`). It authorizes nothing by itself.
- Tool execution takes no identity beyond it:
  `src/personalclaw/inbound/tools.py:442` is
  `async def call_tool(name, arguments, state, client_id="")`, and
  `src/personalclaw/inbound/tools.py:449` says the `client_id` "rides through to the fence
  attribution" — i.e. it labels content, it does not scope access.
- The handlers behind those tools read the **single local owner's** stores with no scoping
  argument at all (`src/personalclaw/inbound/tools.py:294-310`).
- Searching the whole inbound package for an end-user concept
  (`user_id`, `end_user`, `on_behalf_of`, `sub`, `entity_id`) returns **zero matches**.

This is consistent with the product's stated posture, in code and in doctrine:

- `src/personalclaw/auth/credentials.py:3-5` — "One owner, one credential set. This is
  **authentication, not multi-tenancy** … there is deliberately no user table, no roles,
  and no signup."
- `src/personalclaw/gateway.py:370` — "Multi-user access is disabled — only owner is
  authorized".
- `docs/security/threat-model.md:161` — "PersonalClaw is a single-owner, self-hosted tool."
- `src/personalclaw/identity.py:11-13` — the username "is an attribution string, not a
  credential. Nothing authenticates against it and nothing authorizes on it."
  (`current_username` is at `src/personalclaw/identity.py:105`.)

One near-miss deserves an explicit warning, because the name invites it. There *is* an
`entity` concept and an `entity_settings/` directory, and **neither is a tenant**. The
`entities` table (`src/personalclaw/knowledge/store.py:449-457`) holds knowledge-graph
nodes — people and things *mentioned in* memory, e.g. the `person:` prefix at
`src/personalclaw/memory_holder.py:40-41`. `entity_settings/` is a settings-file namespace
resolved at `src/personalclaw/providers/entity_routes.py:38-39`. Per-record attribution is
a `belongs_to(username)` / `author` predicate
(`src/personalclaw/inbox.py:329`, `src/personalclaw/tasks/models.py:335`,
contract at `src/personalclaw/testing/shared_store_conformance.py:22-24`). **Reusing
`entity_id` as an MCP end-user identifier would be a category error** and is the single
most likely wrong turn a future contributor takes from this note.

### 2.3 Outbound: there is no passthrough, by construction

The prohibition cuts both ways, and the outbound side is currently clean — worth recording
precisely so a later change cannot quietly regress it.

PersonalClaw acting as an MCP *client* reads server specs from `mcp.json`
(`src/personalclaw/mcp_client.py:594-608`). Credential injection is **environment-only and
stdio-only** (`src/personalclaw/mcp_client.py:307-309`). Remote transports get **no headers
at all**: `streamablehttp_client(url)` and `sse_client(url)` are called with the URL alone
(`src/personalclaw/mcp_client.py:281-293`), and the module contains no `Authorization` or
`Bearer` handling whatsoever. The one env-injecting site on the ACP path passes
non-credential locators only — home, port, session key
(`src/personalclaw/acp/mcp_servers.py:73`, `:88`, `:92`).

So **no code path copies an inbound bearer, a dashboard session token, or a surface token
into an outbound MCP call.** That is the correct posture; §6 turns it into a stated
constraint rather than an accident.

---

## 3. The pairing substrate — the "no enterprise IdP" ceremony

The research question asks what small teams *without* an IdP actually do. PersonalClaw's
answer already exists on the channel side, and it is not a token mechanism.

`channel_trust.py` gates every inbound channel message. Its identity key is a two-part,
channel-scoped pair — a `provider` string plus a `sender_id`
(`src/personalclaw/channel_trust.py:6-9`) — persisted per provider under
`entity_settings/channel_trust.json` (`src/personalclaw/channel_trust.py:54` resolved
through `src/personalclaw/providers/entity_routes.py:38-39`). The gate itself is
`guard_inbound` (`src/personalclaw/channel_trust.py:679`), described at
`src/personalclaw/channel_trust.py:689` as "THE trust gate a transport calls at the top of
its inbound path", reached from exactly one caller
(`src/personalclaw/channel_inbound.py:73`) and re-exported to apps via
`src/personalclaw/sdk/channel.py:44-61`. It returns a `TrustVerdict`
(`src/personalclaw/channel_trust.py:437`).

The ceremony is the interesting part:

- The owner mints an 8-digit code (`src/personalclaw/channel_trust.py:331`;
  `PAIRING_CODE_DIGITS` at `src/personalclaw/channel_trust.py:58`,
  `PAIRING_CODE_TTL_SECS` at `src/personalclaw/channel_trust.py:57`) and hands it over
  **out of band** — see `docs/guides/build-a-channel-app.md:180-183`.
- The sender redeems it in-channel (`src/personalclaw/channel_trust.py:379`). Single-use,
  TTL-bound.
- Until then an unpaired DM sender is **denied** under the default `pairing` policy, with a
  shared canned reply (`src/personalclaw/channel_trust.py:72`, applied at
  `src/personalclaw/channel_trust.py:769`) and exactly one deduped owner attention item
  (`src/personalclaw/channel_trust.py:605`).
- Foreign content is fenced on the way in
  (`src/personalclaw/channel_trust.py:422`).

Read as an identity protocol, this is precisely the answer to the research question. **No
token is exchanged, and no credential crosses the boundary.** An out-of-band secret proves,
once, that a channel-scoped identifier belongs to someone the owner trusts; the binding is
then stored locally and what flows on every subsequent request is a *locally-computed
verdict*. It satisfies the spec's constraint by making the constraint vacuous — there is no
raw token to pass through. It needs no issuer, no JWKS, no clock skew policy, and no
authorization server, which is exactly why it fits a team that has none of those.

### 3.1 The measured gap: the two substrates never touch

This is the finding that decides the recommendation.

`git grep -n channel_trust -- src/personalclaw/inbound` returns **zero matches**. The
pairing/trust substrate is reachable only from the channel ingestion path
(`src/personalclaw/channel_inbound.py:73`); the inbound MCP surface
(`src/personalclaw/inbound/mcp_http.py`) never consults it. PersonalClaw therefore ships
**two unrelated inbound-identity systems**:

| | `inbound/clients.py` | `channel_trust.py` |
|---|---|---|
| principal | a registered **integration** | a **human sender** on a channel |
| key | minted `client_id` (`:213`) | `(provider, sender_id)` (`:6-9`) |
| credential | locally-minted bearer, hash at rest (`:60-62`) | none — an out-of-band one-time code (`:331`, `:379`) |
| establishes trust by | operator registration (`:189`) | pairing ceremony / owner Allow (`:662`) |
| consulted by MCP | **yes** (`src/personalclaw/inbound/mcp_http.py:247`) | **no** (0 matches) |

Neither is wrong. They answer different questions, and no shipped surface needs them
joined. But it means the honest answer to "how would an end-user identity reach the MCP
server" is: **there is no wire for it today**, and building one is the work — not wiring up
a token format.

---

## 4. The evaluation

### 4.1 Token exchange

Token exchange means a client presents a token it holds and receives, from an authorization
server, a *different* token minted for a specific downstream audience and subject. To build
it, three things must exist: an **issuer** that can mint, a **subject** worth minting for,
and a **validator** that authorizes on the result. PersonalClaw has none of them.

- **No issuer.** There is no authorization server. Session tokens are minted by
  `generate_token` (`src/personalclaw/dashboard/token_auth.py:511`), HMAC-signed
  (`src/personalclaw/dashboard/token_auth.py:507-508`) with claims `sub`/`exp`/
  `session_exp`/`iat`/`nonce` (`src/personalclaw/dashboard/token_auth.py:552-560`). That is
  a local session credential for the browser, not an OAuth issuer, and it has no audience
  claim to bind.
- **No subject.** `sub` would have to be an end user. The only username in the system is
  explicitly "an attribution string, not a credential"
  (`src/personalclaw/identity.py:11-13`), and there is deliberately no user table
  (`src/personalclaw/auth/credentials.py:3-5`).
- **No reachable federation to exchange *from*.** A JWT verifier ships at
  `src/personalclaw/auth/oidc.py` and `AuthConfig` declares `oauth2_issuer` /
  `oauth2_client_id` / `oauth2_audience`
  (`src/personalclaw/auth/modes.py:154-156`) — but `OAUTH2` is **unreachable**:
  it is in `UNSELECTABLE_MODES` (`src/personalclaw/auth/modes.py:57`), only `none` and
  `local_token` are selectable (`src/personalclaw/auth/modes.py:46-49`), and
  `AuthConfig.from_env` sets the mode and nothing else
  (`src/personalclaw/auth/modes.py:176`). So today there is no federated identity in the
  process to exchange.
- **No validator that could use the result.** Every inbound tool handler reads the single
  owner's stores with no scoping argument
  (`src/personalclaw/inbound/tools.py:294-310`). A correctly-exchanged, perfectly
  audience-bound token would arrive and change nothing, because nothing downstream
  authorizes per-user.

Building token exchange therefore means building an authorization server, a user table, and
per-user authorization inside core — which is the multi-tenant identity product the owning
plan's guardrail explicitly forbids, and which the threat model's single-owner scope
disclaims. **Disposition: out of scope.** Not "later" — the wrong shape for this product.

### 4.2 Separately-issued tokens

This arm is not a proposal. It is **already what ships**, and it already satisfies the
specification's constraint:

- The credential is minted locally for exactly one audience — this surface, this client
  (`src/personalclaw/inbound/clients.py:211`, `:189-229`). It never existed elsewhere, so
  there is no passthrough to prohibit and audience binding is structural.
- It is stored as a hash (`src/personalclaw/inbound/clients.py:60-62`), compared in
  constant time (`src/personalclaw/inbound/clients.py:274-277`), fails closed
  (`src/personalclaw/inbound/clients.py:113-126`), narrows capability
  (`src/personalclaw/inbound/clients.py:321-329`), refuses argument override
  (`src/personalclaw/inbound/clients.py:287-318`), and revokes individually
  (`src/personalclaw/inbound/clients.py:232-240`).

Its only limitation is the one in §2.2: it identifies an *integration*, not an end user.
For a single-owner product that is not a defect — the integration *is* the owner's agent —
and paying for a per-user layer would buy nothing a user could observe.
**Disposition: adopt, as the standing mechanism, and write the constraint down (§6).**

### 4.3 Scorecard

| criterion | token exchange | separately-issued (shipped) | pairing (shipped, channels) |
|---|---|---|---|
| satisfies the raw-passthrough prohibition | yes | yes, structurally | yes, vacuously — no token crosses |
| needs an IdP / authorization server | **yes** | no | no |
| audience binding | via claim | by construction | n/a — no token |
| per-principal revocation | via issuer | yes (`src/personalclaw/inbound/clients.py:232`) | yes (`src/personalclaw/channel_trust.py:220`) |
| identifies an **end user** | yes | no — an integration | yes, channel-scoped |
| buildable by a small team, no IdP | **no** | already built | already built |
| fits single-owner posture | no | yes | yes |

The composition of columns 2 and 3 — a locally-issued, revocable, capability-narrowed
credential for the *machine*, plus an out-of-band pairing ceremony for the *human* — is the
mechanism the research found the ecosystem prescribing but never specifying. **That is the
answer to open question #3, and PersonalClaw arrived at it independently in two halves.**

---

## 5. Recommendation — owner ratification required

The clause asks for one of adopt / defer / out-of-scope. The honest answer splits, because
the clause bundles two separable questions. Both dispositions are stated so each can be
ratified with one word:

> **R1 — Token exchange as the propagation mechanism: OUT OF SCOPE (permanent).**
> It requires an issuer, a subject and a per-user validator that core deliberately does not
> have (§4.1), and building them is the multi-tenant identity product the plan guardrail
> and the threat model both refuse. Revisit only if PersonalClaw ever becomes multi-tenant,
> which is a product decision, not an architectural one.

> **R2 — An end-user identity wire into the inbound MCP surface: DEFER (no demand, premise
> absent).**
> There is no end user to propagate: single-owner by design
> (`src/personalclaw/auth/credentials.py:3-5`, `docs/security/threat-model.md:161`), and no
> end-user field on the boundary (§2.2). Building the wire now would add a security-control
> surface serving a tenancy that does not exist. **Adopt instead the two constraints in §6**,
> which cost nothing, are already true, and prevent the regression that would actually hurt.

Deferring is the conservative disposition here, and it is worth naming why: the risk in this
area is not "we lack a propagation mechanism". It is "someone adds one by forwarding a token,
because forwarding is the easy thing and nothing currently forbids it in writing." §6 is the
deliverable that removes that risk.

**What this changes in shipped surfaces: nothing.** No module, config field, route, wire
format or stored shape changes. This note adds doctrine and corrects three stale documents
(§9). That is the whole intended footprint of a spike.

---

## 6. Two constraints to adopt now

Stated as doctrine so a future change has to argue with them rather than drift past them.
Both are **already true** at `c07f7549a`; adopting them costs nothing today and is the
cheapest possible time to write them down.

**C-1 — Core MUST NOT accept a third-party or IdP access token as an inbound bearer.**
An inbound bearer is resolved only against the local client registry
(`src/personalclaw/inbound/clients.py:261`) or the per-surface token
(`src/personalclaw/inbound/auth.py:151-158`). Accepting a foreign token would make core a
resource server for an audience it cannot verify — the exact confusion the prohibition
targets. A future federated mode changes how the *owner* logs in; it must not change what
the inbound surfaces accept.

**C-2 — Core MUST NOT forward an inbound caller's credential to any upstream.**
True by construction today: outbound remote MCP calls carry no headers
(`src/personalclaw/mcp_client.py:281-293`) and injection is env-only/stdio-only
(`src/personalclaw/mcp_client.py:307-309`). If an upstream needs authentication it uses a
credential the operator configured for *that* upstream, never the caller's. Note the one
adjacent spot that already reasons correctly and must stay that way: an empty `upstream`
"never means 'pick one for me'" (`src/personalclaw/inbound/clients.py:84`).

Both constraints are **fail-closed** in the sense the repo's shared conventions require of
inbound and security surfaces: the refusal is the default, and no corrupt or missing input
can relax either one.

---

## 7. Security-control surfaces — every row is owner-escalation

If R2 is ever un-deferred, these are the surfaces it touches. Each is a security-control
decision and none may be taken by an implementing worker without an explicit owner ruling.

| id | surface | why it is owner-escalation |
|---|---|---|
| `S1` | Admitting any end-user identifier at the inbound boundary | Creates a second principal type on a surface whose current guarantee is "one principal, pinned". |
| `S2` | Extending `InboundClient` (`src/personalclaw/inbound/clients.py:65`) with an attribution field | Changes a `0600` on-disk security record and its fail-closed parse. |
| `S3` | Adding a member to `PINNED_BINDINGS` (`src/personalclaw/inbound/clients.py:50`) | The pin set *is* the authorization model; a new pin must be proven enforced. |
| `S4` | Per-user scoping inside tool handlers (`src/personalclaw/inbound/tools.py:294-310`) | Turns single-owner reads into an authorization decision — the largest change in the set. |
| `S5` | Joining `channel_trust` to the inbound package (§3.1) | Couples two deliberately independent trust systems; a shared key would need one owner. |
| `S6` | Making `AuthMode.OAUTH2` selectable (`src/personalclaw/auth/modes.py:57`) | Owned by the SSO/OIDC note, not this one; listed so the two are not implemented twice. |
| `S7` | Any relaxation of C-1 or C-2 (§6) | These are the constraints; relaxing one is the decision this note exists to prevent. |
| `S8` | Attributing an audit line (`src/personalclaw/inbound/audit.py:46`) to an end user | The audit log is the incident-response surface; a second identity in it must be unambiguous. |

---

## 8. If the premise ever changes, the correct shape

Recorded so a future implementer does not re-derive it wrongly. Should PersonalClaw ever
serve multiple end users over MCP, the shape that follows from everything above is:

1. **Extend the existing client record; do not add a parallel path.** An end user becomes an
   attribution field on `InboundClient` (`src/personalclaw/inbound/clients.py:65`), resolved
   from a pairing ceremony — not a `sub` claim lifted from a foreign token.
2. **Reuse the pairing ceremony, do not invent a second one.**
   `create_pairing_code`/`redeem_pairing_code`
   (`src/personalclaw/channel_trust.py:331`, `:379`) already implement out-of-band
   establishment with single use and a TTL. A second ceremony is a second source of truth.
3. **Carry the resolved verdict, never a credential.** `TrustVerdict`
   (`src/personalclaw/channel_trust.py:437`) is the right shape: a decision plus a reason.
4. **Make the new field a pin.** It belongs in `PINNED_BINDINGS`
   (`src/personalclaw/inbound/clients.py:50`) so a request argument can never assert its own
   identity — "an assertion never wins"
   (`src/personalclaw/inbound/clients.py:14-15`).
5. **Scope the stores last, and deliberately.** Until
   `src/personalclaw/inbound/tools.py:294-310` scopes per principal, an identity on the wire
   is decoration. This is `S4` and the largest piece of real work.
6. **Do not reuse `entity_id`.** It is a knowledge-graph node
   (`src/personalclaw/knowledge/store.py:449-457`), not a tenant (§2.2).

---

## 9. Corrections to shipped documents

Code is the as-built authority. Three shipped documents disagree with it and are wrong.

1. **Inbound MCP is described as not yet landed, but it ships.**
   `docs/security/threat-model.md:110-112` says inbound MCP and external remote access are
   "not yet landed", and the ASI07 row at `docs/security/threat-model.md:154` marks the
   control "in progress". The surface is shipped and wired:
   `src/personalclaw/inbound/mcp_http.py:535-536` registers the routes,
   `src/personalclaw/inbound/mcp_http.py:144` handles them, and admission is enforced at
   `src/personalclaw/inbound/mcp_http.py:234` and `:247-250`. The controls that row asks
   for — fail-closed inbound plus fencing at ingestion — are present
   (`src/personalclaw/inbound/clients.py:113-126`,
   `src/personalclaw/inbound/framing.py:69-83`). **The status is stale, not the control.**
2. **The session-token docstring understates both TTLs.**
   `src/personalclaw/dashboard/token_auth.py:515-516` describes a five-minute link window
   capped at twenty hours. The code uses a 24-hour link window
   (`src/personalclaw/dashboard/token_auth.py:383`) and caps the session at one year
   (`src/personalclaw/dashboard/token_auth.py:401`). Anyone reasoning about token lifetime
   from that docstring will be wrong by orders of magnitude — treat a minted token as
   long-lived and sensitive.
3. **The pairing TTL is ten minutes, not an hour.** Planning text describes a one-hour
   pairing code; `src/personalclaw/channel_trust.py:57` is 600 seconds.

A fourth correction concerns this note's own commission. The atom that ordered it, and the
plan behind it, name a `sender_trust` substrate — a `sender_trust.json` store, a
`channel_transports/trust.py` module, and a `check_sender()` entry point. **None of the
three exists.** `sender_trust` appears nowhere in `src`; `channel_transports/` contains only
`__init__.py`, `base.py`, `manager.py`, `reference_echo.py` and `webui.py`; `check_sender`
has zero matches repo-wide. The as-built seam is `channel_trust.py` with
`is_allowed_sender()` (`src/personalclaw/channel_trust.py:197`) and `guard_inbound()`
(`src/personalclaw/channel_trust.py:679`), storing to
`entity_settings/channel_trust.json`. The skew is already recorded in a test that exists to
document it (`tests/test_external_access_seam.py:1077-1080`). This note evaluates against
the code.

---

## 10. Follow-on work this note names

Named for the maintainer's triage, **not registered** — this note creates no plan and no
tracked item.

1. **Correct the two stale `threat-model.md` rows** (§9.1) — the ASI07 status and the
   "not yet landed" paragraph. Docs-only, no security change, and the cheapest item here.
2. **Fix the `generate_token` docstring** (§9.2) so the stated TTLs match `:383` and
   `:401`. Docs-only, in-code.
3. **Record C-1 and C-2 (§6) where a contributor will meet them** — the inbound package
   docstring and the security doc set — so the constraints are enforced by review.
4. **Deliberately not proposed:** any code that admits an end-user identifier at the
   inbound boundary. That is R2, deferred, and every surface it touches is an
   owner-escalation in §7.
