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
  placeMac,
  prepareMac,
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

  it("stops a download that is called off, and leaves no partial file", async () => {
    const dir = tmp();
    const body = Buffer.alloc(64 * 1024, 7);
    const asset = { url: "https://github.com/PersonalClaw/PersonalClaw/releases/download/v0.3.8/PersonalClaw-0.3.8.AppImage", size: body.length * 2, sha256: crypto.createHash("sha256").update(Buffer.concat([body, body])).digest("hex") };
    let chunks = 0;
    const request = (url, options, cb) => {
      const handlers = {};
      const req = { setTimeout: () => req, on: (ev, fn) => ((handlers[ev] = fn), req), destroy: () => {}, end: () => setImmediate(() => {
        const res = Readable.from([body, body]);
        res.statusCode = 200;
        res.headers = {};
        cb(res);
      }) };
      return req;
    };
    const file = path.join(dir, "PersonalClaw-0.3.8.AppImage");
    await assert.rejects(() => downloadAsset(asset, file, { request, userAgent: USER_AGENT, onProgress: () => (chunks += 1), cancelled: () => chunks >= 1 }), (err) => err.why === "download");
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(fs.existsSync(file), false, "the partial file was left");
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

/** The CDHash the fake codesign gives a bundle: its executable's bytes, hashed, so a copy whose
 *  build changed has another one. */
const cdhashOf = (app) => crypto.createHash("sha1").update(fs.readFileSync(path.join(app, "Contents", "MacOS", "PersonalClaw"))).digest("hex");

/** A fake of the programs the update runs on macOS, and what each was asked. Its codesign writes
 *  what `-d -vvv` shows to stderr only, as codesign does, and holds `-R '=cdhash H"…"'` to the
 *  bundle's CDHash. */
function fakeMacTools({ id = BUNDLE_ID, version = "0.3.8", archs = "x86_64 arm64", apps = ["PersonalClaw.app"], codesign = () => {}, ditto = () => {}, shows = (app) => `CDHash=${cdhashOf(app)}`, attach = async () => {}, detach = () => {} } = {}) {
  const calls = [];
  const run = async (cmd, args, opts = {}) => {
    calls.push([path.basename(cmd), ...args]);
    const tool = path.basename(cmd);
    if (tool === "hdiutil" && args[0] === "attach") {
      await attach();
      const mount = args[args.indexOf("-mountpoint") + 1];
      for (const name of apps) {
        fs.mkdirSync(path.join(mount, name, "Contents", "MacOS"), { recursive: true });
        fs.writeFileSync(path.join(mount, name, "Contents", "Info.plist"), "plist");
        fs.writeFileSync(path.join(mount, name, "Contents", "MacOS", "PersonalClaw"), `binary ${version}`);
      }
      return "";
    }
    if (tool === "hdiutil" && args[0] === "detach") {
      detach(args[1]);
      for (const name of fs.readdirSync(args[1])) fs.rmSync(path.join(args[1], name), { recursive: true, force: true });
      return "";
    }
    if (tool === "PlistBuddy") return args[1].includes("CFBundleIdentifier") ? `${id}\n` : `${version}\n`;
    if (tool === "codesign" && args[0] === "-d") {
      const app = args[args.length - 1];
      return opts.stream === "stderr" ? `Executable=${app}/Contents/MacOS/PersonalClaw\n${shows(app)}\n` : "";
    }
    if (tool === "codesign") {
      const app = args[args.length - 1];
      codesign(app);
      const r = args.indexOf("-R");
      if (r >= 0 && args[r + 1] !== `=cdhash H"${cdhashOf(app)}"`) throw new Error("test-requirement: code failed to satisfy specified code requirement(s)");
      return "";
    }
    if (tool === "lipo") return `${archs}\n`;
    if (tool === "ditto") {
      ditto(args[0], args[1]);
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
  const image = { sha256: crypto.createHash("sha256").update("image").digest("hex"), size: "image".length };
  return { root, folder, bundle, work, dmg, image, execPath: path.join(bundle, "Contents", "MacOS", "PersonalClaw") };
}

describe("prepareMac: the app inside the image, checked, copied into the update's own folder", () => {
  it("attaches the image read-only and without a window, checks the app, and copies it out, touching nothing beside this one", async () => {
    const at = installed();
    const { run, calls } = fakeMacTools();
    const { path: ready, cdhash } = await prepareMac({ dmg: at.dmg, version: "0.3.8", arch: "arm64", run, workDir: at.work, ...at.image });
    assert.equal(ready, path.join(at.work, "PersonalClaw.app.ready"));
    assert.equal(cdhash, cdhashOf(ready), "the CDHash kept is the build the image held");
    assert.ok(!ready.endsWith(".app"), "a ready copy named .app is registered with LaunchServices, and a link can open it");
    assert.equal(fs.readFileSync(path.join(ready, "Contents", "MacOS", "PersonalClaw"), "utf8"), "binary 0.3.8");
    const attach = calls.find((c) => c[0] === "hdiutil" && c[1] === "attach");
    for (const flag of ["-readonly", "-nobrowse", "-noautoopen"]) assert.ok(attach.includes(flag), flag);
    // The seal is checked in the image, then again on the copy in the private folder, held there to
    // the build the image held.
    const seals = calls.filter((c) => c[0] === "codesign" && c[1] === "--verify");
    assert.equal(seals.length, 2);
    assert.match(path.basename(path.dirname(seals[0][seals[0].length - 1])), /^mount-/, "the first seal check is not on the image's app");
    assert.deepEqual(seals[1].slice(-3), ["-R", `=cdhash H"${cdhash}"`, ready], "the copy in the private folder is not checked, or not held to the image's build");
    // The CDHash is read from the image's app, before the copy is made.
    const shown = calls.findIndex((c) => c[0] === "codesign" && c[1] === "-d");
    assert.match(path.basename(path.dirname(calls[shown][calls[shown].length - 1])), /^mount-/, "the CDHash is not the image's app's");
    assert.ok(shown < calls.findIndex((c) => c[0] === "ditto"), "the CDHash is read after the copy");
    assert.ok(calls.some((c) => c[0] === "hdiutil" && c[1] === "detach"));
    assert.deepEqual(fs.readdirSync(at.folder), ["PersonalClaw.app"], "something was written beside the app");
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
      { shows: () => "" },
    ];
    for (const tools of cases) {
      const at = installed();
      const { run, calls } = fakeMacTools(tools);
      await assert.rejects(() => prepareMac({ dmg: at.dmg, version: "0.3.8", arch: "arm64", run, workDir: at.work, ...at.image }), (err) => err.why === "contents", JSON.stringify(tools));
      assert.equal(fs.existsSync(path.join(at.work, "PersonalClaw.app.ready")), false, JSON.stringify(tools));
      assert.deepEqual(fs.readdirSync(at.folder), ["PersonalClaw.app"], JSON.stringify(tools));
      assert.ok(calls.some((c) => c[0] === "hdiutil" && c[1] === "detach"), "the image stays attached");
    }
  });
});

describe("prepareMac: the image on disk, once attached, is the release's", () => {
  it("uses nothing from an image whose file differs from the one downloaded, and detaches it", async () => {
    const at = installed();
    const { run, calls } = fakeMacTools({ attach: async () => fs.writeFileSync(at.dmg, "another") });
    await assert.rejects(() => prepareMac({ dmg: at.dmg, version: "0.3.8", arch: "arm64", run, workDir: at.work, ...at.image }), (err) => err.why === "mismatch");
    assert.equal(fs.existsSync(path.join(at.work, "PersonalClaw.app.ready")), false);
    assert.ok(calls.some((c) => c[0] === "hdiutil" && c[1] === "detach"), "the image stays attached");
    assert.equal(calls.some((c) => c[0] === "ditto"), false, "a copy was made from an image that isn't the release's");
  });
});

describe("placeMac: Restart to finish puts the ready copy beside this one", () => {
  /** A ready copy of 0.3.8 in the work folder, and the CDHash the download kept for it. */
  const readyCopy = (at) => {
    const ready = path.join(at.work, "PersonalClaw.app.ready");
    fs.mkdirSync(path.join(ready, "Contents", "MacOS"), { recursive: true });
    fs.writeFileSync(path.join(ready, "Contents", "Info.plist"), "plist");
    fs.writeFileSync(path.join(ready, "Contents", "MacOS", "PersonalClaw"), "binary 0.3.8");
    return { ready, cdhash: cdhashOf(ready) };
  };

  it("copies it beside the app under its hidden name and checks it there as the image's app was, held to its build", async () => {
    const at = installed();
    const { ready, cdhash } = readyCopy(at);
    const { run, calls } = fakeMacTools();
    const incoming = await placeMac({ ready, bundle: at.bundle, version: "0.3.8", arch: "arm64", cdhash, run });
    assert.equal(incoming, path.join(at.folder, ".PersonalClaw-0.3.8.app.incoming"));
    assert.equal(fs.readFileSync(path.join(incoming, "Contents", "MacOS", "PersonalClaw"), "utf8"), "binary 0.3.8");
    assert.deepEqual(calls.filter((c) => c[0] === "codesign").map((c) => c.slice(-3)), [["-R", `=cdhash H"${cdhash}"`, incoming]]);
    assert.ok(calls.some((c) => c[0] === "PlistBuddy" && c[c.length - 1].startsWith(incoming)), "the bundle id and version are not read on the copy");
    assert.ok(calls.some((c) => c[0] === "lipo" && c[2].startsWith(incoming)), "the architecture is not read on the copy");
    assert.equal(fs.readFileSync(at.execPath, "utf8"), "binary 0.3.7");
  });

  it("removes a copy it could not make, or that is not the build that was checked, and fails as put_in_place", async () => {
    const cases = [
      { tools: { ditto: () => { throw new Error("EPERM"); } } },
      { tools: { codesign: () => { throw new Error("invalid signature"); } } },
      { tools: { id: "io.example.other" } },
      { tools: { version: "0.3.7" } },
      { tools: { archs: "x86_64" } },
      { cdhash: "0".repeat(40), why: "another build than the one the image held" },
      { cdhash: "", why: "no build to hold it to" },
    ];
    for (const { tools = {}, cdhash, why } of cases) {
      const at = installed();
      const made = readyCopy(at);
      const { run } = fakeMacTools(tools);
      await assert.rejects(() => placeMac({ ready: made.ready, bundle: at.bundle, version: "0.3.8", arch: "arm64", cdhash: cdhash ?? made.cdhash, run }), (err) => err.why === "put_in_place", why || JSON.stringify(tools));
      assert.deepEqual(fs.readdirSync(at.folder), ["PersonalClaw.app"], why || JSON.stringify(tools));
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

describe("makeAppUpdater: a check on launch and daily; an update only on a press, in two steps", () => {
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
  function updater({ list = [release("v0.3.8")], policy = { channel: "stable", pin: "", checkEnabled: true }, tools, current = "0.3.7", at = installed(), store = memoryStore(), ownsGateway, isMountPoint, listHold = () => null, listStatus = () => 200, getuid, runSync, onState: alsoOnState = () => {} } = {}) {
    const timers = [];
    const states = [];
    const relaunched = [];
    const quits = [];
    const trashed = [];
    const logs = [];
    const body = Buffer.from("the v0.3.8 copy");
    // The release list's *n*th answer: with *listStatus(n)*, once *listHold(n)* (a promise, or null) settles.
    let listed = 0;
    let asking = 0;
    const { request, seen } = fakeRequest((url) =>
      url === RELEASES_API ? { status: listStatus(asking), body: JSON.stringify(list) } : url.startsWith("https://github.com/") ? { status: 302, headers: { location: "https://objects.githubusercontent.com/x" } } : { status: 200, body },
    );
    const mac = tools || fakeMacTools();
    const slowly = (url, options, cb) => {
      if (String(url) !== RELEASES_API) return request(url, options, cb);
      const n = listed++;
      const hold = listHold(n);
      return request(url, options, (res) => (hold ? hold.then(() => cb(res)) : cb(res)));
    };
    // The status is chosen as the answer is made, which fakeRequest does as the request ends.
    const asked = (url, options, cb) => {
      const req = slowly(url, options, cb);
      const end = req.end;
      req.end = () => {
        if (String(url) === RELEASES_API) asking = listed - 1;
        end();
      };
      return req;
    };
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
      request: asked,
      store,
      ownsGateway,
      ...(isMountPoint ? { isMountPoint } : {}),
      ...(getuid ? { getuid } : {}),
      ...(runSync ? { runSync } : {}),
      onState: (s) => {
        states.push(s);
        alsoOnState(s);
      },
      log: (line) => logs.push(String(line)),
      setTimer: (fn, ms) => (timers.push({ fn, ms }), timers.length),
      clearTimer: () => {},
    });
    return { ...made, at, timers, states, relaunched, quits, trashed, seen, mac, store, logs };
  }
  const settle = (u) => new Promise((resolve) => {
    const wait = () => (["ready", "restarting", "failed"].includes(u.status().phase) ? resolve() : setTimeout(wait, 5));
    wait();
  });
  /** Wait for *cond*, for at most two seconds. */
  const until = async (cond) => {
    for (let i = 0; i < 400 && !cond(); i++) await new Promise((r) => setTimeout(r, 5));
    assert.ok(cond(), "waited two seconds for it");
  };
  /** The update's private folders under the work dir. */
  const privateFolders = (u) => fs.readdirSync(u.at.work).filter((n) => n.startsWith("app-update-"));
  /** Update, then Restart to finish, each to its end. */
  const updateAndFinish = async (u) => {
    await u.check({ asked: true });
    u.download("0.3.8");
    await settle(u);
    u.finish("0.3.8");
    await settle(u);
  };

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

  it("downloads and checks only on Update, to the version on offer, in a private folder, and writes nothing beside the app", async () => {
    const u = updater();
    await u.check({ asked: true });
    assert.deepEqual(u.download("0.3.9"), { ok: false, sentence: "That version is not the one on offer. Check again." });
    assert.deepEqual(u.finish("0.3.8"), { ok: false, sentence: "That version is not the one ready. Check again." }, "Restart to finish before anything is ready");
    assert.deepEqual(u.download("0.3.8"), { ok: true });
    assert.deepEqual(u.download("0.3.8"), { ok: false, sentence: "An update is already under way." });
    await settle(u);
    assert.equal(u.status().phase, "ready", u.status().sentence);
    assert.deepEqual(u.status().ready, { version: "0.3.8" });
    assert.equal(u.status().sentence, "PersonalClaw 0.3.8 is downloaded and checked. Restart to finish the update.");
    assert.ok(u.states.some((s) => s.phase === "downloading") && u.states.some((s) => s.phase === "verifying"));
    assert.deepEqual(fs.readdirSync(u.at.folder), ["PersonalClaw.app"], "something was written beside the app before Restart to finish");
    assert.equal(fs.readFileSync(u.at.execPath, "utf8"), "binary 0.3.7");
    assert.deepEqual([u.quits, u.relaunched], [[], []]);
    assert.equal(ATTEMPT_KEY in u.store.data, false);
    const [folder] = privateFolders(u);
    assert.equal(fs.statSync(path.join(u.at.work, folder)).mode & 0o777, 0o700);
    assert.deepEqual(fs.readdirSync(path.join(u.at.work, folder)), ["PersonalClaw.app.ready"], "the image was kept beside the ready copy");
    // While it waits, a check keeps the offer, and Update says Restart to finish is the way on.
    const listed = u.seen.length;
    await u.check({ asked: true });
    assert.equal(u.status().phase, "ready");
    assert.equal(u.seen.length, listed, "a ready update asked GitHub again");
    assert.match(u.download("0.3.8").sentence, /is ready\. Restart to finish the update\./);
  });

  it("puts the ready copy beside the app only on Restart to finish, then quits; the quit's last step swaps, then asks for the start", async () => {
    const u = updater();
    await u.check({ asked: true });
    u.download("0.3.8");
    await settle(u);
    assert.deepEqual(u.finish("0.3.9"), { ok: false, sentence: "That version is not the one ready. Check again." });
    assert.deepEqual(u.finish("0.3.8"), { ok: true });
    await settle(u);
    assert.equal(u.status().phase, "restarting", u.status().sentence);
    assert.ok(u.states.some((s) => s.phase === "placing"));
    assert.equal(u.quits.length, 1);
    assert.deepEqual(u.relaunched, [], "a start was asked for before the swap said which copy to start");
    assert.ok(fs.existsSync(path.join(u.at.folder, ".PersonalClaw-0.3.8.app.incoming")));
    assert.deepEqual(privateFolders(u), [], "the private folder outlived the placing");
    assert.equal(fs.readFileSync(u.at.execPath, "utf8"), "binary 0.3.7", "the copy was replaced before the quit");
    assert.deepEqual(JSON.parse(u.store.data[ATTEMPT_KEY]), { from: "0.3.7", to: "0.3.8" });
    assert.deepEqual(u.finish("0.3.8"), { ok: false, sentence: "An update is already under way." });
    assert.deepEqual(u.download("0.3.8"), { ok: false, sentence: "An update is already under way." });

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

  it("says a copy that cannot be put beside the app at Restart to finish, and leaves the app as it was", async () => {
    const u = updater({ tools: fakeMacTools({ ditto: (from, to) => { if (to.endsWith(".incoming")) throw new Error("EPERM"); } }) });
    await u.check({ asked: true });
    u.download("0.3.8");
    await settle(u);
    assert.equal(u.status().phase, "ready");
    u.finish("0.3.8");
    await settle(u);
    assert.equal(u.status().phase, "failed");
    assert.equal(u.status().sentence, "The new version couldn't be put in place. This copy is unchanged; install it from the releases page.");
    assert.deepEqual([u.quits, u.relaunched], [[], []]);
    assert.deepEqual(fs.readdirSync(u.at.folder), ["PersonalClaw.app"]);
    assert.deepEqual(privateFolders(u), []);
    assert.equal(ATTEMPT_KEY in u.store.data, false);
    assert.equal(u.swapIfStaged(), false);
  });

  it("does the same two steps from an AppImage: the download waits in its folder, executable, until Restart to finish", async () => {
    const root = tmp();
    const file = path.join(root, "PersonalClaw-0.3.7.AppImage");
    fs.writeFileSync(file, "old");
    const work = path.join(root, "work");
    fs.mkdirSync(work);
    const body = Buffer.from("the v0.3.8 copy");
    const { request } = fakeRequest((url) =>
      url === RELEASES_API ? { status: 200, body: JSON.stringify([release("v0.3.8")]) } : url.startsWith("https://github.com/") ? { status: 302, headers: { location: "https://objects.githubusercontent.com/x" } } : { status: 200, body },
    );
    const quits = [];
    const u = makeAppUpdater({
      current: "0.3.7", platform: "linux", arch: "x64", isPackaged: true, execPath: "/tmp/.mount_x/personalclaw", appImage: file,
      policy: () => ({ channel: "stable", pin: "", checkEnabled: true }), workDir: work, run: async () => assert.fail("no program runs for an AppImage"),
      quit: () => quits.push(1), relaunch: () => {}, trash: () => {}, request, setTimer: () => 0, clearTimer: () => {},
    });
    await u.check({ asked: true });
    u.download("0.3.8");
    await settle(u);
    assert.equal(u.status().phase, "ready", u.status().sentence);
    assert.deepEqual(fs.readdirSync(root).sort(), ["PersonalClaw-0.3.7.AppImage", "work"], "something was written beside the AppImage");
    const [folder] = fs.readdirSync(work);
    const ready = path.join(work, folder, "PersonalClaw-0.3.8.AppImage");
    assert.equal(fs.statSync(ready).mode & 0o777, 0o755);
    u.finish("0.3.8");
    await settle(u);
    assert.equal(u.status().phase, "restarting", u.status().sentence);
    const incoming = path.join(root, ".PersonalClaw-0.3.8.AppImage.incoming");
    assert.equal(fs.readFileSync(incoming, "utf8"), "the v0.3.8 copy");
    assert.equal(fs.statSync(incoming).mode & 0o777, 0o755);
    assert.equal(quits.length, 1);
    assert.deepEqual(fs.readdirSync(work), []);
  });

  it("drops a ready update the home's settings no longer allow, at the next check and at Restart to finish", async () => {
    // A pin set after Update: the next check drops the copy, asking GitHub only once it is gone,
    // and says what the pin allows.
    const pinned = { channel: "stable", pin: "", checkEnabled: true };
    const both = [release("v0.3.8"), release("v0.3.7")];
    const u = updater({ list: both, policy: pinned });
    await u.check({ asked: true });
    u.download("0.3.8");
    await settle(u);
    pinned.pin = "0.3.7";
    await u.check({ asked: false });
    assert.equal(u.status().ready, null);
    assert.equal(u.status().phase, "current");
    assert.deepEqual(u.status().dropped, { version: "0.3.8", sentence: "Your update settings no longer allow PersonalClaw 0.3.8, so it wasn't installed." }, "a copy the check dropped is not said");
    assert.deepEqual(privateFolders(u), []);
    assert.equal(u.finish("0.3.8").ok, false);
    // Said until the next Update, which clears it.
    pinned.pin = "";
    await u.check({ asked: true });
    assert.ok(u.status().dropped, "a later check forgot why the copy was dropped");
    u.download("0.3.8");
    assert.equal(u.status().dropped, null);
    await settle(u);

    // The same pin at the press: nothing is put in place.
    const atPress = { channel: "stable", pin: "", checkEnabled: true };
    const v = updater({ list: both, policy: atPress });
    await v.check({ asked: true });
    v.download("0.3.8");
    await settle(v);
    atPress.pin = "0.3.7";
    assert.deepEqual(v.finish("0.3.8"), { ok: false, sentence: "Your update settings no longer allow PersonalClaw 0.3.8, so it wasn't installed." });
    assert.deepEqual(v.status().dropped, { version: "0.3.8", sentence: "Your update settings no longer allow PersonalClaw 0.3.8, so it wasn't installed." });
    assert.deepEqual([v.quits, v.relaunched], [[], []]);
    assert.deepEqual(fs.readdirSync(v.at.folder), ["PersonalClaw.app"]);
    assert.deepEqual(privateFolders(v), []);
    await until(() => v.status().phase === "current");

    // A candidate ready on beta, after the switch to stable; and settings that can't be read.
    const beta = { channel: "beta", pin: "", checkEnabled: true };
    const w = updater({ list: [release("v0.3.8-rc.1", { prerelease: true, body: Buffer.from("the v0.3.8 copy") })], policy: beta, tools: fakeMacTools({ version: "0.3.8rc1" }) });
    await w.check({ asked: true });
    w.download("0.3.8-rc.1");
    await settle(w);
    assert.equal(w.status().phase, "ready", w.status().sentence);
    beta.channel = "stable";
    await w.check({ asked: true });
    assert.equal(w.status().ready, null);
    assert.deepEqual(privateFolders(w), []);
    const broken = { channel: "stable", pin: "", checkEnabled: true };
    const x = updater({ policy: broken });
    await x.check({ asked: true });
    x.download("0.3.8");
    await settle(x);
    broken.unreadable = "/home/config.json";
    await x.check({ asked: true });
    assert.equal(x.status().ready, null);
    assert.match(x.status().sentence, /couldn't read \/home\/config\.json/);
    assert.match(x.status().dropped.sentence, /couldn't read \/home\/config\.json/);

    // With automatic checks off, the press's refusal asks GitHub nothing after it.
    const quiet = { channel: "stable", pin: "", checkEnabled: true };
    const y = updater({ list: both, policy: quiet });
    await y.check({ asked: true });
    y.download("0.3.8");
    await settle(y);
    Object.assign(quiet, { pin: "0.3.7", checkEnabled: false });
    const before = y.seen.length;
    assert.equal(y.finish("0.3.8").ok, false);
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(y.seen.length, before, "the follow-up check asked GitHub with automatic checks off");
    assert.ok(y.status().dropped);
  });

  it("uses the ready copy at Restart to finish only while it is still the one that was checked", async () => {
    const readyOne = async () => {
      const u = updater();
      await u.check({ asked: true });
      u.download("0.3.8");
      await settle(u);
      const dir = path.join(u.at.work, privateFolders(u)[0]);
      return { u, dir, copy: path.join(dir, "PersonalClaw.app.ready") };
    };
    // Gone: no failure of the update. Update downloads it again.
    const gone = await readyOne();
    fs.rmSync(gone.dir, { recursive: true, force: true });
    assert.deepEqual(gone.u.finish("0.3.8"), { ok: false, sentence: "The downloaded copy of PersonalClaw 0.3.8 is gone. Press Update to download it again." });
    assert.equal(gone.u.status().phase, "available");
    assert.deepEqual(gone.u.status().available, { version: "0.3.8", installable: true });
    assert.deepEqual(gone.u.download("0.3.8"), { ok: true });
    await settle(gone.u);
    assert.equal(gone.u.status().phase, "ready");

    // Changed: another folder in its place, its folder opened to others, a link in place of the
    // copy, or a copy whose build is not the one the image held.
    const changes = {
      "another folder": ({ dir }) => {
        fs.renameSync(dir, `${dir}-moved`);
        fs.mkdirSync(dir, { mode: 0o700 });
        fs.cpSync(`${dir}-moved`, dir, { recursive: true });
      },
      "its folder opened to others": ({ dir }) => fs.chmodSync(dir, 0o755),
      "a link": ({ dir, copy }) => {
        const real = path.join(path.dirname(dir), "elsewhere.app");
        fs.renameSync(copy, real);
        fs.symlinkSync(real, copy);
      },
      "another build": ({ copy }) => fs.writeFileSync(path.join(copy, "Contents", "MacOS", "PersonalClaw"), "binary 0.3.8, changed"),
    };
    // Its folder not this account's: the account is the one the updater was made for.
    {
      const u = updater({ getuid: () => (typeof process.getuid === "function" ? process.getuid() + 1 : 4242) });
      await u.check({ asked: true });
      u.download("0.3.8");
      await settle(u);
      assert.equal(u.status().phase, "ready");
      assert.deepEqual(u.finish("0.3.8"), { ok: true });
      await settle(u);
      assert.equal(u.status().phase, "failed", "not this account's");
      assert.deepEqual(fs.readdirSync(u.at.folder), ["PersonalClaw.app"], "not this account's");
    }
    for (const [what, change] of Object.entries(changes)) {
      const r = await readyOne();
      change(r);
      assert.deepEqual(r.u.finish("0.3.8"), { ok: true }, what);
      await settle(r.u);
      assert.equal(r.u.status().phase, "failed", what);
      assert.equal(r.u.status().sentence, "The new version couldn't be put in place. This copy is unchanged; install it from the releases page.", what);
      assert.deepEqual([r.u.quits, r.u.relaunched], [[], []], what);
      assert.deepEqual(fs.readdirSync(r.u.at.folder), ["PersonalClaw.app"], what);
      assert.equal(r.u.swapIfStaged(), false, what);
    }
  });

  it("holds an AppImage's ready copy to the release's digest and size at Restart to finish", async () => {
    for (const [what, replace] of [["the same size", "THE V0.3.8 COPY"], ["another size", "a longer copy than the release's"]]) {
      const root = tmp();
      const file = path.join(root, "PersonalClaw-0.3.7.AppImage");
      fs.writeFileSync(file, "old");
      const work = path.join(root, "work");
      const { request } = fakeRequest((url) =>
        url === RELEASES_API ? { status: 200, body: JSON.stringify([release("v0.3.8", { body: Buffer.from("the v0.3.8 copy") })]) } : url.startsWith("https://github.com/") ? { status: 302, headers: { location: "https://objects.githubusercontent.com/x" } } : { status: 200, body: Buffer.from("the v0.3.8 copy") },
      );
      const quits = [];
      const u = makeAppUpdater({
        current: "0.3.7", platform: "linux", arch: "x64", isPackaged: true, execPath: "/tmp/.mount_x/personalclaw", appImage: file,
        policy: () => ({ channel: "stable", pin: "", checkEnabled: true }), workDir: work, run: async () => assert.fail("no program runs for an AppImage"),
        quit: () => quits.push(1), relaunch: () => {}, trash: () => {}, request, setTimer: () => 0, clearTimer: () => {},
      });
      await u.check({ asked: true });
      u.download("0.3.8");
      await settle(u);
      const [folder] = fs.readdirSync(work);
      fs.writeFileSync(path.join(work, folder, "PersonalClaw-0.3.8.AppImage"), replace);
      u.finish("0.3.8");
      await settle(u);
      assert.equal(u.status().phase, "failed", what);
      assert.deepEqual(quits, [], what);
      assert.deepEqual(fs.readdirSync(root).sort(), ["PersonalClaw-0.3.7.AppImage", "work"], `${what}: a copy was left beside the AppImage`);
    }
  });

  /** An AppImage install of 0.3.7 with 0.3.8 on offer: the updater, the file, its folder, quits. */
  const appImage = () => {
    const root = tmp();
    const file = path.join(root, "PersonalClaw-0.3.7.AppImage");
    fs.writeFileSync(file, "old");
    const work = path.join(root, "work");
    const { request } = fakeRequest((url) =>
      url === RELEASES_API ? { status: 200, body: JSON.stringify([release("v0.3.8", { body: Buffer.from("the v0.3.8 copy") })]) } : url.startsWith("https://github.com/") ? { status: 302, headers: { location: "https://objects.githubusercontent.com/x" } } : { status: 200, body: Buffer.from("the v0.3.8 copy") },
    );
    const quits = [];
    const u = makeAppUpdater({
      current: "0.3.7", platform: "linux", arch: "x64", isPackaged: true, execPath: "/tmp/.mount_x/personalclaw", appImage: file,
      policy: () => ({ channel: "stable", pin: "", checkEnabled: true }), workDir: work, run: async () => assert.fail("no program runs for an AppImage"),
      quit: () => quits.push(1), relaunch: () => {}, trash: () => {}, request, setTimer: () => 0, clearTimer: () => {},
    });
    return { u, root, file, work, quits };
  };

  it("hashes the AppImage's copy beside it, not the download it was made from", async () => {
    const a = appImage();
    await a.u.check({ asked: true });
    a.u.download("0.3.8");
    await settle(a.u);
    const read = [];
    const real = fs.createReadStream;
    fs.createReadStream = (f, ...rest) => (read.push(String(f)), real(f, ...rest));
    try {
      a.u.finish("0.3.8");
      await settle(a.u);
    } finally {
      fs.createReadStream = real;
    }
    assert.equal(a.u.status().phase, "restarting", a.u.status().sentence);
    assert.deepEqual(read, [path.join(a.root, ".PersonalClaw-0.3.8.AppImage.incoming")]);
  });

  it("puts nothing in place when the app quits while Restart to finish copies it, and does not quit for it", async () => {
    const a = appImage();
    await a.u.check({ asked: true });
    a.u.download("0.3.8");
    await settle(a.u);
    assert.deepEqual(a.u.finish("0.3.8"), { ok: true });
    a.u.stop();
    await new Promise((r) => setTimeout(r, 50));
    assert.deepEqual(a.quits, [], "the quit the app is in was asked for again");
    assert.equal(a.u.swapIfStaged(), false, "a copy was staged during the quit");
    assert.deepEqual(fs.readdirSync(a.root).sort(), ["PersonalClaw-0.3.7.AppImage", "work"], "the copy was left beside the AppImage");
    assert.deepEqual(a.u.finish("0.3.8"), { ok: false, sentence: "The app is quitting." });
  });

  it("lets a download still under way go at the quit's last step: its image detached at once, its folder removed", async () => {
    for (const detaches of [true, false]) {
      let open;
      const gate = new Promise((r) => (open = r));
      const detached = [];
      const u = updater({
        tools: fakeMacTools({ attach: () => gate }),
        isMountPoint: (p) => /mount-[^/]*$/.test(p),
        runSync: (cmd, args) => {
          detached.push(args[1]);
          if (!detaches) throw new Error("resource busy");
        },
      });
      await u.check({ asked: true });
      u.download("0.3.8");
      await until(() => u.status().phase === "verifying");
      const [folder] = privateFolders(u);
      u.abandon();
      assert.equal(detached.length, 1, "the image was not detached");
      assert.match(detached[0], /mount-/);
      assert.equal(fs.existsSync(path.join(u.at.work, folder)), !detaches, detaches ? "the folder outlived the quit" : "a folder whose image won't detach was removed");
      open();
    }
  });

  it("keeps nothing from a download the quit overtook, and attaches no image once the quit began", async () => {
    // The quit while the image's app is checked: when the check ends, its folder goes, image detached.
    let open;
    const gate = new Promise((r) => (open = r));
    const u = updater({ tools: fakeMacTools({ attach: () => gate }) });
    await u.check({ asked: true });
    u.download("0.3.8");
    await until(() => u.status().phase === "verifying");
    u.stop();
    open();
    await until(() => privateFolders(u).length === 0);
    assert.notEqual(u.status().phase, "ready");
    assert.equal(u.finish("0.3.8").ok, false);
    assert.ok(u.mac.calls.some((c) => c[0] === "hdiutil" && c[1] === "detach"), "the image stayed attached");

    // The quit while it downloads: no image is attached at all, and no new Update starts.
    const v = updater();
    await v.check({ asked: true });
    v.download("0.3.8");
    v.stop();
    assert.deepEqual(v.download("0.3.8"), { ok: false, sentence: "The app is quitting." });
    await until(() => privateFolders(v).length === 0);
    await new Promise((r) => setTimeout(r, 20));
    assert.deepEqual(v.mac.calls.filter((c) => c[0] === "hdiutil"), []);
    assert.notEqual(v.status().phase, "ready");
  });

  it("attaches no image when the quit begins as the download's last bytes arrive", async () => {
    // Each chunk asks whether the app is quitting before it is written, so a quit that begins on the last
    // one finds the download finished: only the check after the download keeps the image unattached.
    let u = null;
    u = updater({
      onState: (s) => {
        if (u && s.progress && s.progress.total > 0 && s.progress.received === s.progress.total) u.stop();
      },
    });
    await u.check({ asked: true });
    u.download("0.3.8");
    await until(() => privateFolders(u).length === 0);
    await new Promise((r) => setTimeout(r, 20));
    assert.deepEqual(u.mac.calls.filter((c) => c[0] === "hdiutil"), [], "an image was attached after the quit began");
    assert.notEqual(u.status().phase, "ready");
  });

  it("removes the folders an update left at the next start, detaching an image still attached first", async () => {
    const at = installed();
    const left = path.join(at.work, "app-update-left");
    const withImage = path.join(at.work, "app-update-image");
    const stuck = path.join(at.work, "app-update-stuck");
    const other = path.join(at.work, "not-an-update");
    for (const d of [left, path.join(withImage, "mount-a"), path.join(stuck, "mount-b"), other]) fs.mkdirSync(d, { recursive: true });
    fs.writeFileSync(path.join(left, "PersonalClaw-0.3.8-arm64.dmg"), "partial");
    fs.writeFileSync(path.join(withImage, "mount-a", "file"), "inside the image");
    fs.writeFileSync(path.join(stuck, "mount-b", "file"), "inside an image that won't detach");
    const detached = [];
    const tools = fakeMacTools({ detach: (m) => { detached.push(m); if (m.endsWith("mount-b")) throw new Error("resource busy"); } });
    const u = updater({ at, tools, isMountPoint: (p) => /mount-[ab]$/.test(p) && fs.readdirSync(p).length > 0 });
    u.start();
    await until(() => !fs.existsSync(left) && !fs.existsSync(withImage));
    // The sweep removes folders one at a time, so wait for its word on the stuck one before reading what it left.
    await until(() => u.logs.some((line) => line.includes(stuck) && line.includes("could not be detached")));
    assert.deepEqual(detached.sort(), [path.join(stuck, "mount-b"), path.join(withImage, "mount-a")].sort());
    assert.ok(fs.existsSync(path.join(stuck, "mount-b", "file")), "a folder whose image won't detach was emptied");
    assert.ok(fs.existsSync(other), "a folder that is not an update's was removed");
  });

  it("sweeps no link and no folder not its own, and keeps what is under way and what is ready", async () => {
    // A link among the folders, to a folder elsewhere that holds an image: neither followed nor detached.
    {
      const at = installed();
      const elsewhere = path.join(at.root, "elsewhere");
      fs.mkdirSync(path.join(elsewhere, "mount-z"), { recursive: true });
      fs.writeFileSync(path.join(elsewhere, "mount-z", "file"), "someone else's");
      fs.symlinkSync(elsewhere, path.join(at.work, "app-update-link"));
      const tools = fakeMacTools();
      const u = updater({ at, tools, isMountPoint: (p) => /mount-z$/.test(p) });
      u.start();
      await new Promise((r) => setTimeout(r, 30));
      assert.deepEqual(tools.calls.filter((c) => c[0] === "hdiutil"), [], "an image was detached through a link");
      assert.ok(fs.existsSync(path.join(elsewhere, "mount-z", "file")));
    }
    // The work folder itself a link: nothing in it is swept.
    {
      const at = installed();
      const real = path.join(at.root, "real-work");
      fs.mkdirSync(path.join(real, "app-update-x"), { recursive: true });
      fs.writeFileSync(path.join(real, "app-update-x", "file"), "kept");
      const linked = path.join(at.root, "linked-work");
      fs.symlinkSync(real, linked);
      const u = updater({ at: { ...at, work: linked } });
      u.start();
      await new Promise((r) => setTimeout(r, 30));
      assert.ok(fs.existsSync(path.join(real, "app-update-x", "file")), "a linked work folder was swept");
    }
    // A plain folder under the default mount test: removed, with no detach.
    {
      const at = installed();
      fs.mkdirSync(path.join(at.work, "app-update-plain", "mount-y"), { recursive: true });
      const tools = fakeMacTools();
      const u = updater({ at, tools });
      u.start();
      await until(() => !fs.existsSync(path.join(at.work, "app-update-plain")));
      assert.deepEqual(tools.calls.filter((c) => c[0] === "hdiutil"), []);
    }
    // A download under way, and a ready copy: both kept.
    {
      let open;
      const gate = new Promise((r) => (open = r));
      const u = updater({ tools: fakeMacTools({ attach: () => gate }) });
      await u.check({ asked: true });
      u.download("0.3.8");
      await until(() => u.status().phase === "verifying");
      u.start();
      await new Promise((r) => setTimeout(r, 30));
      assert.equal(privateFolders(u).length, 1, "the sweep removed the download under way");
      open();
      await settle(u);
      assert.equal(u.status().phase, "ready");
      u.start();
      await new Promise((r) => setTimeout(r, 30));
      assert.equal(privateFolders(u).length, 1, "the sweep removed the ready copy");
      assert.deepEqual(u.finish("0.3.8"), { ok: true });
    }
  });

  it("keeps an update's state when a check that began before it answers after it, or fails after it", async () => {
    for (const status of [200, 500]) {
      let answer;
      const held = new Promise((r) => (answer = r));
      const u = updater({ listHold: (n) => (n === 1 ? held : null), listStatus: (n) => (n === 1 ? status : 200) });
      await u.check({ asked: true });
      const late = u.check({ asked: true });
      assert.deepEqual(u.download("0.3.8"), { ok: true });
      await settle(u);
      assert.equal(u.status().phase, "ready");
      answer();
      await late;
      assert.equal(u.status().phase, "ready", `the late check's ${status} replaced the ready update`);
      assert.deepEqual(u.status().ready, { version: "0.3.8" });
    }
  });

  it("removes a ready copy nobody restarted on when the app quits", async () => {
    const u = updater();
    await u.check({ asked: true });
    u.download("0.3.8");
    await settle(u);
    assert.equal(privateFolders(u).length, 1);
    u.stop();
    assert.deepEqual(privateFolders(u), []);
    assert.equal(u.swapIfStaged(), false);
    assert.deepEqual(fs.readdirSync(u.at.folder), ["PersonalClaw.app"]);
  });

  it("starts this copy again when the new one cannot take its place, and that start says so", async () => {
    const u = updater();
    await updateAndFinish(u);
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
    await updateAndFinish(u);
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

  it("says a failed update in one sentence, leaves this copy as it was, and keeps and stages nothing", async () => {
    const u = updater({ tools: fakeMacTools({ id: "io.example.other" }) });
    await u.check({ asked: true });
    u.download("0.3.8");
    await settle(u);
    assert.equal(u.status().phase, "failed");
    assert.match(u.status().sentence, /didn't hold the expected app, so nothing was used/);
    assert.deepEqual([u.quits, u.relaunched], [[], []]);
    assert.equal(ATTEMPT_KEY in u.store.data, false);
    assert.equal(u.swapIfStaged(), false);
    assert.equal(fs.readFileSync(u.at.execPath, "utf8"), "binary 0.3.7");
    await new Promise((r) => setTimeout(r, 20));
    assert.deepEqual(privateFolders(u), [], "a failed download left its folder");
    assert.equal(u.finish("0.3.8").ok, false, "a failed download is ready to finish");
    assert.equal(u.download("0.3.9").sentence, "That version is not the one on offer. Check again.", "a failed update holds the updater");
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
    assert.equal(u.download("0.3.8").ok, false);
  });
});

describe("registerAppUpdateIpc: the app's own windows ask, and nothing else", () => {
  const channels = { appUpdateStatus: "s", appUpdateCheck: "c", appUpdateDownload: "d", appUpdateFinish: "f" };
  function wired(isAppWindow) {
    const handlers = new Map();
    const asked = [];
    const updater = {
      status: () => "state",
      check: (o) => (asked.push(["check", o]), "checked"),
      download: (v) => (asked.push(["download", v]), { ok: true }),
      finish: (v) => (asked.push(["finish", v]), { ok: true }),
    };
    registerAppUpdateIpc({ ipcMain: { handle: (ch, fn) => handlers.set(ch, fn) }, channels, updater: () => updater, isAppWindow });
    return { handlers, asked };
  }

  it("answers the app's bridged windows, with the version their Update and confirm named", async () => {
    const { handlers, asked } = wired(() => true);
    assert.deepEqual([...handlers.keys()].sort(), ["c", "d", "f", "s"]);
    assert.equal(await handlers.get("s")({ sender: {} }), "state");
    assert.equal(await handlers.get("c")({ sender: {} }), "checked");
    await handlers.get("d")({ sender: {} }, "0.3.8");
    await handlers.get("d")({ sender: {} }, { version: "0.3.8" });
    await handlers.get("f")({ sender: {} }, "0.3.8");
    await handlers.get("f")({ sender: {} }, ["0.3.8"]);
    assert.deepEqual(asked, [["check", { asked: true }], ["download", "0.3.8"], ["download", ""], ["finish", "0.3.8"], ["finish", ""]]);
  });

  it("refuses a page that is not one of the app's windows", () => {
    const { handlers, asked } = wired(() => false);
    for (const ch of ["s", "c", "d", "f"]) assert.throws(() => handlers.get(ch)({ sender: {} }, "0.3.8"), /not one of the app's windows/);
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
    assert.ok(finale.indexOf("appUpdater?.abandon();") > 0 && finale.indexOf("appUpdater?.abandon();") < finale.indexOf("appUpdater?.swapIfStaged();"), "a download still under way outlives the quit");
    assert.match(quit, /appUpdater\?\.stop\(\);/, "the daily check outlives the quit");
  });

  it("asks to be started again from the updater only, and answers only the app's bridged windows", () => {
    assert.equal([...MAIN.matchAll(/app\.relaunch\(/g)].length, 1);
    assert.match(body("makeThisAppsUpdater"), /relaunch: \(program\) =>\n\s+app\.relaunch\(\{ execPath: program,/);
    const ipc = MAIN.slice(MAIN.indexOf("registerAppUpdateIpc({"), MAIN.indexOf("});", MAIN.indexOf("registerAppUpdateIpc({")));
    assert.match(ipc, /isAppWindow: \(sender\) => BaseWindow\.getAllWindows\(\)\.some\(\(w\) => w\._pcBridgeAttached === true && w\.webContents === sender\)/);
  });

  it("keeps the ready copy in the app's own folder, not the system's temp folder, and reads codesign's display from stderr", () => {
    // The temp folder is emptied of files left unread for days, and on Linux any account can make a
    // folder in /tmp under a name that was freed.
    assert.match(body("makeThisAppsUpdater"), /workDir: path\.join\(app\.getPath\("userData"\), "app-update"\),/);
    assert.doesNotMatch(body("makeThisAppsUpdater"), /tmpdir/);
    assert.match(body("runProgram"), /resolve\(stream === "stderr" \? stderr : stdout\)/);
  });

  it("follows the pin only while the home's gateway is not one the app found running, and trashes the old copy once the new one has its gateway", () => {
    assert.match(body("makeThisAppsUpdater"), /readUpdatePolicy\(\{ home: PERSONALCLAW_HOME, ownGateway: !\["adopted", "served"\]\.includes\(localState\.state\) \}\)/);
    assert.match(body("startAppUpdater"), /if \(localState\.state === "own" \|\| localState\.state === "adopted"\) appUpdater\.trashPrevious\(\);/);
    const ready = MAIN.slice(MAIN.indexOf("app.whenReady().then("));
    assert.ok(ready.indexOf("await bringUpLocalGateway();") < ready.indexOf("startAppUpdater();"), "the updater starts before the gateway is up");
  });
});
