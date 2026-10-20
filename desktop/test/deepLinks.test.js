const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const {
  SCHEME,
  ROUTES,
  ALIASES,
  LIMITS,
  parseDeepLink,
  linkFromArgv,
  hashAssignmentScript,
  makeDeepLinkInbox,
  makeWindowDeepLinkInbox,
  registerDeepLinks,
} = require("../deepLinks");

const ok = (raw) => {
  const r = parseDeepLink(raw);
  assert.equal(r.ok, true, `${raw} was refused: ${r.reason}`);
  return r.hash;
};
const refused = (raw) => {
  const r = parseDeepLink(raw);
  assert.equal(r.ok, false, `${JSON.stringify(raw)} was accepted as ${r.hash}`);
  assert.equal(typeof r.reason, "string");
  assert.equal(r.hash, undefined, "a refusal carries no hash");
  return r.reason;
};

/** A hash may only hold characters that are inert inside a quoted string and a URL. */
const SAFE_HASH = /^#\/[a-z][a-z-]*(\/[A-Za-z0-9%._~:@-]+)*(\?[A-Za-z0-9%._~*+=&-]+)?$/;

describe("parseDeepLink — the links the issue names", () => {
  it("maps each named link to its hash route", () => {
    assert.equal(ok("personalclaw://threads/new?prompt=draft%20this"), "#/chat/new?prompt=draft+this");
    assert.equal(ok("personalclaw://settings"), "#/settings");
    assert.equal(ok("personalclaw://settings/providers"), "#/settings/providers");
    assert.equal(ok("personalclaw://skills"), "#/skills");
    assert.equal(ok("personalclaw://automations"), "#/triggers");
  });

  it("carries a thread's project, mode, pane and review file into its hash", () => {
    const hash = ok(
      "personalclaw://threads/web:abc-123?project=p-1&mode=code&pane=activity&file=src%2Fapp%20main.py",
    );
    assert.equal(hash, "#/chat/web%3Aabc-123?project=p-1&mode=code&pane=activity&file=src%2Fapp+main.py");
    // And the page's own parser reads back exactly what the link said.
    const qs = new URLSearchParams(hash.split("?")[1]);
    assert.deepStrictEqual(Object.fromEntries(qs), {
      project: "p-1", mode: "code", pane: "activity", file: "src/app main.py",
    });
  });

  it("refuses an artifact key: an artifact opens on its own page, so the key would be ignored", () => {
    for (const raw of [
      "personalclaw://threads/k?artifact=report-2",
      "personalclaw://threads/new?artifact=report-2",
      "personalclaw://artifacts?artifact=report-2",
    ]) {
      assert.match(refused(raw), /a parameter the app does not take/, raw);
    }
    assert.equal(ok("personalclaw://artifacts/report-2"), "#/artifacts/report-2");
  });

  it("accepts every one of the app's routes by its own name", () => {
    assert.ok(ROUTES.size >= 20, "vacuity floor: the route list looks truncated");
    for (const route of ROUTES) assert.equal(ok(`${SCHEME}://${route}`), `#/${route}`);
  });

  it("takes the scheme in any case, a trailing slash after the route, and up to four segments", () => {
    assert.equal(ok("PersonalClaw://settings"), "#/settings");
    assert.equal(ok("PERSONALCLAW://skills"), "#/skills");
    assert.equal(ok("personalclaw://settings/"), "#/settings");
    assert.equal(ok("personalclaw://settings/a/b/c/d"), "#/settings/a/b/c/d");
  });

  it("decodes a segment once and writes it encoded into the hash", () => {
    assert.equal(ok("personalclaw://threads/web%3Aabc-123"), "#/chat/web%3Aabc-123");
    assert.equal(ok("personalclaw://threads/web:abc-123"), "#/chat/web%3Aabc-123");
    assert.equal(ok("personalclaw://threads/%61b"), "#/chat/ab");
  });

  it("reads a + in a value as a space and an encoded + as a plus, as the page will", () => {
    const spaced = ok("personalclaw://threads/new?prompt=draft+this");
    assert.equal(spaced, "#/chat/new?prompt=draft+this");
    assert.equal(new URLSearchParams(spaced.split("?")[1]).get("prompt"), "draft this");
    const plus = ok("personalclaw://threads/new?prompt=a%2Bb");
    assert.equal(plus, "#/chat/new?prompt=a%2Bb");
    assert.equal(new URLSearchParams(plus.split("?")[1]).get("prompt"), "a+b");
  });

  it("keeps the order of the parameters it was given, fixed: the hash is the same for the same link", () => {
    const a = ok("personalclaw://threads/k1?pane=review&project=p");
    const b = ok("personalclaw://threads/k1?project=p&pane=review");
    assert.equal(a, b);
  });

  it("every accepted hash holds only inert characters", () => {
    const samples = [
      "personalclaw://threads/new?prompt=%22)%3Balert(1)%3B(%22%20%3Cscript%3E%60%24%7Bx%7D%60%5C",
      "personalclaw://threads/k?file=src%2F%22%27%3C%3E%60.py",
      "personalclaw://settings/providers",
    ];
    for (const raw of samples) assert.match(ok(raw), SAFE_HASH, raw);
  });
});

