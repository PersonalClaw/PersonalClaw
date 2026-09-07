/**
 * Where the desktop shell is allowed to point itself (COMPANION-APPS T4.1, `CA-8`).
 *
 * Connect-mode makes the shell's target a **user-supplied network endpoint**, which is the one
 * genuine trust boundary this atom adds. Everything about that boundary is decided here, in one
 * pure module with no Electron import, so it can be exhaustively tested and so no other file
 * gets to re-decide it.
 *
 * 🔑 THREE TRUST CLASSES, NEVER TWO. `loopback`, `private` and `public` are separate answers and
 * are deliberately not collapsed:
 *
 *   - **loopback** is the gateway the shell spawned itself. It is the only class that gets the
 *     capability bridge (`preload.js`), because registration proves "I am a process running as
 *     this user on this machine" with `~/.personalclaw/.local_secret` — a claim that is false for
 *     every other class by construction.
 *   - **private** is a LAN/tailnet gateway. Reachable, but the network is shared: plaintext is
 *     readable by anyone on it and nothing authenticates the *host*.
 *   - **public** is a gateway through the owner's own tunnel (REMOTE-USER-AUTH S4). TLS is not
 *     optional there — see `transportPolicy`.
 *
 * 🪤 LOOPBACK PRIVILEGE REQUIRES A LOOPBACK **LITERAL**. A DNS name that resolves to 127.0.0.1
 * classifies as `private` at best, never `loopback`. Otherwise `lh.0x41.pw` (a real name that
 * resolves to 127.0.0.1) or any attacker-controlled record would earn the capability bridge by
 * winning a DNS lookup. This is the DNS-rebinding half of the SSRF guidance: validate the host,
 * then do not let a resolver hand back a class the host string did not claim.
 *
 * 🪤 IP-SHAPED-BUT-NOT-CANONICAL HOSTS ARE REFUSED OUTRIGHT. `2130706433`, `0x7f000001`,
 * `0177.0.0.1`, `127.1` and `::ffff:7f00:1` all reach 127.0.0.1 through some client, and each
 * one classifies as "public" under a naive dotted-quad test. There is no legitimate reason for a
 * user to type one, so `classifyHost` answers `refused` rather than trying to normalize them.
 */

// ── outcome vocabularies (closed sets; a caller may switch on them) ────────────────────────────

/** Trust classes, most privileged first. */
const TRUST_LOOPBACK = "loopback";
const TRUST_PRIVATE = "private";
const TRUST_PUBLIC = "public";
/** Not a class the shell will connect to at all. `reason` says which rule refused it. */
const TRUST_REFUSED = "refused";

const TRUST_CLASSES = Object.freeze([TRUST_LOOPBACK, TRUST_PRIVATE, TRUST_PUBLIC, TRUST_REFUSED]);

/** Schemes a gateway may be reached over. Nothing else — `file:`, `data:`, `ws:` included. */
const ALLOWED_SCHEMES = Object.freeze(["http:", "https:"]);

// ── literal-host classification ────────────────────────────────────────────────────────────────

/** Strict canonical dotted quad. Rejects leading zeros (octal), short forms and out-of-range. */
function parseIPv4(text) {
  const parts = String(text).split(".");
  if (parts.length !== 4) return null;
  const out = [];
  for (const part of parts) {
    // `0` alone is fine; `01` is an octal literal to some resolvers and is refused above.
    if (!/^(0|[1-9]\d{0,2})$/.test(part)) return null;
    const n = Number(part);
    if (n > 255) return null;
    out.push(n);
  }
  return out;
}

/**
 * Does this host LOOK like an IP address without being a canonical one?
 *
 * Answering yes means "refuse", never "try harder". Each shape below is a documented SSRF
 * bypass: a bare integer, a hex literal, an octal-looking octet, or a 2/3-part short form.
 */
