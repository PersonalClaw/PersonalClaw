/**
 * Where the shell will and will not point itself (`CA-8`).
 *
 * This file is the security surface of connect-mode, so the cases are written as the attack they
 * refuse rather than as the branch they cover: a name that resolves to loopback, an IPv4 address
 * spelled as IPv6, a decimal integer host, a URL carrying credentials, plaintext to a public host.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const {
  TRUST_LOOPBACK,
  TRUST_PRIVATE,
  TRUST_PUBLIC,
  TRUST_REFUSED,
  classifyHost,
  parseIPv4,
  parseIPv6,
  isNonCanonicalIpShape,
  parseGatewayUrl,
  parsePairingUrl,
  transportPolicy,
  resolveHostTrust,
  addressFingerprint,
} = require("../gatewayUrl");

const lookupOf = (map) => async (host) => {
  if (!Object.prototype.hasOwnProperty.call(map, host)) {
    throw Object.assign(new Error("not found"), { code: "ENOTFOUND" });
  }
  const v = map[host];
  if (v instanceof Error) throw v;
  return v.map((address) => ({ address, family: address.includes(":") ? 6 : 4 }));
};

describe("parseIPv4 — canonical dotted quad only", () => {
  it("accepts a canonical quad", () => {
    assert.deepStrictEqual(parseIPv4("10.0.0.4"), [10, 0, 0, 4]);
    assert.deepStrictEqual(parseIPv4("0.0.0.0"), [0, 0, 0, 0]);
    assert.deepStrictEqual(parseIPv4("255.255.255.255"), [255, 255, 255, 255]);
  });

  it("rejects leading zeros, short forms and out-of-range octets", () => {
    // `0177.0.0.1` is 127.0.0.1 to inet_aton. Accepting it would make the loopback branch
    // reachable through a spelling the classifier does not recognise as loopback.
    assert.strictEqual(parseIPv4("0177.0.0.1"), null);
    assert.strictEqual(parseIPv4("010.0.0.1"), null);
    assert.strictEqual(parseIPv4("127.1"), null);
    assert.strictEqual(parseIPv4("127.0.0.256"), null);
    assert.strictEqual(parseIPv4("1.2.3.4.5"), null);
  });
});

describe("parseIPv6", () => {
  it("expands compressed and mapped forms", () => {
    assert.deepStrictEqual(parseIPv6("::1"), [0, 0, 0, 0, 0, 0, 0, 1]);
    assert.deepStrictEqual(parseIPv6("fe80::1"), [0xfe80, 0, 0, 0, 0, 0, 0, 1]);
    assert.deepStrictEqual(parseIPv6("::ffff:127.0.0.1"), [0, 0, 0, 0, 0, 0xffff, 0x7f00, 1]);
    assert.deepStrictEqual(parseIPv6("::ffff:7f00:1"), [0, 0, 0, 0, 0, 0xffff, 0x7f00, 1]);
  });

  it("refuses garbage rather than guessing", () => {
    assert.strictEqual(parseIPv6("::1::2"), null);
    assert.strictEqual(parseIPv6("1:2:3"), null);
    assert.strictEqual(parseIPv6("gggg::1"), null);
    assert.strictEqual(parseIPv6("10.0.0.1"), null);
  });
});

describe("isNonCanonicalIpShape — the SSRF spellings", () => {
  it("catches the integer, hex and octal forms", () => {
    for (const host of ["2130706433", "0x7f000001", "0177.0.0.1", "127.1", "0x7f.0.0.1", "010.0.0.1"]) {
      assert.strictEqual(isNonCanonicalIpShape(host), true, host);
    }
  });

  it("does NOT catch ordinary short domains whose labels are hex digits", () => {
    // The naive "any hex character" test refuses these, which would make a real hostname
    // unreachable in the name of a threat that does not apply to it.
    for (const host of ["de.ee", "face.dead", "ab.cd", "claw.local"]) {
      assert.strictEqual(isNonCanonicalIpShape(host), false, host);
    }
  });
});

describe("classifyHost — three classes, plus refusal", () => {
  it("classifies loopback literals", () => {
    assert.strictEqual(classifyHost("127.0.0.1").trust, TRUST_LOOPBACK);
    assert.strictEqual(classifyHost("127.5.5.5").trust, TRUST_LOOPBACK);
    assert.strictEqual(classifyHost("localhost").trust, TRUST_LOOPBACK);
    assert.strictEqual(classifyHost("[::1]").trust, TRUST_LOOPBACK);
  });

  it("classifies RFC1918, CGNAT/Tailscale, mDNS and MagicDNS as private", () => {
    for (const host of ["10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.1.5", "100.101.102.103", "claw.local", "brain.ts.net", "fd00::5"]) {
      assert.strictEqual(classifyHost(host).trust, TRUST_PRIVATE, host);
    }
  });

  it("keeps 172.15 and 172.32 out of RFC1918, and 100.0-63 out of CGNAT", () => {
    // The `172.*` and `100.*` globs a shell is tempted to write hand most of a public /8 to the
    // private class. Spelled as ranges here, so these three are public.
    assert.strictEqual(classifyHost("172.15.0.1").trust, TRUST_PUBLIC);
    assert.strictEqual(classifyHost("172.32.0.1").trust, TRUST_PUBLIC);
    assert.strictEqual(classifyHost("100.63.0.1").trust, TRUST_PUBLIC);
  });

  it("refuses every IP-shaped spelling that is not canonical", () => {
    for (const host of ["2130706433", "0x7f000001", "0177.0.0.1", "127.1"]) {
      const v = classifyHost(host);
      assert.strictEqual(v.trust, TRUST_REFUSED, host);
      assert.strictEqual(v.reason, "non_canonical_ip");
    }
  });

  it("refuses an IPv4 address wearing an IPv6 coat", () => {
    for (const host of ["[::ffff:127.0.0.1]", "[::ffff:7f00:1]", "[::ffff:169.254.169.254]", "[::127.0.0.1]"]) {
      assert.strictEqual(classifyHost(host).reason, "mapped_ipv4", host);
    }
  });

  it("refuses link-local, which is also where the cloud metadata service lives", () => {
    assert.strictEqual(classifyHost("169.254.169.254").reason, "ipv4_link_local");
    assert.strictEqual(classifyHost("169.254.1.1").reason, "ipv4_link_local");
    assert.strictEqual(classifyHost("[fe80::1]").reason, "ipv6_link_local");
  });

  it("refuses the alternate loopback names the hosts file ships", () => {
    for (const host of ["localhost.localdomain", "ip6-localhost", "ip6-loopback"]) {
      assert.strictEqual(classifyHost(host).reason, "alternate_loopback_name", host);
    }
  });

  it("does not let a trailing root dot smuggle a loopback literal past the quad parser", () => {
    assert.strictEqual(classifyHost("127.0.0.1.").trust, TRUST_LOOPBACK);
  });

  it("answers `public` for a name it cannot classify by spelling", () => {
    const v = classifyHost("pc.example.com");
    assert.strictEqual(v.trust, TRUST_PUBLIC);
    assert.strictEqual(v.reason, "name_unresolved");
  });
});

describe("parseGatewayUrl", () => {
  it("returns an origin and drops any path, query or fragment", () => {
    const r = parseGatewayUrl("http://claw.local:10000/dashboard?x=1#/chat");
    assert.ok(r.ok);
    assert.strictEqual(r.origin, "http://claw.local:10000");
  });

  it("accepts a bare host:port and says it assumed http", () => {
    const r = parseGatewayUrl("192.168.1.5:10000");
    assert.ok(r.ok);
    assert.strictEqual(r.origin, "http://192.168.1.5:10000");
    assert.strictEqual(r.schemeAssumed, true);
    assert.strictEqual(r.trust, TRUST_PRIVATE);
  });

  it("refuses credentials in the URL", () => {
    const r = parseGatewayUrl("http://admin:hunter2@10.0.0.4:10000");
    assert.strictEqual(r.ok, false);
    assert.strictEqual(r.code, "HAS_CREDENTIALS");
  });

  it("refuses every scheme but http and https", () => {
    for (const raw of ["file:///etc/passwd", "data:text/html,<b>x", "ws://10.0.0.4/api/ws", "javascript:alert(1)"]) {
      const r = parseGatewayUrl(raw);
      assert.strictEqual(r.ok, false, raw);
    }
    assert.strictEqual(parseGatewayUrl("ftp://10.0.0.4").code, "BAD_SCHEME");
  });

  it("refuses INTERIOR whitespace and control characters before the URL parser can be lenient", () => {
    assert.strictEqual(parseGatewayUrl("http://10.0.0.4 /x").code, "CONTROL_CHARS");
    assert.strictEqual(parseGatewayUrl("http://10.0.0.4\u0000").code, "CONTROL_CHARS");
    assert.strictEqual(parseGatewayUrl("http://10.0\t.0.4").code, "CONTROL_CHARS");
  });

  it("TRIMS surrounding whitespace rather than refusing it - that is a paste, not an attack", () => {
    const r = parseGatewayUrl("  http://10.0.0.4:10000\n");
    assert.ok(r.ok, r.code);
    assert.strictEqual(r.origin, "http://10.0.0.4:10000");
  });

  it("keeps a hyphenated hostname, which the control-character class must not eat", () => {
    const r = parseGatewayUrl("http://my-brain.local:10000");
    assert.ok(r.ok, r.code);
    assert.strictEqual(r.hostname, "my-brain.local");
  });

  it("reports the refusing rule by name so the dialog can say which", () => {
    assert.match(parseGatewayUrl("http://2130706433:10000").code, /^REFUSED_HOST:non_canonical_ip$/);
    assert.match(parseGatewayUrl("http://169.254.169.254").code, /^REFUSED_HOST:ipv4_link_local$/);
  });

  it("distinguishes empty from unparseable", () => {
    assert.strictEqual(parseGatewayUrl("").code, "EMPTY");
    assert.strictEqual(parseGatewayUrl("   ").code, "EMPTY");
    assert.strictEqual(parseGatewayUrl("http://").code, "BAD_URL");
  });
});

describe("parsePairingUrl — the preferred input, because the gateway composed it", () => {
  it("splits a pairing URL into an origin, a code and a target", () => {
    const r = parsePairingUrl("http://claw.local:10000/pair?code=ABCD-2345");
    assert.ok(r.ok, r.code);
    assert.strictEqual(r.origin, "http://claw.local:10000");
    assert.strictEqual(r.pairingCode, "ABCD-2345");
    assert.strictEqual(r.pairTarget, "http://claw.local:10000/pair?code=ABCD-2345");
  });

  it("refuses a link that is not the pairing page", () => {
    assert.strictEqual(parsePairingUrl("http://claw.local:10000/login?code=ABCD-2345").code, "NOT_PAIRING");
  });

  it("refuses a pairing link with no code, and one whose code is not code-shaped", () => {
    assert.strictEqual(parsePairingUrl("http://claw.local:10000/pair").code, "NO_CODE");
    assert.strictEqual(parsePairingUrl("http://claw.local:10000/pair?code=%3Cscript%3E").code, "BAD_CODE");
  });

  it("applies the same host refusals as a typed address", () => {
    assert.match(parsePairingUrl("http://2130706433/pair?code=ABCD-2345").code, /REFUSED_HOST:non_canonical_ip/);
  });
});

describe("transportPolicy — loopback, LAN and the public internet are three answers", () => {
  it("allows plaintext loopback with no confirmation at all", () => {
    const p = transportPolicy({ scheme: "http:", trust: TRUST_LOOPBACK });
    assert.deepStrictEqual([p.allowed, p.requiresConfirmation], [true, false]);
  });

  it("allows plaintext on a private network only behind an explicit confirmation, and says why", () => {
    const p = transportPolicy({ scheme: "http:", trust: TRUST_PRIVATE });
    assert.deepStrictEqual([p.allowed, p.requiresConfirmation], [true, true]);
    assert.match(p.warning, /not encrypted/i);
    assert.match(p.warning, /read this session/i);
  });

  it("REFUSES plaintext to a public host — this is not a warning", () => {
    const p = transportPolicy({ scheme: "http:", trust: TRUST_PUBLIC });
    assert.strictEqual(p.allowed, false);
    assert.strictEqual(p.code, "PLAINTEXT_PUBLIC_REFUSED");
  });

  it("allows https to a private or public host behind one confirmation", () => {
    for (const trust of [TRUST_PRIVATE, TRUST_PUBLIC]) {
      const p = transportPolicy({ scheme: "https:", trust });
      assert.deepStrictEqual([p.allowed, p.requiresConfirmation], [true, true], trust);
    }
  });

  it("allows nothing for a refused host or a refused scheme", () => {
    assert.strictEqual(transportPolicy({ scheme: "https:", trust: TRUST_REFUSED }).allowed, false);
    assert.strictEqual(transportPolicy({ scheme: "file:", trust: TRUST_PRIVATE }).allowed, false);
  });
});

describe("resolveHostTrust — the DNS half", () => {
  it("does not ask a resolver about an IP literal", async () => {
    let asked = 0;
    const r = await resolveHostTrust("10.0.0.4", {
      lookup: async () => {
        asked += 1;
        return [];
      },
    });
    assert.strictEqual(asked, 0);
    assert.strictEqual(r.effective, TRUST_PRIVATE);
  });

  it("a NAME that resolves to loopback is private, NEVER loopback", async () => {
    // This is the whole rebinding guard: `lh.0x41.pw` really does resolve to 127.0.0.1, and
    // loopback is the one class that earns the capability bridge.
    const r = await resolveHostTrust("lh.0x41.pw", { lookup: lookupOf({ "lh.0x41.pw": ["127.0.0.1"] }) });
    assert.ok(r.ok);
    assert.strictEqual(r.resolved, TRUST_LOOPBACK);
    assert.strictEqual(r.effective, TRUST_PRIVATE);
    assert.strictEqual(r.reason, "name_resolves_to_loopback");
  });

  it("a public-looking name that resolves into RFC1918 is honestly private", async () => {
    const r = await resolveHostTrust("brain.example.com", { lookup: lookupOf({ "brain.example.com": ["10.7.7.7"] }) });
    assert.strictEqual(r.literal, TRUST_PUBLIC);
    assert.strictEqual(r.effective, TRUST_PRIVATE);
  });

  it("refuses a name that resolves into the metadata range", async () => {
    const r = await resolveHostTrust("mt.0x41.pw", { lookup: lookupOf({ "mt.0x41.pw": ["169.254.169.254"] }) });
    assert.strictEqual(r.ok, false);
    assert.strictEqual(r.reason, "resolves_to_refused_range");
  });

  it("refuses a name whose addresses straddle two classes rather than picking the friendlier one", async () => {
    const r = await resolveHostTrust("both.example.com", { lookup: lookupOf({ "both.example.com": ["10.0.0.1", "93.184.216.34"] }) });
    assert.strictEqual(r.ok, false);
    assert.strictEqual(r.reason, "mixed_resolution");
  });

  it("reports a failed lookup and an empty answer as two different facts", async () => {
    const failed = await resolveHostTrust("nope.example.com", { lookup: lookupOf({}) });
    assert.strictEqual(failed.reason, "dns_failed");
    const empty = await resolveHostTrust("void.example.com", { lookup: lookupOf({ "void.example.com": [] }) });
    assert.strictEqual(empty.reason, "dns_empty");
  });
});

describe("addressFingerprint", () => {
  it("is order- and duplicate-insensitive so a resolver rotation is not a host move", () => {
    assert.strictEqual(addressFingerprint(["10.0.0.2", "10.0.0.1"]), addressFingerprint(["10.0.0.1", "10.0.0.2", "10.0.0.1"]));
  });

  it("changes when the address set really changes", () => {
    assert.notStrictEqual(addressFingerprint(["10.0.0.1"]), addressFingerprint(["10.0.0.2"]));
  });
});