describe("parseDeepLink — refusals (each opens the app without navigating)", () => {
  it("refuses anything that is not a string on the personalclaw scheme", () => {
    for (const raw of [undefined, null, 42, {}, "", "javascript:alert(1)", "http://settings", "file:///etc/passwd",
      "data:text/html,<script>alert(1)</script>", "personalclaws://settings", "personalclaw-x://settings"]) {
      refused(raw);
    }
  });

  it("refuses another scheme exactly as long as this one's, which no later rule would catch", () => {
    assert.equal("personalclax://".length, `${SCHEME}://`.length);
    assert.match(refused("personalclax://settings"), /personalclaw/);
  });

  it("refuses a link without the two slashes or without a route", () => {
    refused("personalclaw:settings");
    refused("personalclaw:///settings");
    refused("personalclaw://");
  });

  it("refuses an unknown route, a shell-only route and a route spelled in capitals", () => {
    assert.match(refused("personalclaw://nowhere"), /route/);
    refused("personalclaw://onboarding");
    refused("personalclaw://companion");
    refused("personalclaw://Settings");
  });

  it("refuses credentials, a port and a fragment", () => {
    refused("personalclaw://user:pw@settings");
    refused("personalclaw://user@settings");
    refused("personalclaw://settings:80");
    refused("personalclaw://settings#/chat");
    refused("personalclaw://settings/providers#x");
    assert.match(refused("personalclaw://threads/new?prompt=hi#send"), /fragment/);
    assert.match(refused("personalclaw://threads/k?file=a#b"), /fragment/);
  });

  it("refuses an encoded hash, query or path injection inside a segment", () => {
    refused("personalclaw://settings%23x");
    refused("personalclaw://settings/%23%2Fchat%3Fsend%3D1");
    refused("personalclaw://threads/a%2Fb");
    refused("personalclaw://threads/a%5Cb");
    refused("personalclaw://threads/..");
    refused("personalclaw://threads/.");
    refused("personalclaw://threads/%22)%3Balert(1)%3B(%22");
    refused("personalclaw://threads/a%00b");
  });

  it("refuses a segment that still holds a %, so a second decoding on the page cannot make structure", () => {
    // The page decodes a segment once more (a room id, for one), so `%252F` would become `/` there.
    assert.match(refused("personalclaw://threads/a%252Fb"), /character/);
    assert.match(refused("personalclaw://threads/room/a%252Fb"), /character/);
    assert.match(refused("personalclaw://threads/a%2523b"), /character/);
  });

  it("refuses an empty segment", () => {
    assert.match(refused("personalclaw://threads//x"), /segment is not one/);
  });

  it("refuses a segment that is . or .. once decoded", () => {
    for (const piece of ["%2E%2E", "%2e%2e", ".%2E", "%2E", "%2e"]) {
      assert.match(refused(`personalclaw://threads/${piece}`), /segment is not one/, piece);
    }
  });

  it("refuses a segment character that encodeURIComponent would leave raw in the hash", () => {
    for (const piece of ["a'(b)", "a*b", "a!b", "a'b", "a(b"]) {
      assert.match(refused(`personalclaw://threads/${piece}`), /character/, piece);
    }
  });

  it("refuses a segment that is not percent-encoded as one", () => {
    assert.match(refused("personalclaw://threads/%E0%A4%A"), /segment/);
    assert.match(refused("personalclaw://threads/%zz"), /segment/);
  });

  it("refuses too many segments and an over-long segment", () => {
    refused("personalclaw://settings/a/b/c/d/e");
    refused(`personalclaw://threads/${"a".repeat(LIMITS.segment + 1)}`);
    ok(`personalclaw://threads/${"a".repeat(LIMITS.segment)}`);
  });

  it("refuses a parameter it does not know, including every one that would act", () => {
    for (const key of ["send", "autosend", "submit", "approve", "answer", "run", "start", "confirm", "capture", "q", "embed"]) {
      assert.match(refused(`personalclaw://threads/new?${key}=1`), /parameter/, key);
    }
  });

  it("refuses a parameter given twice, or given with no value", () => {
    refused("personalclaw://threads/k?pane=review&pane=diff");
    refused("personalclaw://threads/k?pane=");
    refused("personalclaw://threads/k?pane");
    // A key with no "=" is refused as such, not read as a shorter key with the whole pair as its value.
    assert.match(refused("personalclaw://threads/k?panes"), /no value/);
    assert.match(refused("personalclaw://threads/k?modes"), /no value/);
    assert.match(refused("personalclaw://threads/k?=review"), /no value/);
    // An empty value is refused even where the parameter's own shape would take it.
    assert.match(refused("personalclaw://threads/k?file="), /no value/);
    assert.match(refused("personalclaw://threads/new?prompt="), /no value/);
  });

  it("refuses a parameter that is not percent-encoded as one", () => {
    assert.match(refused("personalclaw://threads/k?pane=%E0%A4%A"), /encoded/);
    assert.match(refused("personalclaw://threads/new?prompt=%zz"), /encoded/);
    assert.match(refused("personalclaw://threads/k?%zz=1"), /encoded/);
  });

  it("refuses a prompt anywhere but a new thread", () => {
    refused("personalclaw://threads/web:abc?prompt=hi");
    refused("personalclaw://settings?prompt=hi");
    refused("personalclaw://loop?prompt=hi");
    assert.match(refused("personalclaw://projects/new?prompt=hi"), /new thread/);
    assert.match(refused("personalclaw://threads/new/x?prompt=hi"), /new thread/);
    assert.match(refused("personalclaw://threads?prompt=hi"), /new thread/);
  });

  it("refuses an over-long prompt, and accepts one at the cap", () => {
    assert.match(refused(`personalclaw://threads/new?prompt=${"a".repeat(LIMITS.prompt + 1)}`), /prompt/);
    ok(`personalclaw://threads/new?prompt=${"a".repeat(LIMITS.prompt)}`);
  });

  it("refuses control and direction-override characters in a prompt, and keeps line breaks", () => {
    refused("personalclaw://threads/new?prompt=a%00b");
    refused("personalclaw://threads/new?prompt=a%1Bb");
    refused("personalclaw://threads/new?prompt=a%7Fb");
    refused("personalclaw://threads/new?prompt=a%C2%85b");
    refused("personalclaw://threads/new?prompt=a%E2%80%AEb");
    refused("personalclaw://threads/new?prompt=a%E2%81%A6b");
    refused("personalclaw://threads/new?prompt=a%0Bb");
    refused("personalclaw://threads/new?prompt=a%0Cb");
    assert.equal(ok("personalclaw://threads/new?prompt=a%09b%0Dc"), "#/chat/new?prompt=a%09b%0Dc");
    assert.equal(ok("personalclaw://threads/new?prompt=line%201%0Aline%202"), "#/chat/new?prompt=line+1%0Aline+2");
  });

  it("refuses a value outside its parameter's shape", () => {
    refused("personalclaw://threads/k?mode=Code");
    refused(`personalclaw://threads/k?mode=${"a".repeat(LIMITS.word + 1)}`);
    refused("personalclaw://threads/k?pane=re%20view");
    refused("personalclaw://threads/k?project=p%2F1");
    refused("personalclaw://threads/k?file=a%00b");
    refused(`personalclaw://threads/k?file=${"a".repeat(LIMITS.file + 1)}`);
  });

  it("holds each id and word to its own characters", () => {
    for (const [key, value] of [
      ["project", "a%40b"], ["project", "a~b"],
      ["mode", "1x"], ["mode", "-x"], ["pane", "9"], ["pane", "-review"],
    ]) {
      assert.match(refused(`personalclaw://threads/k?${key}=${value}`), new RegExp(`the ${key} parameter`), `${key}=${value}`);
    }
    assert.equal(ok("personalclaw://threads/k?mode=x1-y&project=P.1_a:b-c"), "#/chat/k?project=P.1_a%3Ab-c&mode=x1-y");
  });

  it("refuses each parameter one past its cap, and accepts it at the cap", () => {
    const caps = { project: LIMITS.id, mode: LIMITS.word, pane: LIMITS.word, file: LIMITS.file };
    for (const [key, cap] of Object.entries(caps)) {
      assert.match(refused(`personalclaw://threads/k?${key}=${"a".repeat(cap + 1)}`), new RegExp(key), key);
      ok(`personalclaw://threads/k?${key}=${"a".repeat(cap)}`);
    }
  });

  it("refuses control and direction-override characters in a file, tab and line breaks included", () => {
    for (const ch of ["%00", "%09", "%0A", "%0D", "%1F", "%7F", "%C2%85", "%E2%80%AE", "%E2%81%A6"]) {
      assert.match(refused(`personalclaw://threads/k?file=src%2Fa${ch}b.py`), /file/, ch);
    }
  });

  it("refuses a file that is not a path inside the thread's folder", () => {
    for (const file of ["%2Fetc%2Fpasswd", "~%2F.ssh%2Fid_rsa", "..%2Fsecrets", "src%2F..%2F..%2Fx", "src%2F..", "a%5Cb", "C%3A%2Fx"]) {
      assert.match(refused(`personalclaw://threads/k?file=${file}`), /file/, file);
    }
    assert.equal(ok("personalclaw://threads/k?file=src%2F.hidden%2Fa..b.py"), "#/chat/k?file=src%2F.hidden%2Fa..b.py");
  });

  it("refuses an over-long link before reading it", () => {
    assert.match(refused(`personalclaw://settings?pane=${"a".repeat(LIMITS.link)}`), /long/);
  });

  it("accepts a link of exactly the cap that every other rule takes, and refuses it one character longer", () => {
    // One CJK character is one UTF-16 code unit in the value and nine characters in the link, so
    // a prompt and a file each inside their own caps fill the link to exactly its cap.
    const cjk = "%E4%B8%80";
    const head = "personalclaw://threads/new?prompt=";
    const link = `${head}${cjk.repeat(LIMITS.prompt)}&file=${cjk.repeat((LIMITS.link - head.length - LIMITS.prompt * 9 - 6) / 9)}`;
    assert.equal(link.length, LIMITS.link);
    ok(link);
    assert.match(refused(`${link}a`), /long/);
  });
});