function isNonCanonicalIpShape(host) {
  if (/^\d+$/.test(host)) return true; // 2130706433
  if (/^0[xX][0-9a-fA-F]+$/.test(host)) return true; // 0x7f000001
  const parts = host.split(".");
  // Dotted and every part is numeric or an explicit hex literal: a short form (`127.1`), an octal
  // octet (`0177.0.0.1`), or a hex octet (`0x7f.0.0.1`). A canonical quad never reaches here — the
  // caller runs `parseIPv4` first. The part test is deliberately NOT "any hex characters": that
  // would refuse ordinary short domains like `de.ee`, whose labels happen to be hex digits.
  if (
    parts.length >= 2 &&
    parts.length <= 4 &&
    parts.every((p) => /^(0[xX][0-9a-fA-F]+|\d+)$/.test(p))
  ) {
    return true;
  }
  return false;
}

/** Expand an IPv6 text form to 8 hextet numbers, or null. Accepts a trailing dotted-quad. */
function parseIPv6(text) {
  let body = String(text);
  if (body.includes("%")) body = body.slice(0, body.indexOf("%")); // drop a zone id
  if (!body.includes(":")) return null;

  let tail4 = null;
  const lastColon = body.lastIndexOf(":");
  const maybeQuad = body.slice(lastColon + 1);
  if (maybeQuad.includes(".")) {
    tail4 = parseIPv4(maybeQuad);
    if (!tail4) return null;
    body = body.slice(0, lastColon + 1) + "0:0";
  }

  const halves = body.split("::");
  if (halves.length > 2) return null;
  const toHextets = (chunk) =>
    chunk === ""
      ? []
      : chunk.split(":").map((h) => (/^[0-9a-fA-F]{1,4}$/.test(h) ? parseInt(h, 16) : NaN));

  let hextets;
  if (halves.length === 2) {
    const head = toHextets(halves[0]);
    const tail = toHextets(halves[1]);
    const fill = 8 - head.length - tail.length;
    if (fill < 0) return null;
    hextets = [...head, ...new Array(fill).fill(0), ...tail];
  } else {
    hextets = toHextets(halves[0]);
  }
  if (hextets.length !== 8 || hextets.some((h) => Number.isNaN(h))) return null;
  if (tail4) {
    hextets[6] = (tail4[0] << 8) | tail4[1];
    hextets[7] = (tail4[2] << 8) | tail4[3];
  }
  return hextets;
}

function verdict(trust, reason) {
  return { trust, reason };
}

/**
 * Classify a host STRING. Never resolves DNS — see `resolveHostTrust` for that half.
 *
 * A name (not an IP literal) answers `public` unless it is structurally private
 * (`*.local` mDNS, `*.ts.net` MagicDNS) or the literal `localhost`. `public` here means
 * "unknown until resolved", and the caller is expected to resolve before granting anything.
 */
