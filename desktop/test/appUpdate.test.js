/**
 * The app's own update (`appUpdate.js`), every step against fakes: the network is a fake `request`
 * that writes down what it was asked, the programs the update runs (hdiutil, codesign, lipo,
 * ditto, PlistBuddy) are a fake `run`, and the app's folder is a real temporary one, so the swap's
 * renames are real.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { Readable } = require("node:stream");

const {
  ATTEMPT_KEY,
  BUNDLE_ID,
  CHECK_EVERY_MS,
  FIRST_CHECK_AFTER_MS,
  RELEASES_API,
  USER_AGENT,
  chooseAsset,
  chooseRelease,
  compareVersions,
  downloadAsset,
  fetchReleases,
  freePrevious,
  isPrerelease,
  makeAppUpdater,
  parseVersion,
  readUpdatePolicy,
  registerAppUpdateIpc,
  stageMac,
  swapNow,
  whereItRuns,
} = require("../appUpdate");

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-app-update-"));

/** A release record as GitHub's API answers it, with its copies for every computer. */
function release(tag, { prerelease = false, draft = false, body = Buffer.from(`the ${tag} copy`) } = {}) {
  const version = tag.replace(/^v/, "");
  const asset = (name) => ({
    name,
    size: body.length,
    digest: `sha256:${crypto.createHash("sha256").update(body).digest("hex")}`,
    browser_download_url: `https://github.com/PersonalClaw/PersonalClaw/releases/download/${tag}/${name}`,
  });
  return {
    tag_name: tag,
    prerelease,
    draft,
    html_url: `https://github.com/PersonalClaw/PersonalClaw/releases/tag/${tag}`,
    assets: [asset(`PersonalClaw-${version}-arm64.dmg`), asset(`PersonalClaw-${version}-x64.dmg`), asset(`PersonalClaw-${version}.AppImage`)],
  };
}

/**
 * A fake `https.request`: *answer(url)* is `{status, headers, body}` for each address asked for.
 * Every request is written down with its headers.
 */
function fakeRequest(answer) {
  const seen = [];
  const request = (url, options, cb) => {
    const asked = { url: String(url), headers: { ...(options.headers || {}) }, agent: options.agent };
    seen.push(asked);
    const handlers = {};
    const req = {
      setTimeout: () => req,
      on: (ev, fn) => ((handlers[ev] = fn), req),
      destroy: (err) => handlers.error && handlers.error(err || new Error("destroyed")),
      end: () =>
        setImmediate(() => {
          const { status = 200, headers = {}, body = "" } = answer(String(url)) || { status: 404 };
          const res = Readable.from(body === null ? [] : [Buffer.isBuffer(body) ? body : Buffer.from(String(body))]);
          res.statusCode = status;
          res.headers = headers;
          cb(res);
        }),
    };
    return req;
  };
  return { request, seen };
}

describe("versions, in the order PersonalClaw ships them", () => {
  it("reads a release, a tag's v and -rc.N, a candidate, a dev build and a post-release", () => {
    for (const v of ["0.3.7", "v0.3.7", "v0.3.8-rc.1", "0.3.8rc1", "0.3.7.dev1", "0.3.7.post1"]) assert.ok(parseVersion(v), v);
    for (const v of ["", "0.3", "latest", "0.3.7 and more", "x".repeat(80)]) assert.equal(parseVersion(v), null, v);
  });

  it("orders them as PEP 440 does: dev < candidate < release < post, and numbers as numbers", () => {
    assert.equal(compareVersions("0.3.7.dev1", "0.3.7rc1"), -1);
    assert.equal(compareVersions("v0.3.8-rc.1", "0.3.8"), -1);
    assert.equal(compareVersions("0.3.8rc1", "v0.3.8-rc.1"), 0);
    assert.equal(compareVersions("0.3.7.post1", "0.3.7"), 1);
    assert.equal(compareVersions("0.3.10", "0.3.9"), 1);
    assert.equal(compareVersions("0.3.7", "nonsense"), null);
    assert.equal(isPrerelease("v0.3.8-rc.1"), true);
    assert.equal(isPrerelease("0.3.8"), false);
  });
});