describe("parseDeepLink — a code cockpit's review state", () => {
  it("carries a cockpit's rail tab, open task and the one thing its center shows into its hash", () => {
    assert.equal(ok("personalclaw://code/u-1?pane=changes&diff=src%2Fa.py&task=t-1"), "#/code/u-1?pane=changes&diff=src%2Fa.py&task=t-1");
    assert.equal(ok("personalclaw://code/u-1?commit=0123abc"), "#/code/u-1?commit=0123abc");
    assert.equal(ok("personalclaw://code/u-1?commit=" + "a".repeat(40)), "#/code/u-1?commit=" + "a".repeat(40));
    assert.equal(ok("personalclaw://code/u-1?pane=files&file=src%2Fa.py"), "#/code/u-1?pane=files&file=src%2Fa.py");
    // And the page's own parser reads back exactly what the link said.
    const qs = new URLSearchParams(ok("personalclaw://code/u-1?task=t-1&diff=src%2Fa%20b.py").split("?")[1]);
    assert.deepStrictEqual(Object.fromEntries(qs), { diff: "src/a b.py", task: "t-1" });
  });

  it("refuses a diff, a commit or a task anywhere but one code cockpit", () => {
    for (const [key, value] of [["diff", "src%2Fa.py"], ["commit", "0123abc"], ["task", "t-1"]]) {
      for (const at of ["threads/k", "threads/new", "code", "code/history", "code/u-1/x", "files", "tasks", "loop"]) {
        assert.match(refused(`personalclaw://${at}?${key}=${value}`), /code cockpit/, `${at} ${key}`);
      }
    }
  });

  it("refuses a link that names more than one of file, diff and commit: the center shows one at a time", () => {
    for (const pair of [
      "file=src%2Fa.py&diff=src%2Fb.py",
      "diff=src%2Fb.py&file=src%2Fa.py",
      "file=src%2Fa.py&commit=0123abc",
      "commit=0123abc&file=src%2Fa.py",
      "diff=src%2Fb.py&commit=0123abc",
      "commit=0123abc&diff=src%2Fb.py",
      "file=src%2Fa.py&diff=src%2Fb.py&commit=0123abc",
    ]) {
      assert.match(refused(`personalclaw://code/u-1?${pair}`), /one of file, diff and commit/, pair);
    }
  });

  it("holds a commit to 7 to 40 lowercase hex characters", () => {
    for (const value of ["012345", "a".repeat(41), "0123ABC", "0123abg", "0123abc%20", "HEAD", "main", "-0123abc", "0123abc~1", "0123abc%5E"]) {
      assert.match(refused(`personalclaw://code/u-1?commit=${value}`), /the commit parameter/, value);
    }
    ok("personalclaw://code/u-1?commit=0123456");
  });

  it("holds a diff to the review file's own rules: a path inside the workspace", () => {
    for (const file of ["%2Fetc%2Fpasswd", "~%2F.ssh%2Fid_rsa", "..%2Fsecrets", "src%2F..%2F..%2Fx", "src%2F..", "a%5Cb", "C%3A%2Fx"]) {
      assert.match(refused(`personalclaw://code/u-1?diff=${file}`), /the diff parameter/, file);
    }
    // The same control and direction-override characters as a file, tab and line breaks included.
    for (const ch of ["%00", "%09", "%0A", "%0D", "%1F", "%7F", "%C2%85", "%E2%80%AE", "%E2%81%A6"]) {
      assert.match(refused(`personalclaw://code/u-1?diff=src%2Fa${ch}b.py`), /the diff parameter/, ch);
    }
    assert.match(refused(`personalclaw://code/u-1?diff=${"a".repeat(LIMITS.file + 1)}`), /the diff parameter/);
    ok(`personalclaw://code/u-1?diff=${"a".repeat(LIMITS.file)}`);
  });

  it("holds a task to an id's own characters and cap", () => {
    for (const value of ["a%40b", "a~b", "a%2Fb", "a%20b", "a".repeat(LIMITS.id + 1)]) {
      assert.match(refused(`personalclaw://code/u-1?task=${value}`), /the task parameter/, value);
    }
    ok(`personalclaw://code/u-1?task=${"a".repeat(LIMITS.id)}`);
  });

  it("refuses a diff, commit or task given twice, or with no value", () => {
    for (const key of ["diff", "commit", "task"]) {
      refused(`personalclaw://code/u-1?${key}=0123abc&${key}=0123abd`);
      assert.match(refused(`personalclaw://code/u-1?${key}=`), /no value/, key);
    }
  });

  it("every accepted cockpit hash holds only inert characters", () => {
    for (const raw of [
      "personalclaw://code/u-1?diff=src%2F%22%27%3C%3E%60.py&task=t-1",
      "personalclaw://code/u-1?commit=0123abc&pane=changes",
    ]) assert.match(ok(raw), SAFE_HASH, raw);
  });
});