function classifyHost(rawHost) {
  const host = String(rawHost ?? "")
    .trim()
    .replace(/^\[|\]$/g, "")
    // A trailing root dot is the same host to every resolver, so it is dropped BEFORE
    // classification. Left on, `127.0.0.1.` fails the dotted-quad parse and falls through to the
    // name branch, where it would classify `public` — a loopback literal wearing a dot.
    .replace(/\.$/, "")
    .toLowerCase();
  if (!host) return verdict(TRUST_REFUSED, "empty_host");

  // ── IPv6 literals ──
  if (host.includes(":")) {
    const v6 = parseIPv6(host);
    if (!v6) return verdict(TRUST_REFUSED, "malformed_ipv6");
    const isZero = v6.every((h) => h === 0);
    if (isZero) return verdict(TRUST_REFUSED, "unspecified_address"); // ::
    if (v6.slice(0, 7).every((h) => h === 0) && v6[7] === 1) return verdict(TRUST_LOOPBACK, "ipv6_loopback");
    // ::ffff:a.b.c.d and ::a.b.c.d — an IPv4 address wearing an IPv6 coat. Refused rather than
    // unwrapped: `curl -g -6 'http://[::ffff:7f00:1]/'` reaches 127.0.0.1, so unwrapping would
    // make loopback privilege reachable through a spelling.
    if (v6.slice(0, 5).every((h) => h === 0) && (v6[5] === 0xffff || v6[5] === 0)) {
      return verdict(TRUST_REFUSED, "mapped_ipv4");
    }
    if ((v6[0] & 0xffc0) === 0xfe80) return verdict(TRUST_REFUSED, "ipv6_link_local");
    if ((v6[0] & 0xfe00) === 0xfc00) return verdict(TRUST_PRIVATE, "ipv6_unique_local");
    return verdict(TRUST_PUBLIC, "ipv6_global");
  }

  // ── IPv4 literals ──
  const v4 = parseIPv4(host);
  if (v4) {
    const [a, b] = v4;
    if (a === 127) return verdict(TRUST_LOOPBACK, "ipv4_loopback");
    if (a === 0) return verdict(TRUST_REFUSED, "this_network");
    // 169.254/16 is APIPA — and 169.254.169.254 is the cloud instance-metadata service. A
    // gateway is never legitimately at a link-local address, so the whole /16 is refused rather
    // than carving out one host.
    if (a === 169 && b === 254) return verdict(TRUST_REFUSED, "ipv4_link_local");
    if (a === 10) return verdict(TRUST_PRIVATE, "rfc1918_10");
    if (a === 172 && b >= 16 && b <= 31) return verdict(TRUST_PRIVATE, "rfc1918_172");
    if (a === 192 && b === 168) return verdict(TRUST_PRIVATE, "rfc1918_192");
    // 100.64/10 — CGNAT, and the range Tailscale hands out.
    if (a === 100 && b >= 64 && b <= 127) return verdict(TRUST_PRIVATE, "cgnat_tailscale");
    if (a >= 224) return verdict(TRUST_REFUSED, "multicast_or_reserved");
    return verdict(TRUST_PUBLIC, "ipv4_global");
  }

  if (isNonCanonicalIpShape(host)) return verdict(TRUST_REFUSED, "non_canonical_ip");

  // ── names ──
  if (host === "localhost") return verdict(TRUST_LOOPBACK, "localhost");
  // The OS hosts file ships more loopback spellings than `localhost`; each is loopback, and a
  // gateway is not reached by any of them, so they are refused rather than privileged.
  if (host === "localhost.localdomain" || host === "ip6-localhost" || host === "ip6-loopback") {
    return verdict(TRUST_REFUSED, "alternate_loopback_name");
  }
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.?$/.test(host)) {
    return verdict(TRUST_REFUSED, "malformed_hostname");
  }
  const bare = host.replace(/\.$/, "");
  if (bare === "local" || bare.endsWith(".local")) return verdict(TRUST_PRIVATE, "mdns_local");
  if (bare.endsWith(".ts.net")) return verdict(TRUST_PRIVATE, "tailscale_magicdns");
  // Anything else is UNKNOWN until it resolves. `public` is the safe placeholder because it is
  // the least privileged non-refused class.
  return verdict(TRUST_PUBLIC, "name_unresolved");
}

// ── URL parsing ────────────────────────────────────────────────────────────────────────────────

function reject(code, message) {
  return { ok: false, code, message };
}

/**
 * The host as WRITTEN in a URL string, before any parser normalises it.
 *
 * Needed because the WHATWG parser rewrites `2130706433` to `127.0.0.1` (see the note in
 * `parseGatewayUrl`), so a refusal that has to fire on the spelling cannot read `url.hostname`.
 * Deliberately dumb string work: scheme off, then up to the first `/?#`, then userinfo off at the
 * LAST `@` (an earlier one can appear inside a password), then the port off — skipping a bracketed
 * IPv6 literal, whose colons are part of the host.
 */