describe("chooseRelease: what this copy is offered", () => {
  const list = [release("v0.3.9-rc.1", { prerelease: true }), release("v0.3.8"), release("v0.3.7"), release("v0.4.0", { draft: true })];

  it("offers the newest full release newer than this copy, never a draft or a candidate on stable", () => {
    assert.equal(chooseRelease(list, { current: "0.3.6" }).release.tag_name, "v0.3.8");
  });

  it("offers a candidate on the beta channel", () => {
    assert.equal(chooseRelease(list, { current: "0.3.6", channel: "beta" }).release.tag_name, "v0.3.9-rc.1");
  });

  it("offers nothing to a copy that is current, and says nothing about it", () => {
    assert.deepEqual(chooseRelease(list, { current: "0.3.8" }), { release: null, why: "current" });
  });

  it("follows a pin exactly when it is newer, and never moves this copy back to an older pin", () => {
    assert.equal(chooseRelease(list, { current: "0.3.6", pin: "0.3.7" }).release.tag_name, "v0.3.7");
    assert.deepEqual(chooseRelease(list, { current: "0.3.8", pin: "0.3.7" }), { release: null, why: "pin_older" });
    assert.deepEqual(chooseRelease(list, { current: "0.3.7", pin: "0.3.7" }), { release: null, why: "current" });
    assert.deepEqual(chooseRelease(list, { current: "0.3.6", pin: "0.3.5" }), { release: null, why: "pin_missing" });
  });
});

describe("chooseAsset: exactly this computer's copy, checkable before anything is fetched", () => {
  it("names the dmg for this Mac's architecture and the AppImage", () => {
    const r = release("v0.3.8");
    assert.equal(chooseAsset(r, { platform: "darwin", arch: "arm64" }).asset.name, "PersonalClaw-0.3.8-arm64.dmg");
    assert.equal(chooseAsset(r, { platform: "darwin", arch: "x64" }).asset.name, "PersonalClaw-0.3.8-x64.dmg");
    assert.equal(chooseAsset(r, { platform: "linux", arch: "x64" }).asset.name, "PersonalClaw-0.3.8.AppImage");
    assert.deepEqual(chooseAsset(r, { platform: "win32", arch: "x64" }), { ok: false, why: "no_asset" });
  });

  it("uses no copy without GitHub's sha256 digest, a size, or at an address other than the one GitHub publishes", () => {
    const pick = (mutate) => {
      const r = release("v0.3.8");
      mutate(r.assets[0]);
      return chooseAsset(r, { platform: "darwin", arch: "arm64" });
    };
    assert.deepEqual(pick((a) => delete a.digest), { ok: false, why: "unchecked" });
    assert.deepEqual(pick((a) => (a.digest = "md5:abc")), { ok: false, why: "unchecked" });
    assert.deepEqual(pick((a) => (a.size = 0)), { ok: false, why: "unchecked" });
    assert.deepEqual(pick((a) => (a.size = 2 * 1024 ** 3)), { ok: false, why: "unchecked" });
    assert.deepEqual(pick((a) => (a.browser_download_url = "https://elsewhere.invalid/PersonalClaw-0.3.8-arm64.dmg")), { ok: false, why: "unchecked" });
  });
});

describe("whereItRuns: whether this copy can be replaced here", () => {
  const app = () => {
    const dir = tmp();
    const bundle = path.join(dir, "PersonalClaw.app");
    fs.mkdirSync(path.join(bundle, "Contents", "MacOS"), { recursive: true });
    return { dir, bundle, execPath: path.join(bundle, "Contents", "MacOS", "PersonalClaw") };
  };

  it("replaces a PersonalClaw.app this account can write", () => {
    const { bundle, execPath } = app();
    assert.deepEqual(whereItRuns({ platform: "darwin", isPackaged: true, execPath }), { how: "mac", bundle });
  });

  it("asks a copy on the disk image or a copy macOS moved aside to be moved to Applications first", () => {
    for (const execPath of ["/Volumes/PersonalClaw/PersonalClaw.app/Contents/MacOS/PersonalClaw", "/private/var/folders/x/AppTranslocation/ABC/d/PersonalClaw.app/Contents/MacOS/PersonalClaw"]) {
      assert.equal(whereItRuns({ platform: "darwin", isPackaged: true, execPath, access: () => {} }).why, "move");
    }
  });

  it("sends a copy it cannot write, a renamed one, a package and a development run to the releases page", () => {
    const { execPath } = app();
    const refused = () => {
      throw new Error("EACCES");
    };
    assert.equal(whereItRuns({ platform: "darwin", isPackaged: true, execPath, access: refused }).why, "not_writable");
    assert.equal(whereItRuns({ platform: "darwin", isPackaged: true, execPath: "/Applications/Other.app/Contents/MacOS/Other", access: () => {} }).why, "renamed");
    assert.deepEqual(whereItRuns({ platform: "linux", isPackaged: true, execPath: "/opt/PersonalClaw/personalclaw" }), { how: "page", why: "package" });
    assert.equal(whereItRuns({ platform: "darwin", isPackaged: false, execPath }).how, "dev");
  });

  it("replaces an AppImage this account can write", () => {
    const dir = tmp();
    const file = path.join(dir, "PersonalClaw-0.3.7.AppImage");
    fs.writeFileSync(file, "old");
    assert.deepEqual(whereItRuns({ platform: "linux", isPackaged: true, execPath: "/tmp/.mount_x/personalclaw", appImage: file }), { how: "appimage", file });
  });
});