describe("hashAssignmentScript — the hash reaches the page as data", () => {
  it("assigns exactly the hash, and nothing in it runs", () => {
    const hostile = parseDeepLink("personalclaw://threads/new?prompt=%22%3Bwindow.ran%3D1%3B%22%5Cu0022");
    assert.equal(hostile.ok, true);
    const sandbox = { window: { location: { hash: "" } } };
    vm.runInNewContext(hashAssignmentScript(hostile.hash), sandbox);
    assert.equal(sandbox.window.location.hash, hostile.hash);
    assert.equal(sandbox.window.ran, undefined);
    assert.deepStrictEqual(Object.keys(sandbox.window), ["location"]);
  });

  it("accepts every hash the menu-bar item writes, so the tray's links still open", () => {
    const { DEEP_LINKS } = require("../trayPresence");
    const hashes = [
      DEEP_LINKS.dashboard, DEEP_LINKS.approvals, DEEP_LINKS.inbox, DEEP_LINKS.settings,
      `${DEEP_LINKS.inbox}?capture=1`, DEEP_LINKS.loop("loop 1/(x)*!'"),
    ];
    for (const hash of hashes) assert.doesNotThrow(() => hashAssignmentScript(hash), hash);
  });

  it("refuses to build a script for a hash that is not one", () => {
    assert.throws(() => hashAssignmentScript("javascript:alert(1)"));
    assert.throws(() => hashAssignmentScript('#/chat";alert(1);"'));
    assert.throws(() => hashAssignmentScript(42));
  });

  it("holds every part of the hash to the grammar: its start, its route, its segments and its query", () => {
    // Each character sits where the grammar would otherwise take the hash, so each row fails on
    // one rule alone.
    const rows = [
      "x#/chat", " #/chat", "#/Chat", "#/chat\n", "#/",
      '#/chat/a"b', "#/chat/a\\b", "#/chat/a b", "#/chat/a;b", "#/chat/a<b", "#/chat/a#b", "#/chat//a",
      '#/chat?q="x', "#/chat?q=a\\b", "#/chat?a b", "#/chat?q=a;b", "#/chat?q=a<b", "#/chat?q=a#b", "#/chat?",
    ];
    for (const hash of rows) assert.throws(() => hashAssignmentScript(hash), /not a hash route/, JSON.stringify(hash));
  });

  it("takes only a string, not an object that turns into one", () => {
    assert.throws(() => hashAssignmentScript({ toString: () => "#/chat" }), /not a hash route/);
    assert.throws(() => hashAssignmentScript(new String("#/chat")), /not a hash route/);
  });

  it("quotes the hash as a JSON string literal, not by pasting it between quotes", () => {
    // The grammar keeps out every character the two would quote differently, so only the
    // writer's own text can show which one it does.
    assert.match(String(hashAssignmentScript), /window\.location\.hash = \$\{JSON\.stringify\(hash\)\};/);
  });
});