function rawAuthorityHost(candidate) {
  const afterScheme = String(candidate).replace(/^[a-z][a-z0-9+.-]*:\/\//i, "");
  const authority = afterScheme.split(/[/?#]/)[0];
  const at = authority.lastIndexOf("@");
  const hostPort = at >= 0 ? authority.slice(at + 1) : authority;
  if (hostPort.startsWith("[")) {
    const close = hostPort.indexOf("]");
    return close > 0 ? hostPort.slice(0, close + 1) : hostPort;
  }
  const colon = hostPort.lastIndexOf(":");
  return colon >= 0 ? hostPort.slice(0, colon) : hostPort;
}

/**
 * Turn what the user typed (or what a pairing URL carried) into a gateway **origin**.
 *
 * Returns `{ok:true, origin, scheme, host, hostname, port, trust, trustReason}` or
 * `{ok:false, code, message}`. Every rejection is coded so the dialog can say which rule fired
 * rather than "invalid URL".
 *
 * Only an origin comes back. Any path, query or fragment the input carried is DROPPED — the
 * shell decides which route to open, never the input. That is also why a pairing URL is parsed
 * by `parsePairingUrl` instead of being smuggled through here.
 */
function parseGatewayUrl(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return reject("EMPTY", "Enter your gateway address.");
  // Whitespace and control characters, refused before `new URL()` gets a chance to be lenient
  // about them. Spelled with codepoint arithmetic rather than a character-class escape so this
  // source file holds no control bytes of its own. (A hyphen is legal in a hostname and is
  // deliberately NOT in this class.)
  const controlish = (ch) => ch.codePointAt(0) < 0x20 || ch.codePointAt(0) === 0x7f;
  if (/\s/.test(text) || [...text].some(controlish)) {
    return reject("CONTROL_CHARS", "That address contains characters an address cannot contain.");
  }

  // No scheme means someone typed `192.168.1.5:10000`. Default to http:, because a gateway on a
  // private address is overwhelmingly plain HTTP — and the transport policy below still makes the
  // user confirm it. (Matches `mobile/www/shell/network.mjs`, deliberately.)
  const hasScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(text);
  const candidate = hasScheme ? text : `http://${text}`;

  let url;
  try {
    url = new URL(candidate);
  } catch {
    return reject("BAD_URL", `${text} is not an address.`);
  }
  if (!ALLOWED_SCHEMES.includes(url.protocol)) {
    return reject("BAD_SCHEME", "A gateway is reached over http or https, and nothing else.");
  }
  // Credentials in a URL are the classic validator bypass (parsers disagree about where the host
  // starts). The shell has no use for them, so they are refused rather than stripped.
  if (url.username || url.password) {
    return reject("HAS_CREDENTIALS", "Remove the username and password from the address.");
  }
  if (!url.hostname) return reject("BAD_URL", `${text} is not an address.`);
  if (url.port && !/^\d{1,5}$/.test(url.port)) return reject("BAD_PORT", "That port is not a number.");
  if (url.port && (Number(url.port) < 1 || Number(url.port) > 65535)) {
    return reject("BAD_PORT", "That port is outside 1–65535.");
  }

  // 🪤 MEASURED: THE WHATWG URL PARSER SILENTLY CANONICALISES THE SSRF SPELLINGS.
  // `new URL('http://2130706433').hostname` is `'127.0.0.1'`, and so is `0x7f000001`'s and
  // `0177.0.0.1`'s. So `classifyHost(url.hostname)` alone never sees a non-canonical form and
  // cannot refuse one. That canonicalisation is not itself a bypass here (Node's HTTP client is
  // handed the same parsed host, so validator and client agree) — but the shell must still refuse
  // these on INPUT: they are never what a person meant to type, and a stored `base_url` that
  // depends on one parser agreeing with another is a trap for whoever re-parses it next. Hence the
  // check runs on the RAW authority text, before the parser gets to be helpful.
  const rawHost = rawAuthorityHost(candidate);
  if (rawHost && !parseIPv4(rawHost) && isNonCanonicalIpShape(rawHost)) {
    return {
      ok: false,
      code: `REFUSED_HOST:non_canonical_ip`,
      message: refusalMessage("non_canonical_ip"),
      trust: TRUST_REFUSED,
      trustReason: "non_canonical_ip",
    };
  }

  const { trust, reason } = classifyHost(url.hostname);
  if (trust === TRUST_REFUSED) {
    return { ok: false, code: `REFUSED_HOST:${reason}`, message: refusalMessage(reason), trust, trustReason: reason };
  }

  return {
    ok: true,
    origin: url.origin,
    scheme: url.protocol,
    host: url.host,
    hostname: url.hostname.replace(/^\[|\]$/g, "").toLowerCase(),
    port: url.port,
    trust,
    trustReason: reason,
    /** True when the user typed no scheme and http: was assumed for them. */
    schemeAssumed: !hasScheme,
  };
}

function refusalMessage(reason) {
  switch (reason) {
    case "non_canonical_ip":
      return "Write an address as a normal dotted IP (10.0.0.4), not as a number or in hex.";
    case "mapped_ipv4":
      return "That is an IPv4 address written as IPv6. Use the plain IPv4 form.";
    case "ipv4_link_local":
    case "ipv6_link_local":
      return "Link-local addresses are not gateways, and one of them is the cloud metadata service.";
    case "alternate_loopback_name":
      return "Use localhost or 127.0.0.1 for a gateway on this machine.";
    case "unspecified_address":
    case "this_network":
      return "That address does not name a host.";
    case "multicast_or_reserved":
      return "That address range cannot host a gateway.";
    case "malformed_ipv6":
    case "malformed_hostname":
      return "That is not a hostname or an IP address.";
    default:
      return "That address is not one the app will connect to.";
  }
}

/** The gateway's device-pairing page (`GET /pair`, `handlers/devices.py:pair_page`). */
const PAIR_ROUTE = "/pair";

/**
 * Read a pairing URL — `<base>/pair?code=XXXX-XXXX` — into an origin plus a code.
 *
 * 🔑 THIS IS THE PREFERRED WAY TO ADD AN ENDPOINT, and the reason is a security one. `base` is
 * composed **by the gateway** (`handlers/devices.py:_pair_base_url`, C2 (a)), so pasting the URL
 * means the host string came from the owner's own gateway rather than from their typing. A typo
 * cannot point the shell somewhere else, because there is nothing to mistype.
 *
 * The code is NOT a credential this shell holds. It rides in the URL handed to the WebView, which
 * is what lets the served `/pair` page do the redemption so the session cookie lands in the
 * WebView's own jar (the same ruling `mobile/www/shell/network.mjs:pairingTargetFromScan`
 * records: a shell that redeemed natively would hold a session the WebView could not use).
 */
function parsePairingUrl(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return reject("EMPTY", "Paste the pairing link from Settings → Devices.");
  let url;
  try {
    url = new URL(text);
  } catch {
    return reject("NOT_PAIRING", "That is not a pairing link.");
  }
  // 🪤 VALIDATE THE RAW TEXT, NOT `url.origin`. This used to pass `url.origin`, and a test caught
  // what that costs: the WHATWG parser has already rewritten `http://2130706433/pair?code=…` to
  // origin `http://127.0.0.1`, so the non-canonical-spelling refusal could never fire for a
  // pairing link — the one input shape a user is most likely to paste from somewhere else.
  // `parseGatewayUrl` drops the path and query itself, so handing it the whole URL is correct and
  // leaves exactly one validator for both entry points.
  const base = parseGatewayUrl(text);
  if (!base.ok) return base;
  if (url.pathname.replace(/\/+$/, "") !== PAIR_ROUTE) {
    return reject("NOT_PAIRING", "That link does not point at a gateway's pairing page.");
  }
  const code = (url.searchParams.get("code") ?? "").trim();
  if (!code) return reject("NO_CODE", "That pairing link carries no code.");
  // The code vocabulary is `pairing.format_code`'s: groups of A–Z/0–9 separated by `-`. Refusing
  // anything else keeps arbitrary text out of the URL the WebView is handed.
  if (!/^[A-Z0-9]{2,8}(-[A-Z0-9]{2,8}){0,3}$/i.test(code)) {
    return reject("BAD_CODE", "That pairing code is not in the expected form.");
  }
  return {
    ok: true,
    origin: base.origin,
    scheme: base.scheme,
    host: base.host,
    hostname: base.hostname,
    port: base.port,
    trust: base.trust,
    trustReason: base.trustReason,
    // 🪤 NOT `code`. On a rejection every function here answers `{ok:false, code}` where `code` is
    // the RULE that refused; a success field also called `code` would mean a caller reading
    // `r.code` gets a rejection reason or a pairing code depending on a flag it may not have
    // checked. A test caught exactly that confusion, so the field is named for what it is.
    pairingCode: code,
    pairTarget: `${base.origin}${PAIR_ROUTE}?code=${encodeURIComponent(code)}`,
  };
}

// ── transport policy ───────────────────────────────────────────────────────────────────────────

/**
 * May the shell connect over this scheme to a host in this trust class, and what is the user told?
 *
 * `{allowed, requiresConfirmation, code, warning}`. The three classes get three answers, and the
 * narrowest one is deliberate:
 *
 *   - **loopback + http** — allowed silently. This is the spawn-local default and the traffic
 *     never leaves the machine.
 *   - **private + http** — allowed, but ONLY behind an explicit confirmation, because every other
 *     device on that network can read the session cookie and the whole conversation. The plan's
 *     own C1 example is `http://claw.local:10000`, so refusing this outright would refuse the
 *     documented LAN case; making the user say yes once is the narrowest honest answer.
 *   - **public + http** — REFUSED. Not warned about: refused. A gateway reached over the internet
 *     rides REMOTE-USER-AUTH's TLS boundary, whose session cookie is `Secure` and therefore is
 *     not even sent over plaintext — so a plaintext public endpoint is broken as well as unsafe,
 *     and "it does not work" is not a tradeoff worth offering.
 *   - **https, any class** — allowed behind the same one-time confirmation as any non-loopback
 *     host, because TLS authenticates the *name*, and the shell still cannot tell the user's own
 *     gateway from any other host that holds a certificate for that name.
 */
function transportPolicy({ scheme, trust }) {
  if (trust === TRUST_REFUSED) {
    return { allowed: false, requiresConfirmation: false, code: "REFUSED_HOST", warning: "" };
  }
  if (!ALLOWED_SCHEMES.includes(scheme)) {
    return { allowed: false, requiresConfirmation: false, code: "BAD_SCHEME", warning: "" };
  }
  const plaintext = scheme === "http:";

  if (trust === TRUST_LOOPBACK) {
    return {
      allowed: true,
      requiresConfirmation: false,
      code: plaintext ? "LOOPBACK_PLAINTEXT_OK" : "LOOPBACK_TLS_OK",
      warning: "",
    };
  }
  if (trust === TRUST_PUBLIC && plaintext) {
    return {
      allowed: false,
      requiresConfirmation: false,
      code: "PLAINTEXT_PUBLIC_REFUSED",
      warning:
        "This address is on the public internet and http:// would send your session in the clear. " +
        "Use https:// — a remote gateway is reached through your own tunnel, which terminates TLS.",
    };
  }
  if (plaintext) {
    return {
      allowed: true,
      requiresConfirmation: true,
      code: "PLAINTEXT_PRIVATE_CONFIRM",
      warning:
        "http:// on a local network is not encrypted. Anyone else on this network can read this " +
        "session, including the cookie that keeps you signed in. Only continue on a network you trust.",
    };
  }
  return {
    allowed: true,
    requiresConfirmation: true,
    code: trust === TRUST_PUBLIC ? "TLS_PUBLIC_CONFIRM" : "TLS_PRIVATE_CONFIRM",
    warning:
      "Check the address below is your own gateway. The app cannot tell your gateway apart from " +
      "any other machine answering at this address.",
  };
}

// ── DNS resolution: the second half of host classification ─────────────────────────────────────

/**
 * Resolve a hostname and classify what it actually points at.
 *
 * Two things this exists for, both of which a literal-only check misses:
 *
 *   1. **A name can point somewhere its spelling does not admit.** `pc.example.com` classifies
 *      `public` by spelling; if it resolves into RFC1918 it is genuinely a private endpoint, and a
 *      shell that ignored that would tell the user "public internet" about their own LAN box.
 *   2. **A name can point at loopback.** That must NOT earn loopback privilege — see the module
 *      header. `resolved` is reported, `effective` never rises above `private` for a name.
 *
 * `{ok, literal, resolved, effective, addresses, reason}`. A name whose addresses span more than
 * one class answers `ok:false` with `mixed_resolution`: the shell cannot tell the user one true
 * thing about it, and picking the friendlier class would be picking the wrong one.
 *
 * `lookup` is injected (`dns.promises.lookup` in production) so this is testable without a
 * network, and so no caller can accidentally use a resolver with different semantics.
 */
async function resolveHostTrust(hostname, { lookup } = {}) {
  const literalVerdict = classifyHost(hostname);
  if (literalVerdict.trust === TRUST_REFUSED) {
    return {
      ok: false,
      literal: TRUST_REFUSED,
      resolved: null,
      effective: TRUST_REFUSED,
      addresses: [],
      reason: literalVerdict.reason,
    };
  }
  // An IP literal resolves to itself. Asking a resolver would be a second answer to a question
  // that already has one, and a chance for the two to disagree.
  const isLiteral = parseIPv4(hostname) !== null || String(hostname).includes(":");
  if (isLiteral || typeof lookup !== "function") {
    return {
      ok: true,
      literal: literalVerdict.trust,
      resolved: isLiteral ? literalVerdict.trust : null,
      effective: literalVerdict.trust,
      addresses: isLiteral ? [String(hostname)] : [],
      reason: isLiteral ? literalVerdict.reason : "not_resolved",
    };
  }

  let records;
  try {
    records = await lookup(hostname, { all: true });
  } catch (err) {
    return {
      ok: false,
      literal: literalVerdict.trust,
      resolved: null,
      effective: TRUST_REFUSED,
      addresses: [],
      reason: "dns_failed",
      detail: err && err.code ? String(err.code) : "",
    };
  }
  const addresses = (Array.isArray(records) ? records : [records])
    .map((r) => (r && typeof r === "object" ? r.address : r))
    .filter((a) => typeof a === "string" && a);
  if (!addresses.length) {
    return {
      ok: false,
      literal: literalVerdict.trust,
      resolved: null,
      effective: TRUST_REFUSED,
      addresses: [],
      reason: "dns_empty",
    };
  }
  const classes = new Set(addresses.map((a) => classifyHost(a).trust));
  if (classes.has(TRUST_REFUSED)) {
    return {
      ok: false,
      literal: literalVerdict.trust,
      resolved: TRUST_REFUSED,
      effective: TRUST_REFUSED,
      addresses,
      reason: "resolves_to_refused_range",
    };
  }
  if (classes.size > 1) {
    return {
      ok: false,
      literal: literalVerdict.trust,
      resolved: null,
      effective: TRUST_REFUSED,
      addresses,
      reason: "mixed_resolution",
    };
  }
  const resolved = [...classes][0];
  // A NAME never reaches `loopback`, however it resolves. That ceiling is the rebinding guard.
  const effective = resolved === TRUST_LOOPBACK ? TRUST_PRIVATE : resolved;
  return {
    ok: true,
    literal: literalVerdict.trust,
    resolved,
    effective,
    addresses,
    reason: resolved === TRUST_LOOPBACK ? "name_resolves_to_loopback" : "resolved",
  };
}

/**
 * A stable fingerprint of what a hostname resolved to, so a later connect can notice that it
 * moved. Sorted, so a resolver returning the same set in a different order is not a change.
 */
function addressFingerprint(addresses) {
  return [...new Set((addresses || []).map((a) => String(a).toLowerCase()))].sort().join(",");
}

module.exports = {
  TRUST_LOOPBACK,
  TRUST_PRIVATE,
  TRUST_PUBLIC,
  TRUST_REFUSED,
  TRUST_CLASSES,
  ALLOWED_SCHEMES,
  PAIR_ROUTE,
  parseIPv4,
  parseIPv6,
  isNonCanonicalIpShape,
  classifyHost,
  parseGatewayUrl,
  parsePairingUrl,
  transportPolicy,
  resolveHostTrust,
  addressFingerprint,
};