describe("fetchReleases: the release list, read and nothing more", () => {
  it("reads the list from GitHub's API with no credential", async () => {
    const { request, seen } = fakeRequest(() => ({ status: 200, body: JSON.stringify([release("v0.3.8")]) }));
    const list = await fetchReleases({ request, userAgent: USER_AGENT });
    assert.equal(list[0].tag_name, "v0.3.8");
    assert.equal(seen.length, 1);
    assert.equal(seen[0].url, RELEASES_API);
    assert.equal(seen[0].headers["User-Agent"], "personalclaw-update-check");
    for (const h of ["Authorization", "authorization", "Cookie", "cookie"]) assert.equal(h in seen[0].headers, false);
  });

  it("follows no redirect, not even on GitHub's API, and says the list could not be read", async () => {
    for (const location of ["https://api.github.com/repos/PersonalClaw/PersonalClaw/releases?page=2", "https://elsewhere.invalid/list"]) {
      const { request, seen } = fakeRequest((url) => (url === RELEASES_API ? { status: 302, headers: { location } } : { status: 200, body: "[]" }));
      await assert.rejects(() => fetchReleases({ request, userAgent: "x" }), (err) => err.why === "unreachable", location);
      assert.equal(seen.length, 1, `a redirect to ${location} was followed`);
    }
  });
});

describe("downloadAsset: GitHub's release hosts only, and only the bytes the release states", () => {
  const body = Buffer.from("the 0.3.8 copy");
  const asset = () => chooseAsset(release("v0.3.8", { body }), { platform: "darwin", arch: "arm64" }).asset;

  it("follows GitHub's redirect to its asset host and keeps a download whose size and digest match", async () => {
    const dir = tmp();
    const { request, seen } = fakeRequest((url) =>
      url.startsWith("https://github.com/") ? { status: 302, headers: { location: "https://release-assets.githubusercontent.com/x?sig=1" } } : { status: 200, body },
    );
    const file = await downloadAsset(asset(), path.join(dir, "PersonalClaw-0.3.8-arm64.dmg"), { request, userAgent: "x" });
    assert.deepEqual(fs.readFileSync(file), body);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    assert.deepEqual(seen.map((s) => new URL(s.url).hostname), ["github.com", "release-assets.githubusercontent.com"]);
    for (const s of seen) assert.equal("Authorization" in s.headers || "authorization" in s.headers, false);
  });

  it("sends nothing to a host that is not GitHub's, nor over plain http, nor past five redirects", async () => {
    for (const location of ["https://elsewhere.invalid/x", "http://release-assets.githubusercontent.com/x"]) {
      const dir = tmp();
      const file = path.join(dir, "f.dmg");
      const { request, seen } = fakeRequest((url) => (url.startsWith("https://github.com/") ? { status: 302, headers: { location } } : { status: 200, body }));
      await assert.rejects(() => downloadAsset(asset(), file, { request, userAgent: "x" }), (err) => err.why === "download");
      assert.equal(seen.length, 1, `${location} was asked`);
      assert.equal(fs.existsSync(file), false);
    }
    const { request, seen } = fakeRequest(() => ({ status: 302, headers: { location: "https://github.com/again" } }));
    await assert.rejects(() => downloadAsset(asset(), path.join(tmp(), "f.dmg"), { request, userAgent: "x" }));
    assert.equal(seen.length, 6, "more than five redirects were followed");
  });

  it("uses nothing whose bytes differ from the release's, and leaves no partial file", async () => {
    for (const served of [Buffer.from("the 0.3.8 cop!"), Buffer.from("the 0.3.8 copy and then some")]) {
      const file = path.join(tmp(), "f.dmg");
      const { request } = fakeRequest(() => ({ status: 200, body: served }));
      await assert.rejects(() => downloadAsset(asset(), file, { request, userAgent: "x" }), (err) => err.why === "mismatch");
      assert.equal(fs.existsSync(file), false);
    }
  });
});