describe("linkFromArgv — the link a second launch hands over", () => {
  it("takes the first personalclaw: argument and nothing else", () => {
    assert.equal(linkFromArgv(["/app", "--flag", "personalclaw://settings", "personalclaw://skills"]), "personalclaw://settings");
    assert.equal(linkFromArgv(["/app", "PersonalClaw://settings"]), "PersonalClaw://settings");
    assert.equal(linkFromArgv(["/app", "."]), null);
    assert.equal(linkFromArgv(null), null);
  });

  it("skips an argument that only starts with the scheme's name, such as the Linux binary's", () => {
    assert.equal(linkFromArgv(["personalclaw-desktop", "personalclaw://settings"]), "personalclaw://settings");
    assert.equal(linkFromArgv(["personalclaw", "--no-sandbox", "personalclaw://skills"]), "personalclaw://skills");
  });
});

describe("makeDeepLinkInbox — a link waits for the dashboard, and only the newest waits", () => {
  function harness({ ready = false } = {}) {
    const calls = { delivered: [], shown: 0, logs: [] };
    let pageReady = ready;
    const inbox = makeDeepLinkInbox({
      canDeliver: () => pageReady,
      deliver: (hash) => calls.delivered.push(hash),
      show: () => (calls.shown += 1),
      log: (m) => calls.logs.push(m),
    });
    return { inbox, calls, setReady: (v) => (pageReady = v) };
  }

  it("delivers at once when the dashboard is up, and brings the window forward", () => {
    const { inbox, calls } = harness({ ready: true });
    assert.equal(inbox.receive("personalclaw://settings"), true);
    assert.deepStrictEqual(calls.delivered, ["#/settings"]);
    assert.equal(calls.shown, 1);
  });

  it("holds a link until the dashboard loads, then delivers the newest one only once", () => {
    const { inbox, calls, setReady } = harness();
    inbox.receive("personalclaw://settings");
    inbox.receive("personalclaw://skills");
    assert.deepStrictEqual(calls.delivered, []);
    setReady(true);
    inbox.flush();
    inbox.flush();
    assert.deepStrictEqual(calls.delivered, ["#/skills"]);
  });

  it("a refused link opens the app without navigating, drops nothing held, and logs no link text", () => {
    const { inbox, calls, setReady } = harness();
    inbox.receive("personalclaw://settings");
    assert.equal(inbox.receive("personalclaw://threads/new?prompt=my%20secret&send=1"), false);
    assert.equal(calls.shown, 2);
    setReady(true);
    inbox.flush();
    assert.deepStrictEqual(calls.delivered, ["#/settings"]);
    assert.ok(calls.logs.length >= 1);
    assert.ok(calls.logs.every((m) => !/secret|prompt=/.test(m)), "a refusal names its reason, never the link");
  });

  it("an accepted link logs no link text either", () => {
    const { inbox, calls } = harness({ ready: true });
    inbox.receive("personalclaw://threads/new?prompt=my%20secret");
    assert.ok(calls.logs.every((m) => !/secret/.test(m)));
  });
});

describe("makeWindowDeepLinkInbox — a link goes only to the dashboard of the gateway the window shows", () => {
  const GATEWAY = "http://127.0.0.1:8123";

  function harness({ url = `${GATEWAY}/`, loading = false, gateway = GATEWAY, contents = true } = {}) {
    const page = {
      url,
      loading,
      destroyed: false,
      scripts: [],
      getURL: () => page.url,
      isLoading: () => page.loading,
      isDestroyed: () => page.destroyed,
      executeJavaScript: (script) => {
        page.scripts.push(script);
        return Promise.resolve();
      },
    };
    const state = { gateway, contents };
    const logs = [];
    const inbox = makeWindowDeepLinkInbox({
      contents: () => (state.contents ? page : null),
      gatewayUrl: () => state.gateway,
      show: () => {},
      log: (m) => logs.push(m),
    });
    return { inbox, page, state, logs };
  }

  it("delivers to a loaded dashboard page on the gateway, through the one hash writer", () => {
    const { inbox, page } = harness();
    inbox.receive("personalclaw://settings/providers");
    assert.deepStrictEqual(page.scripts, [hashAssignmentScript("#/settings/providers")]);
  });

  it("holds a link while the window shows the loading page, then delivers it once on the dashboard", () => {
    const { inbox, page } = harness({ url: "file:///app/loading.html" });
    inbox.receive("personalclaw://skills");
    inbox.flush();
    assert.deepStrictEqual(page.scripts, []);
    page.url = `${GATEWAY}/`;
    inbox.flush();
    inbox.flush();
    assert.deepStrictEqual(page.scripts, [hashAssignmentScript("#/skills")]);
  });

  it("never delivers to the gateway switcher, which is a local page too", () => {
    const { inbox, page } = harness({ url: "file:///app/connectDialog.html" });
    inbox.receive("personalclaw://skills");
    assert.deepStrictEqual(page.scripts, []);
  });

  it("holds a link while the gateway's page is still loading", () => {
    const { inbox, page } = harness({ loading: true });
    inbox.receive("personalclaw://skills");
    assert.deepStrictEqual(page.scripts, []);
    page.loading = false;
    inbox.flush();
    assert.deepStrictEqual(page.scripts, [hashAssignmentScript("#/skills")]);
  });

  it("never delivers to a page on another origin than the gateway's", () => {
    for (const url of [
      "http://127.0.0.1:8124/", "https://127.0.0.1:8123/", "http://localhost:8123/", "about:blank", "data:text/html,x",
      `blob:${GATEWAY}/3f1c`, `blob:${GATEWAY}/`,
    ]) {
      const { inbox, page } = harness({ url });
      inbox.receive("personalclaw://skills");
      assert.deepStrictEqual(page.scripts, [], url);
    }
  });

  it("delivers only to the dashboard, the gateway's root page: never to another page of the gateway", () => {
    for (const url of [`${GATEWAY}/login`, `${GATEWAY}/login?next=%2F`, `${GATEWAY}/pair?code=x`, `${GATEWAY}/api/health`, `${GATEWAY}/claw.svg`]) {
      const { inbox, page } = harness({ url });
      inbox.receive("personalclaw://skills");
      inbox.flush();
      assert.deepStrictEqual(page.scripts, [], url);
      // The link waits for the dashboard, and goes once it has loaded.
      page.url = `${GATEWAY}/?from=login#/chat`;
      inbox.flush();
      inbox.flush();
      assert.deepStrictEqual(page.scripts, [hashAssignmentScript("#/skills")], url);
    }
  });

  it("finds the dashboard at the root whether or not the gateway's address ends in a slash", () => {
    for (const [gateway, url] of [[GATEWAY, `${GATEWAY}/`], [`${GATEWAY}/`, `${GATEWAY}/#/chat`]]) {
      const { inbox, page } = harness({ gateway, url });
      inbox.receive("personalclaw://skills");
      assert.deepStrictEqual(page.scripts, [hashAssignmentScript("#/skills")], `${gateway} ${url}`);
    }
  });

  it("a page that refuses the script leaves no unhandled rejection behind", async () => {
    const seen = [];
    const onRejection = (err) => seen.push(err);
    process.on("unhandledRejection", onRejection);
    try {
      const { inbox, page } = harness();
      page.executeJavaScript = () => Promise.reject(new Error("the page navigated away"));
      assert.equal(inbox.receive("personalclaw://skills"), true);
      await new Promise((resolve) => setImmediate(resolve));
      await new Promise((resolve) => setImmediate(resolve));
    } finally {
      process.off("unhandledRejection", onRejection);
    }
    assert.deepStrictEqual(seen, []);
  });

  it("a page that cannot take a script at all is logged, and the link text is not", () => {
    const { inbox, page, logs } = harness();
    page.executeJavaScript = () => {
      throw new Error("the page is gone");
    };
    assert.doesNotThrow(() => inbox.receive("personalclaw://threads/new?prompt=my%20secret"));
    assert.ok(logs.some((m) => /not delivered/.test(m)), logs.join(" | "));
    assert.ok(logs.every((m) => !/secret/.test(m)));
  });

  it("holds a link when there is no window, a closed one, or no gateway yet", () => {
    for (const setup of [
      { contents: false },
      { gateway: null },
      { gateway: null, url: "about:blank" },
      { gateway: "file:///app/loading.html", url: "file:///app/loading.html" },
      { gateway: "file:///", url: "file:///" },
      { gateway: "about:blank", url: "about:blank" },
      { gateway: "ftp://127.0.0.1:8123/", url: "ftp://127.0.0.1:8123/" },
    ]) {
      const { inbox, page } = harness(setup);
      inbox.receive("personalclaw://skills");
      assert.deepStrictEqual(page.scripts, [], JSON.stringify(setup));
    }
    const { inbox, page } = harness();
    page.destroyed = true;
    inbox.receive("personalclaw://skills");
    assert.deepStrictEqual(page.scripts, []);
  });
});