/** A fake of the programs the update runs on macOS, and what each was asked. */
function fakeMacTools({ id = BUNDLE_ID, version = "0.3.8", archs = "x86_64 arm64", apps = ["PersonalClaw.app"], codesign = () => {} } = {}) {
  const calls = [];
  const run = async (cmd, args) => {
    calls.push([path.basename(cmd), ...args]);
    const tool = path.basename(cmd);
    if (tool === "hdiutil" && args[0] === "attach") {
      const mount = args[args.indexOf("-mountpoint") + 1];
      for (const name of apps) {
        fs.mkdirSync(path.join(mount, name, "Contents", "MacOS"), { recursive: true });
        fs.writeFileSync(path.join(mount, name, "Contents", "Info.plist"), "plist");
        fs.writeFileSync(path.join(mount, name, "Contents", "MacOS", "PersonalClaw"), `binary ${version}`);
      }
      return "";
    }
    if (tool === "hdiutil" && args[0] === "detach") {
      for (const name of fs.readdirSync(args[1])) fs.rmSync(path.join(args[1], name), { recursive: true, force: true });
      return "";
    }
    if (tool === "PlistBuddy") return args[1].includes("CFBundleIdentifier") ? `${id}\n` : `${version}\n`;
    if (tool === "codesign") return codesign(args[args.length - 1]) || "";
    if (tool === "lipo") return `${archs}\n`;
    if (tool === "ditto") {
      fs.cpSync(args[0], args[1], { recursive: true });
      return "";
    }
    throw new Error(`unexpected ${cmd}`);
  };
  return { run, calls };
}

/** An installed PersonalClaw.app 0.3.7 in a folder of its own, with a downloaded dmg beside it. */
function installed() {
  const root = tmp();
  const folder = path.join(root, "Applications");
  const bundle = path.join(folder, "PersonalClaw.app");
  fs.mkdirSync(path.join(bundle, "Contents", "MacOS"), { recursive: true });
  fs.writeFileSync(path.join(bundle, "Contents", "MacOS", "PersonalClaw"), "binary 0.3.7");
  const work = path.join(root, "work");
  fs.mkdirSync(work);
  const dmg = path.join(work, "PersonalClaw-0.3.8-arm64.dmg");
  fs.writeFileSync(dmg, "image");
  return { root, folder, bundle, work, dmg, execPath: path.join(bundle, "Contents", "MacOS", "PersonalClaw") };
}

describe("stageMac: the app inside the image, checked, copied beside this one", () => {
  it("attaches the image read-only and without a window, checks the app, and copies it beside this one", async () => {
    const at = installed();
    const { run, calls } = fakeMacTools();
    const incoming = await stageMac({ dmg: at.dmg, bundle: at.bundle, version: "0.3.8", arch: "arm64", run, workDir: at.work });
    assert.equal(incoming, path.join(at.folder, ".PersonalClaw-0.3.8.app.incoming"));
    assert.equal(fs.readFileSync(path.join(incoming, "Contents", "MacOS", "PersonalClaw"), "utf8"), "binary 0.3.8");
    const attach = calls.find((c) => c[0] === "hdiutil" && c[1] === "attach");
    for (const flag of ["-readonly", "-nobrowse", "-noautoopen"]) assert.ok(attach.includes(flag), flag);
    assert.equal(calls.filter((c) => c[0] === "codesign").length, 2, "the seal is checked in the image and again in the copy");
    assert.ok(calls.some((c) => c[0] === "hdiutil" && c[1] === "detach"));
    assert.equal(fs.readFileSync(at.execPath, "utf8"), "binary 0.3.7", "the running app was touched");
  });

  it("uses nothing from an image whose app is not this one, not this version, not sealed, or not for this Mac", async () => {
    const cases = [
      { id: "io.example.other" },
      { version: "0.3.9" },
      { archs: "x86_64" },
      { apps: ["PersonalClaw.app", "Helper.app"] },
      { apps: ["Other.app"] },
      { codesign: () => { throw new Error("invalid signature"); } },
    ];
    for (const tools of cases) {
      const at = installed();
      const { run, calls } = fakeMacTools(tools);
      await assert.rejects(() => stageMac({ dmg: at.dmg, bundle: at.bundle, version: "0.3.8", arch: "arm64", run, workDir: at.work }), (err) => err.why === "contents", JSON.stringify(tools));
      assert.equal(fs.existsSync(path.join(at.folder, ".PersonalClaw-0.3.8.app.incoming")), false, JSON.stringify(tools));
      assert.ok(calls.some((c) => c[0] === "hdiutil" && c[1] === "detach"), "the image stays attached");
    }
  });
});

describe("swapNow: two renames, the second undone by the first's reverse", () => {
  it("moves the app aside and the new copy into its place", () => {
    const at = installed();
    const incoming = path.join(at.folder, ".PersonalClaw-0.3.8.app.incoming");
    fs.cpSync(at.bundle, incoming, { recursive: true });
    fs.writeFileSync(path.join(incoming, "Contents", "MacOS", "PersonalClaw"), "binary 0.3.8");
    const previous = freePrevious(at.bundle, "0.3.7", "app");
    swapNow({ current: at.bundle, incoming, previous });
    assert.equal(fs.readFileSync(at.execPath, "utf8"), "binary 0.3.8");
    assert.equal(fs.readFileSync(path.join(previous, "Contents", "MacOS", "PersonalClaw"), "utf8"), "binary 0.3.7");
  });

  it("keeps an old copy no new one cleared under its name, and takes the next name", () => {
    const at = installed();
    const left = path.join(at.folder, ".PersonalClaw-0.3.7.app.previous");
    fs.mkdirSync(left);
    assert.equal(freePrevious(at.bundle, "0.3.7", "app"), path.join(at.folder, ".PersonalClaw-0.3.7-2.app.previous"));
  });

  it("puts the app back when the new copy cannot take its place", () => {
    const at = installed();
    const previous = path.join(at.folder, ".PersonalClaw-0.3.7.app.previous");
    assert.throws(() => swapNow({ current: at.bundle, incoming: path.join(at.folder, ".missing"), previous }), (err) => err.why === "put_in_place");
    assert.equal(fs.readFileSync(at.execPath, "utf8"), "binary 0.3.7");
    assert.equal(fs.existsSync(previous), false);
  });
});