describe("registerDeepLinks — the wiring a start makes", () => {
  function fakeApp() {
    const calls = { protocol: [], handlers: new Map() };
    return {
      calls,
      setAsDefaultProtocolClient: (...args) => {
        calls.protocol.push(args);
        return true;
      },
      on: (evt, fn) => calls.handlers.set(evt, fn),
    };
  }
  function fakeInbox() {
    const got = [];
    const inbox = { got, flushes: 0, receive: (raw) => got.push(raw), flush: () => (inbox.flushes += 1) };
    return inbox;
  }
  function fakeContents() {
    const listeners = [];
    return { listeners, on: (evt, fn) => listeners.push([evt, fn]) };
  }

  it("a packaged app claims the scheme and listens for open-url and second-instance", () => {
    const app = fakeApp();
    const inbox = fakeInbox();
    registerDeepLinks({ app, inbox, isPackaged: true, argv: ["/Applications/PersonalClaw"] });
    assert.deepStrictEqual(app.calls.protocol, [["personalclaw"]]);
    assert.ok(app.calls.handlers.has("open-url"));
    assert.ok(app.calls.handlers.has("second-instance"));
    assert.deepStrictEqual(inbox.got, []);
  });

  it("a development run does not claim the scheme from the installed app", () => {
    const app = fakeApp();
    const logs = [];
    registerDeepLinks({ app, inbox: fakeInbox(), isPackaged: false, argv: ["electron", "."], log: (m) => logs.push(m) });
    assert.deepStrictEqual(app.calls.protocol, []);
    assert.ok(logs.length >= 1);
  });

  it("a link opened at a development run still reaches it, by each of the three ways in", () => {
    const app = fakeApp();
    const inbox = fakeInbox();
    registerDeepLinks({ app, inbox, isPackaged: false, argv: ["electron", ".", "personalclaw://automations"] });
    app.calls.handlers.get("open-url")({ preventDefault: () => {} }, "personalclaw://skills");
    app.calls.handlers.get("second-instance")({}, ["electron", ".", "personalclaw://settings"], "/");
    assert.deepStrictEqual(inbox.got, ["personalclaw://automations", "personalclaw://skills", "personalclaw://settings"]);
  });

  it("offers a held link again each time a page finishes loading, in any window or tab", () => {
    const app = fakeApp();
    const inbox = fakeInbox();
    registerDeepLinks({ app, inbox, isPackaged: true, argv: [] });
    const created = app.calls.handlers.get("web-contents-created");
    assert.equal(typeof created, "function", "registerDeepLinks must watch for new web contents");
    const tab = fakeContents();
    created({}, tab);
    assert.deepStrictEqual(tab.listeners.map(([evt]) => evt), ["did-finish-load"]);
    assert.equal(inbox.flushes, 0);
    tab.listeners[0][1]();
    tab.listeners[0][1]();
    assert.equal(inbox.flushes, 2);
  });

  it("open-url takes the event and hands the link to the inbox", () => {
    const app = fakeApp();
    const inbox = fakeInbox();
    registerDeepLinks({ app, inbox, isPackaged: true, argv: [] });
    let prevented = 0;
    app.calls.handlers.get("open-url")({ preventDefault: () => (prevented += 1) }, "personalclaw://skills");
    assert.equal(prevented, 1);
    assert.deepStrictEqual(inbox.got, ["personalclaw://skills"]);
  });

  it("second-instance hands over the link in its argv, and ignores an argv with none", () => {
    const app = fakeApp();
    const inbox = fakeInbox();
    registerDeepLinks({ app, inbox, isPackaged: true, argv: [] });
    const handler = app.calls.handlers.get("second-instance");
    handler({}, ["/app", "personalclaw://settings"], "/");
    handler({}, ["/app"], "/");
    assert.deepStrictEqual(inbox.got, ["personalclaw://settings"]);
  });

  it("a first launch by a link (its argv) is handed over too", () => {
    const inbox = fakeInbox();
    registerDeepLinks({ app: fakeApp(), inbox, isPackaged: true, argv: ["/app", "personalclaw://automations"] });
    assert.deepStrictEqual(inbox.got, ["personalclaw://automations"]);
  });
});

describe("the route list is the app's own", () => {
  const app = fs.readFileSync(path.join(__dirname, "..", "..", "web", "src", "app", "App.tsx"), "utf8");

  it("equals the routes the dashboard renders, without its shell-only routes", () => {
    const nav = app.match(/const NAV: NavItem\[\] = \[([\s\S]*?)\n\]/);
    assert.ok(nav, "the NAV list must be findable");
    const navIds = [...nav[1].matchAll(/\{ id: '([a-z-]+)'/g)].map((m) => m[1]);
    const extra = app.match(/const ROUTABLE = new Set\(\[\.\.\.NAV\.map\(\(n\) => n\.id\), ([^\]]+)\]\)/);
    assert.ok(extra, "the ROUTABLE set must be findable");
    const extras = [...extra[1].matchAll(/'([a-z-]+)'/g)].map((m) => m[1]);
    assert.ok(navIds.length >= 10 && extras.length >= 3, "vacuity floor");
    assert.deepStrictEqual([...ROUTES].sort(), [...new Set([...navIds, ...extras])].sort());
  });

  const shell = app.match(/const SHELL_ROUTES = new Set\(\[([^\]]+)\]\)/);
  const shellRoutes = shell ? [...shell[1].matchAll(/'([a-z-]+)'/g)].map((m) => m[1]) : [];

  it("refuses each of the dashboard's shell-only routes", () => {
    assert.ok(shellRoutes.length >= 2, "the SHELL_ROUTES set must be findable");
    for (const route of shellRoutes) {
      assert.ok(!ROUTES.has(route), route);
      assert.match(refused(`${SCHEME}://${route}`), /route/, route);
    }
  });

  it("is the list the desktop guide gives, aliases and refused shell pages included", () => {
    const guide = fs.readFileSync(path.join(__dirname, "..", "..", "docs", "guides", "desktop.md"), "utf8");
    const bullet = guide.match(/^- \*\*The page\*\*[\s\S]*?(?=\n- \*\*|\n\n)/m);
    assert.ok(bullet, "the guide's page bullet must be findable");
    // The bullet names the pages a link opens, then, from its sentence on the shell's own pages,
    // the ones it refuses.
    const at = bullet[0].indexOf("shell");
    assert.ok(at > 0, "the guide must say which shell pages a link is refused");
    const names = (text) => [...new Set([...text.matchAll(/`([a-z][a-z-]*)`/g)].map((m) => m[1]))].sort();
    assert.deepStrictEqual(names(bullet[0].slice(0, at)), [...ROUTES, ...Object.keys(ALIASES)].sort(), "every route a link takes, and no other");
    assert.deepStrictEqual(names(bullet[0].slice(at)), [...shellRoutes].sort(), "the shell pages a link is refused");
    assert.match(bullet[0].slice(at), /refused/);
  });
});