describe("readUpdatePolicy: the home's updates settings, read each time, failing closed", () => {
  const home = (text) => {
    const dir = tmp();
    if (text !== undefined) fs.writeFileSync(path.join(dir, "config.json"), text);
    return dir;
  };

  it("follows the channel, beta its own line and every other one stable, and the pin only for the app's own gateway", () => {
    const cases = [
      [{ channel: "beta", pin: " 0.3.8 ", check_enabled: false }, true, { channel: "beta", pin: "0.3.8", checkEnabled: false }],
      [{ channel: "beta", pin: "0.3.8" }, false, { channel: "beta", pin: "", checkEnabled: true }],
      [{ channel: "nightly" }, true, { channel: "stable", pin: "", checkEnabled: true }],
      [{ channel: 7, pin: 0.38 }, true, { channel: "stable", pin: "", checkEnabled: true }],
    ];
    for (const [updates, ownGateway, want] of cases) {
      assert.deepEqual(readUpdatePolicy({ home: home(JSON.stringify({ updates })), ownGateway }), want, JSON.stringify(updates));
    }
  });

  it("takes the defaults for no file, an empty one, or a block that is not one, as the gateway does", () => {
    const defaults = { channel: "stable", pin: "", checkEnabled: true };
    for (const text of [undefined, "", "  \n", JSON.stringify({ updates: "beta" }), JSON.stringify({ updates: ["beta"] }), "[]"]) {
      assert.deepEqual(readUpdatePolicy({ home: home(text), ownGateway: true }), defaults, String(text));
    }
  });

  it("offers nothing from a file it cannot read or parse: the pin it may hold is a limit", () => {
    const broken = home("{ not json");
    assert.deepEqual(readUpdatePolicy({ home: broken, ownGateway: true }), { unreadable: path.join(broken, "config.json") });
    const denied = home();
    const readFile = () => {
      const err = new Error("EACCES");
      err.code = "EACCES";
      throw err;
    };
    assert.deepEqual(readUpdatePolicy({ home: denied, ownGateway: true, readFile }), { unreadable: path.join(denied, "config.json") });
  });
});