describe("main.js wires the links (loaded under Electron stand-ins)", () => {
  const { loadMainWithStubs } = require("./helpers/stubbedMain");
  const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  const code = main.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
  const opened = (route) => `desktop: deep link opens the ${route} route`;

  it("hands the first launch's link to the inbox at load, before the app is ready", () => {
    // `whenReady()` never settles under the stand-ins, so whatever happens here happens before ready.
    // A packaged app's argv is its binary, then the link.
    const { logs } = loadMainWithStubs({ isPackaged: true, argv: [process.execPath, "personalclaw://settings"] });
    assert.ok(logs.includes(opened("settings")), logs.join(" | "));
    const dev = loadMainWithStubs({ argv: [process.execPath, ".", "personalclaw://skills"] });
    assert.ok(dev.logs.includes(opened("skills")), dev.logs.join(" | "));
  });

  it("listens for open-url and second-instance before the app is ready, into the same inbox", () => {
    const { listeners, logs, fire } = loadMainWithStubs();
    const names = listeners.map(([evt]) => evt);
    assert.ok(names.includes("open-url"), names.join(", "));
    let prevented = 0;
    fire("open-url", { preventDefault: () => (prevented += 1) }, "personalclaw://skills");
    assert.equal(prevented, 1);
    assert.ok(logs.includes(opened("skills")), logs.join(" | "));
    fire("second-instance", {}, ["/app", "personalclaw://automations"], "/");
    assert.ok(logs.includes(opened("triggers")), logs.join(" | "));
  });

  it("offers a held link again when a page finishes loading", () => {
    const { listeners, fire } = loadMainWithStubs();
    assert.ok(listeners.some(([evt]) => evt === "web-contents-created"));
    const added = [];
    fire("web-contents-created", {}, { on: (evt, fn) => added.push([evt, fn]) });
    assert.deepStrictEqual(added.map(([evt]) => evt), ["did-finish-load"]);
  });

  it("claims the scheme only in a packaged app", () => {
    const dev = loadMainWithStubs({ isPackaged: false });
    assert.deepStrictEqual(dev.calls.filter(([c]) => c === "setAsDefaultProtocolClient"), []);
    const packaged = loadMainWithStubs({ isPackaged: true });
    assert.deepStrictEqual(packaged.calls.filter(([c]) => c === "setAsDefaultProtocolClient"), [["setAsDefaultProtocolClient", SCHEME]]);
  });

  it("wires nothing for links when another instance holds the lock: that instance takes them", () => {
    const { listeners, calls, logs } = loadMainWithStubs({ lock: false, isPackaged: true, argv: [process.execPath, "personalclaw://settings"] });
    assert.ok(calls.some(([c]) => c === "quit"));
    assert.deepStrictEqual(listeners.filter(([evt]) => evt === "open-url" || evt === "second-instance"), []);
    assert.deepStrictEqual(calls.filter(([c]) => c === "setAsDefaultProtocolClient"), []);
    assert.ok(!logs.includes(opened("settings")));
  });

  it("delivers through the one hash writer, which quotes the hash as data", () => {
    assert.match(code, /executeJavaScript\(hashAssignmentScript\(hash\)\)/);
    assert.doesNotMatch(code, /window\.location\.hash = \$\{/);
  });

  it("writes no hash of its own: the hash writer lives in deepLinks.js alone", () => {
    assert.doesNotMatch(code, /location\.hash/);
    assert.match(code, /wc\.executeJavaScript\(hashAssignmentScript\(hash\)\)/, "the menu-bar item goes through the writer too");
  });

  it("points the inbox at the active tab, on the gateway the window is pointed at", () => {
    assert.match(code, /makeWindowDeepLinkInbox\(\{/);
    assert.match(code, /contents: \(\) => \(mainWindow && !mainWindow\.isDestroyed\(\) \? mainWindow\.webContents : null\)/);
    assert.match(code, /gatewayUrl: \(\) => activeUrl \|\| localGatewayUrl/);
    assert.match(code, /show: \(\) => showMainWindow\(\)/);
  });
});