describe("makeAppUpdater: a check on launch and daily; an update only on a press", () => {
  /** A shell store in memory, as `shellStore.js` keeps it: strings by key. */
  const memoryStore = (seed = {}) => {
    const data = { ...seed };
    return {
      data,
      getItem: (k) => (Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null),
      setItem: (k, v) => { data[k] = String(v); },
      removeItem: (k) => { delete data[k]; },
    };
  };
  function updater({ list = [release("v0.3.8")], policy = { channel: "stable", pin: "", checkEnabled: true }, tools, current = "0.3.7", at = installed(), store = memoryStore(), ownsGateway } = {}) {
    const timers = [];
    const states = [];
    const relaunched = [];
    const quits = [];
    const trashed = [];
    const body = Buffer.from("the v0.3.8 copy");
    const { request, seen } = fakeRequest((url) =>
      url === RELEASES_API ? { status: 200, body: JSON.stringify(list) } : url.startsWith("https://github.com/") ? { status: 302, headers: { location: "https://objects.githubusercontent.com/x" } } : { status: 200, body },
    );
    const mac = tools || fakeMacTools();
    const made = makeAppUpdater({
      current,
      platform: "darwin",
      arch: "arm64",
      isPackaged: true,
      execPath: at.execPath,
      policy: () => policy,
      workDir: at.work,
      run: mac.run,
      quit: () => quits.push(Date.now()),
      relaunch: (program) => relaunched.push(program),
      trash: (p) => {
        trashed.push(p);
        fs.renameSync(p, path.join(at.root, `trashed-${path.basename(p)}`));
      },
      request,
      store,
      ownsGateway,
      onState: (s) => states.push(s),
      setTimer: (fn, ms) => (timers.push({ fn, ms }), timers.length),
      clearTimer: () => {},
    });
    return { ...made, at, timers, states, relaunched, quits, trashed, seen, mac, store };
  }
  const settle = (u) => new Promise((resolve) => {
    const wait = () => (["restarting", "failed"].includes(u.status().phase) ? resolve() : setTimeout(wait, 5));
    wait();
  });

  it("checks a minute after it starts and then once a day, and only reads the release list", async () => {
    const u = updater();
    u.start();
    assert.deepEqual(u.timers.map((t) => t.ms), [FIRST_CHECK_AFTER_MS]);
    u.timers[0].fn();
    await new Promise((r) => setTimeout(r, 30));
    assert.equal(u.status().phase, "available");
    assert.deepEqual(u.status().available, { version: "0.3.8", installable: true });
    assert.deepEqual(u.timers.map((t) => t.ms), [FIRST_CHECK_AFTER_MS, CHECK_EVERY_MS]);
    assert.deepEqual(u.seen.map((s) => s.url), [RELEASES_API], "something but the list was fetched before a press");
    assert.deepEqual([u.quits, u.relaunched], [[], []]);
  });

  it("makes no check of its own with automatic checks off, and still checks when asked", async () => {
    const u = updater({ policy: { channel: "stable", pin: "", checkEnabled: false } });
    u.start();
    u.timers[0].fn();
    await new Promise((r) => setTimeout(r, 30));
    assert.equal(u.seen.length, 0);
    await u.check({ asked: true });
    assert.equal(u.seen.length, 1);
    assert.equal(u.status().phase, "available");
  });

  it("asks GitHub nothing while the home's settings cannot be read, and says so", async () => {
    const u = updater({ policy: { unreadable: "/home/config.json" } });
    await u.check({ asked: true });
    assert.equal(u.seen.length, 0);
    assert.equal(u.status().phase, "failed");
    assert.match(u.status().sentence, /couldn't read \/home\/config\.json, so this app doesn't know which release it follows/);
  });

  it("follows the pin it is given, and says one older than this copy instead of offering it", async () => {
    const list = [release("v0.3.8"), release("v0.3.6")];
    const pinned = updater({ list, policy: { channel: "stable", pin: "0.3.6", checkEnabled: true } });
    await pinned.check({ asked: true });
    assert.equal(pinned.status().phase, "failed");
    assert.match(pinned.status().sentence, /updates\.pin names 0\.3\.6, older than this app \(0\.3\.7\)\. The app doesn't roll back/);
  });

  it("says whether the restart for an update stops the home's gateway", () => {
    let own = true;
    const u = updater({ ownsGateway: () => own });
    assert.equal(u.status().restartsGateway, true);
    own = false;
    assert.equal(u.status().restartsGateway, false);
  });

  it("updates only on a press, to the version on offer, and quits; the quit's last step swaps, then asks for the start", async () => {
    const u = updater();
    await u.check({ asked: true });
    assert.deepEqual(u.install("0.3.9"), { ok: false, sentence: "That version is not the one on offer. Check again." });
    assert.deepEqual(u.install("0.3.8"), { ok: true });
    await settle(u);
    assert.equal(u.status().phase, "restarting", u.status().sentence);
    assert.equal(u.quits.length, 1);
    assert.deepEqual(u.relaunched, [], "a start was asked for before the swap said which copy to start");
    assert.ok(u.states.some((s) => s.phase === "downloading") && u.states.some((s) => s.phase === "verifying"));
    assert.equal(fs.readFileSync(u.at.execPath, "utf8"), "binary 0.3.7", "the copy was replaced before the quit");
    assert.deepEqual(JSON.parse(u.store.data[ATTEMPT_KEY]), { from: "0.3.7", to: "0.3.8" });
    assert.deepEqual(u.install("0.3.8"), { ok: false, sentence: "An update is already under way." });

    // The quit's last step.
    assert.equal(u.swapIfStaged(), true);
    assert.equal(fs.readFileSync(u.at.execPath, "utf8"), "binary 0.3.8");
    assert.deepEqual(u.relaunched, [u.at.execPath]);
    assert.ok(fs.existsSync(path.join(u.at.folder, ".PersonalClaw-0.3.7.app.previous")), "the old copy went before the new one started");
    assert.equal(u.swapIfStaged(), false, "a second quit swaps again");
    assert.equal(u.relaunched.length, 1);

    // The new copy's start says how it ended, once.
    const next = updater({ current: "0.3.8", at: u.at, store: u.store });
    assert.deepEqual(next.start(), { from: "0.3.7", to: "0.3.8", ok: true, sentence: "Updated from 0.3.7 to 0.3.8." });
    assert.equal(next.status().last.sentence, "Updated from 0.3.7 to 0.3.8.");
    assert.equal(ATTEMPT_KEY in next.store.data, false);
    assert.deepEqual(updater({ current: "0.3.8", at: u.at, store: u.store }).start(), null);
  });

  it("starts this copy again when the new one cannot take its place, and that start says so", async () => {
    const u = updater();
    await u.check({ asked: true });
    u.install("0.3.8");
    await settle(u);
    const realRename = fs.renameSync;
    fs.renameSync = () => {
      throw Object.assign(new Error("EPERM"), { code: "EPERM" });
    };
    try {
      assert.equal(u.swapIfStaged(), false);
    } finally {
      fs.renameSync = realRename;
    }
    assert.deepEqual(u.relaunched, [u.at.execPath]);
    assert.equal(fs.readFileSync(u.at.execPath, "utf8"), "binary 0.3.7");
    assert.equal(fs.existsSync(path.join(u.at.folder, ".PersonalClaw-0.3.8.app.incoming")), false, "the staged copy was left");
    const again = updater({ at: u.at, store: u.store });
    const last = again.start();
    assert.equal(last.ok, false);
    assert.equal(last.sentence, "The new version couldn't be put in place. This copy is unchanged; install it from the releases page.");
  });

  it("starts the old copy where it was left when it cannot be put back, and says where", async () => {
    const u = updater();
    await u.check({ asked: true });
    u.install("0.3.8");
    await settle(u);
    const realRename = fs.renameSync;
    let renames = 0;
    fs.renameSync = (from, to) => {
      renames += 1;
      if (renames === 1) return realRename(from, to);
      throw Object.assign(new Error("EPERM"), { code: "EPERM" });
    };
    try {
      assert.equal(u.swapIfStaged(), false);
    } finally {
      fs.renameSync = realRename;
    }
    const left = path.join(u.at.folder, ".PersonalClaw-0.3.7.app.previous");
    assert.deepEqual(u.relaunched, [path.join(left, "Contents", "MacOS", "PersonalClaw")]);
    const last = updater({ at: installed(), store: u.store }).start();
    assert.equal(last.sentence, `The new version couldn't be put in place, and this copy couldn't be moved back: it is at ${left}. Rename it to PersonalClaw.app.`);
  });

  it("moves the old copy to the Trash only when asked, once the new one is up, and nothing else", async () => {
    const u = updater();
    const previous = path.join(u.at.folder, ".PersonalClaw-0.3.6.app.previous");
    const abandoned = path.join(u.at.folder, ".PersonalClaw-0.3.9.app.incoming");
    const other = path.join(u.at.folder, "Other.app");
    for (const p of [previous, abandoned, other]) fs.mkdirSync(p);
    u.start();
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(u.trashed, [], "the start alone cleared the old copy");
    u.trashPrevious();
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(u.trashed.sort(), [abandoned, previous].sort());
    assert.ok(fs.existsSync(other));
  });

  it("says a failed update in one sentence, leaves this copy as it was, and stages nothing", async () => {
    const u = updater({ tools: fakeMacTools({ id: "io.example.other" }) });
    await u.check({ asked: true });
    u.install("0.3.8");
    await settle(u);
    assert.equal(u.status().phase, "failed");
    assert.match(u.status().sentence, /didn't hold the expected app, so nothing was used/);
    assert.deepEqual([u.quits, u.relaunched], [[], []]);
    assert.equal(ATTEMPT_KEY in u.store.data, false);
    assert.equal(u.swapIfStaged(), false);
    assert.equal(fs.readFileSync(u.at.execPath, "utf8"), "binary 0.3.7");
    assert.equal(u.install("0.3.9").sentence, "That version is not the one on offer. Check again.", "a failed update holds the updater");
  });

  it("offers the releases page, not a press, where this copy cannot be replaced", async () => {
    const at = installed();
    const { request } = fakeRequest(() => ({ status: 200, body: JSON.stringify([release("v0.3.8")]) }));
    const u = makeAppUpdater({
      current: "0.3.7", platform: "darwin", arch: "arm64", isPackaged: true, execPath: at.execPath,
      policy: () => ({ channel: "stable", pin: "", checkEnabled: true }), workDir: at.work, run: async () => "",
      quit: () => assert.fail("quit"), relaunch: () => assert.fail("relaunched"), trash: () => {}, request, setTimer: () => 0, clearTimer: () => {},
      access: () => { throw new Error("EACCES"); },
    });
    await u.check({ asked: true });
    assert.deepEqual(u.status().available, { version: "0.3.8", installable: false });
    assert.match(u.status().sentence, /This account can't replace PersonalClaw/);
    assert.equal(u.install("0.3.8").ok, false);
  });
});

describe("registerAppUpdateIpc: the app's own windows ask, and nothing else", () => {
  const channels = { appUpdateStatus: "s", appUpdateCheck: "c", appUpdateInstall: "i" };
  function wired(isAppWindow) {
    const handlers = new Map();
    const asked = [];
    const updater = { status: () => "state", check: (o) => (asked.push(["check", o]), "checked"), install: (v) => (asked.push(["install", v]), { ok: true }) };
    registerAppUpdateIpc({ ipcMain: { handle: (ch, fn) => handlers.set(ch, fn) }, channels, updater: () => updater, isAppWindow });
    return { handlers, asked };
  }

  it("answers the app's bridged windows, with the version their confirm named", async () => {
    const { handlers, asked } = wired(() => true);
    assert.deepEqual([...handlers.keys()].sort(), ["c", "i", "s"]);
    assert.equal(await handlers.get("s")({ sender: {} }), "state");
    assert.equal(await handlers.get("c")({ sender: {} }), "checked");
    await handlers.get("i")({ sender: {} }, "0.3.8");
    await handlers.get("i")({ sender: {} }, { version: "0.3.8" });
    assert.deepEqual(asked, [["check", { asked: true }], ["install", "0.3.8"], ["install", ""]]);
  });

  it("refuses a page that is not one of the app's windows", () => {
    const { handlers, asked } = wired(() => false);
    for (const ch of ["s", "c", "i"]) assert.throws(() => handlers.get(ch)({ sender: {} }, "0.3.8"), /not one of the app's windows/);
    assert.deepEqual(asked, []);
  });
});


describe("main.js: the update is asked for in the app menu, and the swap is the quit's last step", () => {
  const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  const body = (name) => {
    const start = MAIN.indexOf(`function ${name}(`);
    assert.ok(start > 0, `${name} is gone — this rail is now blind`);
    return MAIN.slice(start, MAIN.indexOf("\n}\n", start));
  };

  it("offers Check for Updates… in the app menu on macOS and under Help elsewhere, and both check now", () => {
    assert.match(body("appMenuTemplate"), /\{ role: "about" \},\n\s+\{ label: "Check for Updates…", click: \(\) => checkForAppUpdates\(\) \}/);
    assert.match(body("helpMenuTemplate"), /label: "Check for Updates…", click: \(\) => checkForAppUpdates\(\)/);
    assert.match(MAIN, /Menu\.buildFromTemplate\(\[\n\s+\.\.\.appMenuTemplate\(\),/);
    assert.match(MAIN, /\.\.\.helpMenuTemplate\(\),\n\s+\]\);/);
    assert.match(body("checkForAppUpdates"), /check\(\{ asked: true \}\)/);
  });

  it("swaps a staged copy only once everything the app ran has stopped", () => {
    const start = MAIN.indexOf('app.on("before-quit"');
    const quit = MAIN.slice(start, MAIN.indexOf("\n});\n", start));
    const finale = quit.slice(quit.indexOf(".finally("));
    assert.ok(quit.indexOf("stop: (budget) => stopGateway(budget)") < quit.indexOf(".finally("), "the gateway's stop is not before the swap");
    assert.ok(finale.indexOf("appUpdater?.swapIfStaged();") > 0, "the quit's last step does not swap");
    assert.ok(finale.indexOf("appUpdater?.swapIfStaged();") < finale.indexOf("shutdownComplete = true;"), "the swap is not before the quit lets go");
    assert.match(quit, /appUpdater\?\.stop\(\);/, "the daily check outlives the quit");
  });

  it("asks to be started again from the updater only, and answers only the app's bridged windows", () => {
    assert.equal([...MAIN.matchAll(/app\.relaunch\(/g)].length, 1);
    assert.match(body("makeThisAppsUpdater"), /relaunch: \(program\) =>\n\s+app\.relaunch\(\{ execPath: program,/);
    const ipc = MAIN.slice(MAIN.indexOf("registerAppUpdateIpc({"), MAIN.indexOf("});", MAIN.indexOf("registerAppUpdateIpc({")));
    assert.match(ipc, /isAppWindow: \(sender\) => BaseWindow\.getAllWindows\(\)\.some\(\(w\) => w\._pcBridgeAttached === true && w\.webContents === sender\)/);
  });

  it("follows the pin only while the home's gateway is not one the app found running, and trashes the old copy once the new one has its gateway", () => {
    assert.match(body("makeThisAppsUpdater"), /readUpdatePolicy\(\{ home: PERSONALCLAW_HOME, ownGateway: !\["adopted", "served"\]\.includes\(localState\.state\) \}\)/);
    assert.match(body("startAppUpdater"), /if \(localState\.state === "own" \|\| localState\.state === "adopted"\) appUpdater\.trashPrevious\(\);/);
    const ready = MAIN.slice(MAIN.indexOf("app.whenReady().then("));
    assert.ok(ready.indexOf("await bringUpLocalGateway();") < ready.indexOf("startAppUpdater();"), "the updater starts before the gateway is up");
  });
});
