"use strict";

/**
 * The desktop app's own update: a check when it starts and then once a day, and an update only
 * when the owner presses for it, in two steps. **Update** downloads and checks the release in a
 * private folder of its own, in the background, and writes nothing beside the app; once it is
 * ready, the app says **Restart to finish the update**. Only that press puts the new copy beside
 * the app (in `/Applications`, where it is installed) and restarts on it. Nothing is downloaded
 * before the first press, and nothing beside the app changes before the second.
 *
 * The check reads the repository's release list over TLS, follows no redirect, and picks the
 * release this copy should move to: the newest full release newer than it, or the newest with
 * release candidates when the home follows the beta channel, or exactly the home's pin when the
 * app runs its own gateway (a pin there pins the app, whose version is its gateway's; a gateway the
 * app found running is pinned by its own install).
 *
 * The update takes that release's copy for this computer and nothing else: on macOS exactly
 * `PersonalClaw-<version>-<arch>.dmg`, from Linux's AppImage exactly `PersonalClaw-<version>.AppImage`,
 * at the address GitHub publishes for it, with GitHub's `sha256` digest for it and a size it
 * states. The download is https, to GitHub's release hosts only, with no credential; it goes into a
 * folder of its own and is hashed as it streams, and a byte count or digest that differs uses
 * nothing. On macOS the disk image is attached read-only; the app inside must be `PersonalClaw.app`
 * with this app's bundle id and the release's version, its seal intact (`codesign --verify --deep
 * --strict`) and this Mac's architecture in it. It is copied out of the image into the private
 * folder, sealed still and with the CDHash of the app the image held, and waits there, ready
 * (`prepareMac`); the AppImage waits as it came. The image is hashed again on disk once it is
 * attached, so a file swapped for it after the download is not used. The private folder is the
 * app's own (in its data folder, 0700), which nothing empties while the copy waits, and it lasts
 * only until Restart to finish or the quit: one an update left behind is removed at the next
 * start, unless a disk image in it can't be detached; that folder is left, and the log says so.
 * The CDHash holds the copy to that build across the wait and against other accounts. It is no
 * guard against a process running as the owner, which could change the image as it downloads:
 * that is an already-compromised account, outside SECURITY.md's scope.
 *
 * Restart to finish uses the ready copy only if it is still the one that was checked: its folder
 * the same one, this account's and 0700, and the copy in it. It copies it beside the running app,
 * under a hidden name, and checks it again there: the bundle id, version and architecture, and
 * exactly the build the image held (`placeMac`, by its CDHash), or the release's digest
 * (`placeAppImage`). A ready copy that is gone is downloaded again by Update. The home's update
 * settings are read again too: one they no longer allow is dropped, at the next check or at the
 * press. Then the app quits as it always does: it signs out and stops its gateway. Only once that is done
 * does the new copy take the app's place (`swapNow`), by two renames: the running app moves to a
 * hidden name beside it, the new copy to the app's own path; a second rename that fails undoes the
 * first. The app then starts again, on the new copy, which moves the old one to the Trash once it
 * and its gateway are up (`trashPrevious`). Nothing else ever removes the old copy. Every failure
 * is said in one sentence, with the releases page as the way on: an update that cannot finish here
 * leaves this copy as it was, and one that ended while the app was quitting is said by the next
 * start (`ATTEMPT_KEY`).
 *
 * Electron, the network and the commands it runs are injected, so `desktop/test/appUpdate.test.js`
 * drives every step against fakes.
 */

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const REPO = "PersonalClaw/PersonalClaw";
/** The release list, newest first. */
const RELEASES_API = `https://api.github.com/repos/${REPO}/releases?per_page=30`;
/** Where every update that cannot happen here points instead. */
const RELEASES_PAGE_URL = `https://github.com/${REPO}/releases`;
const APP_NAME = "PersonalClaw";
const BUNDLE_ID = "io.personalclaw.app";
/** The only hosts a download may be sent to, redirects included. */
const DOWNLOAD_HOSTS = Object.freeze(["github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"]);
const MAX_REDIRECTS = 5;
/** Larger than any copy a release ships (about 250 MB today); a larger one is not used. */
const MAX_ASSET_BYTES = 1024 * 1024 * 1024;
const MAX_LIST_BYTES = 4 * 1024 * 1024;
const LIST_TIMEOUT_MS = 20_000;
const DOWNLOAD_IDLE_MS = 60_000;
/** The first check, after the start has settled; then one a day. */
const FIRST_CHECK_AFTER_MS = 60_000;
const CHECK_EVERY_MS = 24 * 60 * 60 * 1000;
const DIGEST = /^sha256:([0-9a-f]{64})$/;
const USER_AGENT = "personalclaw-update-check";

// ── Versions ──

const VERSION = /^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})(?:[-.]?(a|alpha|b|beta|c|rc|pre|preview)[-.]?(\d{0,6}))?(?:[-.]?post[-.]?(\d{1,6}))?(?:[-.]?dev[-.]?(\d{0,6}))?$/i;
const PRE_RANK = { a: 0, alpha: 0, b: 1, beta: 1, c: 2, rc: 2, pre: 2, preview: 2 };

/** *text* as a version this file orders, or null: the release forms PersonalClaw ships, in the
 *  order PEP 440 gives them (`personalclaw.versions`), a tag's `v` and `-rc.1` spelling included. */
function parseVersion(text) {
  if (typeof text !== "string" || text.length > 64) return null;
  const m = VERSION.exec(text.trim());
  if (!m) return null;
  return {
    release: [Number(m[1]), Number(m[2]), Number(m[3])],
    pre: m[4] ? [PRE_RANK[m[4].toLowerCase()], Number(m[5] || 0)] : null,
    post: m[6] === undefined ? null : Number(m[6]),
    dev: m[7] === undefined ? null : Number(m[7] || 0),
  };
}

/** The key that orders *v*: `X.Y.Z.devN < X.Y.ZaN < X.Y.ZbN < X.Y.ZrcN < X.Y.Z < X.Y.Z.postN`, and a
 *  `.devN` of any of them just before it. */
function orderKey(v) {
  if (v.pre) return [...v.release, 1, ...v.pre, v.dev === null ? Infinity : v.dev];
  if (v.post !== null) return [...v.release, 3, v.post, v.dev === null ? Infinity : v.dev];
  if (v.dev !== null) return [...v.release, 0, v.dev];
  return [...v.release, 2];
}

/** -1, 0 or 1 as *a* is older than, the same as, or newer than *b*; null when either is unreadable. */
function compareVersions(a, b) {
  const x = parseVersion(a);
  const y = parseVersion(b);
  if (!x || !y) return null;
  const kx = orderKey(x);
  const ky = orderKey(y);
  for (let i = 0; i < Math.max(kx.length, ky.length); i++) {
    const p = i < kx.length ? kx[i] : -Infinity;
    const q = i < ky.length ? ky[i] : -Infinity;
    if (p !== q) return p < q ? -1 : 1;
  }
  return 0;
}

const isNewer = (candidate, current) => compareVersions(candidate, current) === 1;
const sameVersion = (a, b) => compareVersions(a, b) === 0;

/** Whether *text* is a release candidate, an alpha or beta, or a development build. */
function isPrerelease(text) {
  const v = parseVersion(text);
  return Boolean(v && (v.pre || v.dev !== null));
}

// ── Which release, and which file of it ──

/**
 * The release this copy (*current*) should be offered: `{release}`, or `{release: null, why}`.
 * With a *pin* (the app's own gateway only), exactly the pinned release when it is newer: a pin
 * older than this copy is not offered, since the app does not roll back (`pin_older`), and a pin no
 * release matches is said (`pin_missing`). Otherwise the newest full release newer than this copy,
 * or the newest of any release on the beta channel. Drafts never.
 */
function chooseRelease(releases, { current, channel = "stable", pin = "" }) {
  const usable = (Array.isArray(releases) ? releases : []).filter(
    (r) => r && typeof r === "object" && !r.draft && parseVersion(String(r.tag_name || "")),
  );
  if (pin) {
    const pinned = usable.find((r) => sameVersion(r.tag_name, pin));
    if (!pinned) return { release: null, why: "pin_missing" };
    const order = compareVersions(pinned.tag_name, current);
    if (order === 1) return { release: pinned, why: "" };
    return { release: null, why: order === 0 ? "current" : "pin_older" };
  }
  const beta = channel === "beta";
  const newer = usable.filter(
    (r) => (beta || (!r.prerelease && !isPrerelease(r.tag_name))) && isNewer(r.tag_name, current),
  );
  if (!newer.length) return { release: null, why: "current" };
  newer.sort((a, b) => compareVersions(b.tag_name, a.tag_name));
  return { release: newer[0], why: "" };
}

/** The name a release gives its copy for this computer, or "" when it ships none. */
function assetName(version, { platform, arch }) {
  if (platform === "darwin" && (arch === "arm64" || arch === "x64")) return `${APP_NAME}-${version}-${arch}.dmg`;
  if (platform === "linux") return `${APP_NAME}-${version}.AppImage`;
  return "";
}

/**
 * *release*'s copy for this computer, checked before anything is fetched: `{ok, asset}`, or
 * `{ok: false, why}`. Exactly the one name; GitHub's `sha256` digest for it; a size within
 * `MAX_ASSET_BYTES`; and the address GitHub publishes for that tag and name, which is the address
 * built from them, so a release record cannot point the download anywhere else.
 */
function chooseAsset(release, { platform, arch }) {
  const tag = String(release && release.tag_name);
  const version = tag.replace(/^v/, "");
  const name = assetName(version, { platform, arch });
  const asset = name ? (release.assets || []).find((a) => a && a.name === name) : null;
  if (!asset) return { ok: false, why: "no_asset" };
  const digest = DIGEST.exec(String(asset.digest || ""));
  if (!digest) return { ok: false, why: "unchecked" };
  const size = Number(asset.size);
  if (!Number.isSafeInteger(size) || size <= 0 || size > MAX_ASSET_BYTES) return { ok: false, why: "unchecked" };
  const url = `https://github.com/${REPO}/releases/download/${encodeURIComponent(tag)}/${encodeURIComponent(name)}`;
  if (asset.browser_download_url !== url) return { ok: false, why: "unchecked" };
  return { ok: true, asset: { name, size, sha256: digest[1], url, version, tag } };
}

// ── Where this copy runs ──

/**
 * How this copy is updated: `mac` (a `PersonalClaw.app` this account can replace), `appimage`
 * (an AppImage file it can replace), else `page` (the releases page) or `dev`, with why.
 */
function whereItRuns({ platform, isPackaged, execPath, appImage = "", access = fs.accessSync }) {
  if (!isPackaged) return { how: "dev", why: "dev" };
  if (platform === "darwin") {
    const bundle = path.resolve(execPath, "..", "..", "..");
    if (!bundle.endsWith(`${path.sep}${APP_NAME}.app`)) return { how: "page", why: "renamed" };
    if (bundle.includes("/AppTranslocation/") || bundle.startsWith("/Volumes/")) return { how: "page", why: "move" };
    try {
      access(path.dirname(bundle), fs.constants.W_OK);
      access(bundle, fs.constants.W_OK);
    } catch {
      return { how: "page", why: "not_writable", folder: path.dirname(bundle) };
    }
    return { how: "mac", bundle };
  }
  if (platform === "linux" && appImage) {
    const file = path.resolve(appImage);
    try {
      access(path.dirname(file), fs.constants.W_OK);
    } catch {
      return { how: "page", why: "not_writable", folder: path.dirname(file) };
    }
    return { how: "appimage", file };
  }
  return { how: "page", why: "package" };
}

/** The one sentence each outcome is said with. The releases page is offered beside every failure. */
function sentenceFor(why, detail = {}) {
  switch (why) {
    case "dev":
      return "A development run updates from its checkout.";
    case "move":
      return "Move PersonalClaw to your Applications folder first: this copy runs from the disk image or a copy macOS made of it, which can't be replaced.";
    case "renamed":
      return "This copy isn't named PersonalClaw.app, so it isn't replaced in place. Install the new version from the releases page.";
    case "not_writable":
      return `This account can't replace PersonalClaw in ${detail.folder || "its folder"}. Install the new version from the releases page.`;
    case "package":
      return "This copy was installed as a package. Install the new version from the releases page.";
    case "pin_older":
      return `updates.pin names ${detail.pin}, older than this app (${detail.current}). The app doesn't roll back: install that version from the releases page.`;
    case "pin_missing":
      return `No release matches updates.pin (${detail.pin}).`;
    case "unreachable":
      return "The release list couldn't be read. Check again later, or look at the releases page.";
    case "no_asset":
      return "This release has no copy for this computer. Install it from the releases page.";
    case "unchecked":
      return "This release's copy for this computer can't be checked, so it wasn't used. Install it from the releases page.";
    case "download":
      return "The download didn't finish, so nothing was used. Try again, or install it from the releases page.";
    case "mismatch":
      return "The download didn't match the release, so nothing was used. Install it from the releases page.";
    case "contents":
      return "The disk image didn't hold the expected app, so nothing was used. Install it from the releases page.";
    case "put_in_place":
      return "The new version couldn't be put in place. This copy is unchanged; install it from the releases page.";
    case "ready_gone":
      return `The downloaded copy of PersonalClaw ${detail.version} is gone. Press Update to download it again.`;
    case "ready_not_allowed":
      return `Your update settings no longer allow PersonalClaw ${detail.version}, so it wasn't installed.`;
    case "stranded":
      return `The new version couldn't be put in place, and this copy couldn't be moved back: it is at ${detail.path}. Rename it to PersonalClaw.app.`;
    case "unreadable_config":
      return `PersonalClaw couldn't read ${detail.file}, so this app doesn't know which release it follows. Fix the file, or install from the releases page.`;
    default:
      return "The update didn't finish. This copy is unchanged; install it from the releases page.";
  }
}

class UpdateFailure extends Error {
  constructor(why, detail = {}) {
    super(sentenceFor(why, detail));
    this.why = why;
    this.detail = detail;
  }
}

// ── The home's settings ──

/**
 * The home's `updates` settings as the app follows them, read from its `config.json` each time
 * they are needed, so a change in Settings › Updates holds from the next check: `{channel, pin,
 * checkEnabled}`, or `{unreadable}` naming the file. Only `beta` is a line of its own; every other
 * channel rides `stable`, as the gateway's resolver does (`nightly` follows a checkout's branch,
 * and no app is built from one). The pin is followed only while the home's gateway is the app's
 * own (*ownGateway*): a gateway the app found serving the home is pinned by its own install.
 *
 * Fails closed: a file that is there but cannot be read or parsed offers nothing, since the pin it
 * may hold is a limit, and dropping it would offer more than the owner chose. No file, or an empty
 * one, is the defaults, as the gateway reads it.
 */
function readUpdatePolicy({ home, ownGateway, readFile = fs.readFileSync }) {
  const file = path.join(home, "config.json");
  let raw;
  try {
    raw = String(readFile(file, "utf8"));
  } catch (err) {
    if (err && err.code === "ENOENT") return { channel: "stable", pin: "", checkEnabled: true };
    return { unreadable: file };
  }
  let data = {};
  try {
    if (raw.trim()) data = JSON.parse(raw);
  } catch {
    return { unreadable: file };
  }
  const block = data && typeof data === "object" ? data.updates : null;
  // A block that is not an object is reported and dropped by the gateway's loader, which then
  // takes the defaults; so does this.
  const updates = block && typeof block === "object" && !Array.isArray(block) ? block : {};
  const pin = typeof updates.pin === "string" ? updates.pin.trim() : "";
  return {
    channel: updates.channel === "beta" ? "beta" : "stable",
    pin: ownGateway ? pin : "",
    checkEnabled: updates.check_enabled !== false && updates.check_enabled !== "false",
  };
}

// ── The network ──

/**
 * GET *url* and hand the response to *onResponse*, refusing anything but https to *hosts* (or the
 * one host of *url* when null), following at most *redirects* redirects, sending no credential.
 */
function getOnce(request, url, { hosts, redirects, headers, timeoutMs }, onResponse, onError) {
  let target;
  try {
    target = new URL(url);
  } catch {
    onError(new UpdateFailure("download"));
    return;
  }
  const allowed = hosts ? hosts.includes(target.hostname) : true;
  if (target.protocol !== "https:" || !allowed || target.username || target.password) {
    onError(new UpdateFailure("download"));
    return;
  }
  const req = request(target, { method: "GET", headers, agent: false }, (res) => {
    const status = res.statusCode || 0;
    if (status >= 300 && status < 400 && res.headers.location) {
      res.resume();
      if (redirects <= 0) {
        onError(new UpdateFailure("download"));
        return;
      }
      const next = new URL(res.headers.location, target).toString();
      getOnce(request, next, { hosts, redirects: redirects - 1, headers, timeoutMs }, onResponse, onError);
      return;
    }
    onResponse(res, status);
  });
  req.setTimeout(timeoutMs, () => req.destroy(new UpdateFailure("download")));
  req.on("error", onError);
  req.end();
}

/** The release list, as GitHub's API answers it. No redirect is followed. */
function fetchReleases({ request = require("https").request, userAgent }) {
  return new Promise((resolve, reject) => {
    const headers = { Accept: "application/vnd.github+json", "User-Agent": userAgent };
    getOnce(request, RELEASES_API, { hosts: ["api.github.com"], redirects: 0, headers, timeoutMs: LIST_TIMEOUT_MS }, (res, status) => {
      if (status !== 200) {
        res.resume();
        reject(new UpdateFailure("unreachable"));
        return;
      }
      const chunks = [];
      let bytes = 0;
      res.on("data", (chunk) => {
        bytes += chunk.length;
        if (bytes > MAX_LIST_BYTES) res.destroy(new UpdateFailure("unreachable"));
        else chunks.push(chunk);
      });
      res.on("error", () => reject(new UpdateFailure("unreachable")));
      res.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
        } catch {
          reject(new UpdateFailure("unreachable"));
        }
      });
    }, () => reject(new UpdateFailure("unreachable")));
  });
}

/**
 * Download *asset* into *file* (created new, 0600), hashing it as it streams. Resolves once the
 * byte count and the sha256 are both the release's; rejects, with the partial file removed, when
 * either differs, the download stalls, *cancelled()* says it is called off, or anything but
 * GitHub's release hosts is asked for it.
 */
function downloadAsset(asset, file, { request = require("https").request, userAgent, onProgress = () => {}, cancelled = () => false }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const fail = (why) => {
      if (settled) return;
      settled = true;
      fs.rm(file, { force: true }, () => reject(why instanceof UpdateFailure ? why : new UpdateFailure("download")));
    };
    getOnce(request, asset.url, { hosts: DOWNLOAD_HOSTS, redirects: MAX_REDIRECTS, headers: { "User-Agent": userAgent }, timeoutMs: DOWNLOAD_IDLE_MS }, (res, status) => {
      if (status !== 200) {
        res.resume();
        fail(new UpdateFailure("download"));
        return;
      }
      let out;
      try {
        out = fs.createWriteStream(file, { flags: "wx", mode: 0o600 });
      } catch {
        res.resume();
        fail(new UpdateFailure("download"));
        return;
      }
      const hash = crypto.createHash("sha256");
      let bytes = 0;
      res.on("data", (chunk) => {
        // Called off (the app is quitting): the partial file goes with the failure.
        if (cancelled()) {
          res.destroy();
          out.destroy();
          fail(new UpdateFailure("download"));
          return;
        }
        bytes += chunk.length;
        if (bytes > asset.size) {
          res.destroy();
          out.destroy();
          fail(new UpdateFailure("mismatch"));
          return;
        }
        hash.update(chunk);
        onProgress({ received: bytes, total: asset.size });
      });
      res.on("error", () => {
        out.destroy();
        fail(new UpdateFailure("download"));
      });
      res.pipe(out);
      out.on("error", () => fail(new UpdateFailure("download")));
      out.on("finish", () => {
        if (settled) return;
        if (bytes !== asset.size || hash.digest("hex") !== asset.sha256) {
          fail(new UpdateFailure("mismatch"));
          return;
        }
        settled = true;
        resolve(file);
      });
    }, () => fail(new UpdateFailure("download")));
  });
}

// ── The ready copy, and putting it beside this one ──

/** The hidden names beside the app: the new copy while it waits, the old one once it is replaced. */
const incomingName = (version, ext) => `.${APP_NAME}-${version}.${ext}.incoming`;
const previousName = (version, ext, n = 1) => `.${APP_NAME}-${version}${n > 1 ? `-${n}` : ""}.${ext}.previous`;

/** A previous name beside *place* that nothing holds yet: an old copy a new one never started to
 *  clear keeps its name, and the next start moves both to the Trash. */
function freePrevious(place, version, ext) {
  for (let n = 1; n <= 50; n++) {
    const candidate = path.join(path.dirname(place), previousName(version, ext, n));
    if (!fs.existsSync(candidate)) return candidate;
  }
  throw new UpdateFailure("put_in_place");
}

/** A CDHash as `codesign` prints it: the SHA-1 or truncated SHA-256 of the code directory. */
const CDHASH = /^CDHash=([0-9a-f]{40}|[0-9a-f]{64})$/m;

/**
 * Whether *app* (a bundle) is this app at *version* for *arch*: its bundle id and version, its
 * architecture, and its seal intact (`codesign --verify --deep --strict`); with *cdhash*, exactly
 * that signed build (`-R '=cdhash H"…"'`). A seal check that fails rejects.
 */
async function isThisApp(app, { version, arch, run, cdhash = "" }) {
  const plist = path.join(app, "Contents", "Info.plist");
  const id = String(await run("/usr/libexec/PlistBuddy", ["-c", "Print :CFBundleIdentifier", plist])).trim();
  const shipped = String(await run("/usr/libexec/PlistBuddy", ["-c", "Print :CFBundleShortVersionString", plist])).trim();
  if (id !== BUNDLE_ID || !sameVersion(shipped, version)) return false;
  const exactly = cdhash ? ["-R", `=cdhash H"${cdhash}"`] : [];
  await run("/usr/bin/codesign", ["--verify", "--deep", "--strict", ...exactly, app]);
  const archs = String(await run("/usr/bin/lipo", ["-archs", path.join(app, "Contents", "MacOS", APP_NAME)])).trim().split(/\s+/);
  return archs.includes(arch === "x64" ? "x86_64" : arch);
}

/**
 * Check the app inside *dmg* and copy it out into *workDir*, the update's private folder: resolves
 * `{path, cdhash}`, the ready copy and the CDHash of the app the image held, which the copy is held
 * to from then on. Nothing beside the app is touched. *run* runs one program and resolves its
 * stdout (its stderr with `{stream: "stderr"}`), or rejects. The image is attached read-only,
 * without a Finder window, at a mount point of its own, and detached again however the check ends.
 */
async function prepareMac({ dmg, version, arch, run, workDir, sha256 = "", size = 0 }) {
  const mount = fs.mkdtempSync(path.join(workDir, "mount-"));
  try {
    await run("/usr/bin/hdiutil", ["attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", mount, dmg]);
  } catch {
    throw new UpdateFailure("contents");
  }
  // Not named `.app`: LaunchServices registers a bundle by that name wherever it sees one, and a
  // copy of this app's bundle id in the private folder could then open a link or `open -b` in the
  // owner's place until Restart to finish. The incoming copy beside the app is named so too.
  const ready = path.join(workDir, `${APP_NAME}.app.ready`);
  try {
    // The image as it is on disk now, once attached, is the release's: a file swapped for it after
    // the download, whose bytes were hashed as they streamed, is not used.
    const onDisk = await hashFile(dmg);
    if (!sha256 || onDisk.sha256 !== sha256 || onDisk.size !== size) throw new UpdateFailure("mismatch");
    const apps = fs.readdirSync(mount).filter((n) => n.endsWith(".app"));
    if (apps.length !== 1 || apps[0] !== `${APP_NAME}.app`) throw new UpdateFailure("contents");
    const fresh = path.join(mount, `${APP_NAME}.app`);
    if (!(await isThisApp(fresh, { version, arch, run }))) throw new UpdateFailure("contents");
    const shown = CDHASH.exec(String(await run("/usr/bin/codesign", ["-d", "-vvv", fresh], { stream: "stderr" })));
    if (!shown) throw new UpdateFailure("contents");
    const cdhash = shown[1];
    fs.rmSync(ready, { recursive: true, force: true });
    await run("/usr/bin/ditto", [fresh, ready]);
    await run("/usr/bin/codesign", ["--verify", "--deep", "--strict", "-R", `=cdhash H"${cdhash}"`, ready]);
    return { path: ready, cdhash };
  } catch (err) {
    fs.rmSync(ready, { recursive: true, force: true });
    throw err instanceof UpdateFailure ? err : new UpdateFailure("contents");
  } finally {
    await run("/usr/bin/hdiutil", ["detach", mount, "-quiet"]).catch(() => run("/usr/bin/hdiutil", ["detach", mount, "-force", "-quiet"]).catch(() => {}));
    // Only the empty mount point: one still attached is left as it is, never emptied.
    try {
      fs.rmdirSync(mount);
    } catch {
      // Still attached, or already gone: the private folder is removed with it later.
    }
  }
}

/**
 * Restart to finish, on macOS: copy the ready copy beside *bundle*, under its hidden incoming name,
 * and check it there as the image's app was checked, held to exactly that build (*cdhash*):
 * resolves that copy's path. A copy that cannot be made, or is not that build, is removed and fails
 * as `put_in_place`, leaving the app as it was.
 */
async function placeMac({ ready, bundle, version, arch, cdhash, run }) {
  const incoming = path.join(path.dirname(bundle), incomingName(version, "app"));
  try {
    if (!cdhash) throw new UpdateFailure("put_in_place");
    fs.rmSync(incoming, { recursive: true, force: true });
    // Made first: a link that appears at the name in between fails here, not inside the copy.
    fs.mkdirSync(incoming);
    await run("/usr/bin/ditto", [ready, incoming]);
    if (!(await isThisApp(incoming, { version, arch, run, cdhash }))) throw new UpdateFailure("put_in_place");
    return incoming;
  } catch {
    try {
      fs.rmSync(incoming, { recursive: true, force: true });
    } catch {
      // Left beside the app; the next start of either copy moves it to the Trash.
    }
    throw new UpdateFailure("put_in_place");
  }
}

/** The sha256 of *file* and its size, read as a stream: `{sha256, size}`. */
function hashFile(file) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash("sha256");
    let size = 0;
    fs.createReadStream(file)
      .on("data", (chunk) => {
        size += chunk.length;
        hash.update(chunk);
      })
      .on("error", reject)
      .on("end", () => resolve({ sha256: hash.digest("hex"), size }));
  });
}

/** Restart to finish, from an AppImage: copy the ready download beside *file*, executable, and hash
 *  the copy there: resolves the copy's path when its bytes are the release's (*sha256*, *size*). A
 *  copy that cannot be made, or differs, is removed and fails as `put_in_place`. */
async function placeAppImage({ ready, file, version, sha256, size, uid = -1 }) {
  const incoming = path.join(path.dirname(file), incomingName(version, "AppImage"));
  try {
    fs.rmSync(incoming, { force: true });
    // Made new: a file or a link that appears at the name in between fails the copy.
    fs.copyFileSync(ready, incoming, fs.constants.COPYFILE_EXCL);
    const st = fs.lstatSync(incoming);
    if (!st.isFile() || st.nlink !== 1 || (uid >= 0 && st.uid !== uid)) throw new UpdateFailure("put_in_place");
    fs.chmodSync(incoming, 0o755);
    const made = await hashFile(incoming);
    if (!sha256 || made.sha256 !== sha256 || made.size !== size) throw new UpdateFailure("put_in_place");
    return incoming;
  } catch {
    fs.rmSync(incoming, { force: true });
    throw new UpdateFailure("put_in_place");
  }
}

/**
 * Put the staged copy in the app's place, once the app has stopped everything it runs: the app
 * to its hidden previous name (`freePrevious`), then the copy to its path. A second rename that
 * fails puts the app back. Synchronous: it runs as the quit's last step. Throws an `UpdateFailure`
 * when it could not: `stranded` when the app could not be put back either, with where it is.
 */
function swapNow({ current, incoming, previous }) {
  try {
    fs.renameSync(current, previous);
  } catch {
    throw new UpdateFailure("put_in_place");
  }
  try {
    fs.renameSync(incoming, current);
  } catch {
    try {
      fs.renameSync(previous, current);
    } catch {
      throw new UpdateFailure("stranded", { path: previous });
    }
    throw new UpdateFailure("put_in_place");
  }
}

/** The program that starts a copy at *place*: the app's executable inside a bundle, or the
 *  AppImage itself. */
function programIn(place, how) {
  return how === "mac" ? path.join(place, "Contents", "MacOS", APP_NAME) : place;
}

/** The old copies and abandoned copies beside *place* (the app or the AppImage): what the first
 *  start of a new copy moves to the Trash. */
function leftBeside(place) {
  const folder = path.dirname(place);
  const prefix = `.${APP_NAME}-`;
  let names = [];
  try {
    names = fs.readdirSync(folder);
  } catch {
    return [];
  }
  return names
    .filter((n) => n.startsWith(prefix) && (n.endsWith(".previous") || n.endsWith(".incoming")))
    .map((n) => path.join(folder, n));
}

// ── The updater ──

/** The shell-store key that holds the update a copy started, for the next start to say how it
 *  ended: `{from, to}`, and `stranded` when the old copy could not be put back. */
const ATTEMPT_KEY = "app-update:attempt";

/**
 * The app's updater. *policy()* says the home's `updates` settings as they stand now
 * (`readUpdatePolicy`). *onState* hears every change, for the app's windows (`appUpdateState`).
 * *quit()* starts the app's quit, whose last step is `swapIfStaged()`, and *relaunch(program)* asks
 * the app to start again on *program* once it has exited. *store* is the shell's store, where the
 * update a copy started waits for the next start (`ATTEMPT_KEY`). *ownsGateway()* says whether the
 * app's quit stops the home's gateway, which a restart for an update then interrupts.
 */
function makeAppUpdater({
  current,
  platform,
  arch,
  isPackaged,
  execPath,
  appImage = "",
  policy,
  workDir,
  run,
  quit,
  relaunch,
  trash,
  request,
  store = null,
  ownsGateway = () => false,
  log = () => {},
  onState = () => {},
  now = Date.now,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  access,
  isMountPoint = (p) => fs.lstatSync(p).dev !== fs.lstatSync(path.dirname(p)).dev,
  getuid = () => (typeof process.getuid === "function" ? process.getuid() : -1),
  runSync = (cmd, args) => require("child_process").execFileSync(cmd, args, { stdio: "ignore", timeout: 10_000 }),
}) {
  const where = whereItRuns({ platform, isPackaged, execPath, appImage, access });
  // The gateway's check's own User-Agent (`self_update.fetch_releases`): it names the product and
  // no version, so the two checks look the same to GitHub (the README's Privacy section).
  const userAgent = USER_AGENT;
  let state = {
    current,
    how: where.how,
    phase: "idle",
    available: null,
    checkedAt: 0,
    progress: null,
    sentence: "",
    /** How the last update this app started ended, said once by the start after it. */
    last: null,
    /** The version downloaded and checked, waiting for Restart to finish: `{version}`, or null. */
    ready: null,
    /** A ready copy the home's settings no longer allowed, and why: `{version, sentence}`, until the
     *  next Update. */
    dropped: null,
    releasesPage: RELEASES_PAGE_URL,
  };
  /** The ready copy in the update's private folder, and what it is held to at Restart to finish:
   *  `{dir, folder: {dev, ino}, path, version, release, cdhash, sha256, size}`, or null. */
  let readyCopy = null;
  let staged = null;
  let timer = null;
  let busy = false;
  /** Set by the quit's first step: a download that ends after it keeps nothing. */
  let stopping = false;
  /** The private folder of the download under way, which the start's sweep leaves alone. */
  let activeDir = null;
  const uid = getuid();
  /** What is said while nothing failed: why this copy updates from the releases page, if it does. */
  const standing = where.how === "page" || where.how === "dev" ? sentenceFor(where.why, where) : "";

  const view = () => ({ ...state, restartsGateway: Boolean(ownsGateway()) });
  const set = (patch) => {
    state = { ...state, ...patch };
    const shown = view();
    onState(shown);
    return shown;
  };
  const remember = (record) => {
    try {
      if (store) store.setItem(ATTEMPT_KEY, JSON.stringify(record));
    } catch (err) {
      log(`the app update could not note itself for the next start: ${err && err.message}`);
    }
  };

  /** Remove the private folder of a ready copy that was not used. */
  const discardReady = () => {
    if (!readyCopy) return;
    const { dir } = readyCopy;
    readyCopy = null;
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch (err) {
      log(`the app update's folder ${dir} could not be removed: ${err && err.message}`);
    }
  };

  /** Whether *rules*, the home's update settings read now, still allow the ready copy's release:
   *  its pin and channel, as the check that offered it applied them. Settings that can't be read don't. */
  const readyAllowed = (rules) =>
    !rules.unreadable && Boolean(chooseRelease([readyCopy.release], { current, channel: rules.channel, pin: rules.pin }).release);

  /** Drop a ready copy *rules* no longer allow, and keep why in the state until the next Update:
   *  the check that follows says what they do allow. Resolves the sentence. */
  const dropDisallowed = (rules) => {
    const { version } = readyCopy;
    const sentence = rules.unreadable ? sentenceFor("unreadable_config", { file: rules.unreadable }) : sentenceFor("ready_not_allowed", { version });
    log(`the app update to ${version} was dropped: the home's update settings no longer allow it`);
    discardReady();
    set({ phase: "idle", ready: null, available: null, sentence: "", dropped: { version, sentence } });
    return sentence;
  };

  /**
   * Whether the ready copy is still the one that was checked: `ok`; `gone` when its folder or the
   * copy is not there; `changed` when its folder is another one, not this account's or not 0700,
   * or the copy is a link or not this account's, or the AppImage's size differs.
   */
  const readyState = (ready) => {
    let folder;
    let copy;
    try {
      folder = fs.lstatSync(ready.dir);
      copy = fs.lstatSync(ready.path);
    } catch (err) {
      return err && err.code === "ENOENT" ? "gone" : "changed";
    }
    const own = (st) => uid < 0 || st.uid === uid;
    const sameFolder = folder.isDirectory() && folder.dev === ready.folder.dev && folder.ino === ready.folder.ino && (folder.mode & 0o777) === 0o700 && own(folder);
    const sameCopy = !copy.isSymbolicLink() && own(copy) && (where.how === "mac" ? copy.isDirectory() : copy.isFile() && copy.size === ready.size);
    return sameFolder && sameCopy ? "ok" : "changed";
  };

  /** The folders an update left in *workDir*, from a quit mid-download, say: each image still
   *  attached in one is detached first, and a folder whose image won't detach is left as it is,
   *  since nothing is ever removed from inside an attached image. */
  async function sweepStale() {
    let names = [];
    try {
      // The app's own folder, as it was made: a link, or a folder not this account's, is not swept.
      const own = fs.lstatSync(workDir);
      if (!own.isDirectory() || (uid >= 0 && own.uid !== uid)) {
        log(`${workDir} is not this app's own folder, so it is not swept`);
        return;
      }
      names = fs.readdirSync(workDir).filter((n) => n.startsWith("app-update-"));
    } catch {
      return;
    }
    for (const name of names) {
      const dir = path.join(workDir, name);
      if (dir === activeDir || (readyCopy && readyCopy.dir === dir)) continue;
      try {
        if (!fs.lstatSync(dir).isDirectory()) continue;
      } catch {
        continue;
      }
      let attached = false;
      let inside = [];
      try {
        inside = fs.readdirSync(dir).filter((n) => n.startsWith("mount-"));
      } catch {
        inside = [];
      }
      for (const child of inside) {
        const mount = path.join(dir, child);
        let mounted = false;
        try {
          mounted = isMountPoint(mount);
        } catch {
          mounted = false;
        }
        if (!mounted) continue;
        try {
          if (platform !== "darwin") throw new Error("no detach here");
          await run("/usr/bin/hdiutil", ["detach", mount, "-force", "-quiet"]);
        } catch {
          attached = true;
        }
      }
      if (attached) {
        log(`a disk image an update left attached in ${dir} could not be detached; the folder is left`);
        continue;
      }
      try {
        await fs.promises.rm(dir, { recursive: true, force: true });
        log(`removed ${dir}, which an update left`);
      } catch (err) {
        log(`could not remove ${dir}, which an update left: ${err && err.message}`);
      }
    }
  }

  async function check({ asked = false } = {}) {
    if (busy) return view();
    if (readyCopy) {
      // A ready update keeps its offer while the home's settings still allow it, read again now
      // with nothing asked of GitHub: Restart to finish is the way on, not another download.
      const now = policy();
      if (readyAllowed(now)) return view();
      dropDisallowed(now);
    }
    const rules = policy();
    // Nothing is asked of GitHub while the home's settings are unknown: the check says why instead.
    if (rules.unreadable) {
      return set({ phase: "failed", available: null, checkedAt: now(), sentence: sentenceFor("unreadable_config", { file: rules.unreadable }) });
    }
    if (!asked && !rules.checkEnabled) return view();
    set({ phase: "checking", sentence: "" });
    try {
      const releases = await fetchReleases({ request, userAgent });
      // An update that started while the list was read keeps its own state.
      if (busy || readyCopy) return view();
      const chosen = chooseRelease(releases, { current, channel: rules.channel, pin: rules.pin });
      if (!chosen.release) {
        const quiet = chosen.why === "current";
        return set({
          phase: quiet ? "current" : "failed",
          available: null,
          checkedAt: now(),
          sentence: quiet ? "" : sentenceFor(chosen.why, { pin: rules.pin, current }),
        });
      }
      const version = String(chosen.release.tag_name).replace(/^v/, "");
      const asset = where.how === "mac" || where.how === "appimage" ? chooseAsset(chosen.release, { platform, arch }) : null;
      const installable = Boolean(asset && asset.ok);
      return set({
        phase: "available",
        available: { version, installable },
        checkedAt: now(),
        sentence: standing || (asset && !asset.ok ? sentenceFor(asset.why) : ""),
      });
    } catch (err) {
      if (busy || readyCopy) return view();
      return set({ phase: "failed", checkedAt: now(), sentence: err instanceof UpdateFailure ? err.message : sentenceFor("unreachable") });
    }
  }

  /** Update: download and check *version*, the one on offer, in a private folder of its own, and
   *  answer at once: `{ok}` once it is under way, else `{ok: false, sentence}`. Its progress and how
   *  it ends are the state's: `ready`, with nothing beside the app changed, or `failed`. */
  function download(version) {
    if (stopping) return { ok: false, sentence: "The app is quitting." };
    if (busy) return { ok: false, sentence: "An update is already under way." };
    if (readyCopy) return { ok: false, sentence: `PersonalClaw ${readyCopy.version} is ready. Restart to finish the update.` };
    if (!state.available || !sameVersion(state.available.version, version)) return { ok: false, sentence: "That version is not the one on offer. Check again." };
    if (!state.available.installable) return { ok: false, sentence: state.sentence || sentenceFor(where.why, where) };
    busy = true;
    (async () => {
      let dir = null;
      try {
        set({ phase: "downloading", sentence: "", last: null, dropped: null, progress: { received: 0, total: 0 } });
        fs.mkdirSync(workDir, { recursive: true, mode: 0o700 });
        fs.chmodSync(workDir, 0o700);
        dir = fs.mkdtempSync(path.join(workDir, "app-update-"));
        fs.chmodSync(dir, 0o700);
        activeDir = dir;
        const made = fs.lstatSync(dir);
        const releases = await fetchReleases({ request, userAgent });
        if (stopping) return;
        const release = (Array.isArray(releases) ? releases : []).find((r) => r && !r.draft && sameVersion(r.tag_name, version));
        if (!release) throw new UpdateFailure("unreachable");
        const picked = chooseAsset(release, { platform, arch });
        if (!picked.ok) throw new UpdateFailure(picked.why);
        const { asset } = picked;
        const file = await downloadAsset(asset, path.join(dir, asset.name), {
          request,
          userAgent,
          onProgress: (progress) => set({ progress }),
          cancelled: () => stopping,
        });
        // The app is quitting: no image is attached, and nothing is kept.
        if (stopping) return;
        set({ phase: "verifying", progress: null });
        let ready = file;
        let cdhash = "";
        if (where.how === "mac") {
          ({ path: ready, cdhash } = await prepareMac({ dmg: file, version: asset.version, arch, run, workDir: dir, sha256: asset.sha256, size: asset.size }));
          // The image is no use once its app is out of it and checked.
          fs.rmSync(file, { force: true });
        } else {
          fs.chmodSync(file, 0o755);
        }
        if (stopping) return;
        readyCopy = {
          dir,
          folder: { dev: made.dev, ino: made.ino },
          path: ready,
          version: asset.version,
          release: { tag_name: release.tag_name, prerelease: Boolean(release.prerelease), draft: false },
          cdhash,
          sha256: asset.sha256,
          size: asset.size,
        };
        set({ phase: "ready", ready: { version: asset.version }, sentence: `PersonalClaw ${asset.version} is downloaded and checked. Restart to finish the update.` });
        log(`the app update to ${asset.version} is ready in ${dir}; nothing beside the app changes until Restart to finish`);
      } catch (err) {
        log(`the app update to ${version} stopped: ${err && err.message}`);
        set({ phase: "failed", progress: null, sentence: err instanceof UpdateFailure ? err.message : sentenceFor("") });
      } finally {
        if (dir && !readyCopy) {
          // During the quit the folder goes at once: the process may not live to finish an rm.
          if (stopping) {
            try {
              fs.rmSync(dir, { recursive: true, force: true });
            } catch (err) {
              log(`the app update's folder ${dir} could not be removed: ${err && err.message}`);
            }
          } else {
            fs.rm(dir, { recursive: true, force: true }, () => {});
          }
        }
        activeDir = null;
        busy = false;
      }
    })();
    return { ok: true };
  }

  /** Restart to finish the update to *version*, the one ready: put the ready copy beside this one,
   *  under a hidden name, then quit as the app always does, whose last step is the swap
   *  (`swapIfStaged`). Answers at once: `{ok}` once it is under way, else `{ok: false, sentence}`. A
   *  copy that cannot be put beside the app leaves the app as it was, and says so. */
  function finish(version) {
    if (stopping) return { ok: false, sentence: "The app is quitting." };
    if (busy) return { ok: false, sentence: "An update is already under way." };
    if (!readyCopy || !sameVersion(readyCopy.version, version)) return { ok: false, sentence: "That version is not the one ready. Check again." };
    // Settings changed since Update: a pin or channel that no longer allows the ready copy drops it,
    // and the check that follows says what they allow, if automatic checks are on.
    const rules = policy();
    if (!readyAllowed(rules)) {
      const sentence = dropDisallowed(rules);
      check({ asked: false }).catch(() => {});
      return { ok: false, sentence };
    }
    // A ready copy that is gone is no failure of the update: Update downloads it again.
    if (readyState(readyCopy) === "gone") {
      const sentence = sentenceFor("ready_gone", { version: readyCopy.version });
      discardReady();
      set({ phase: "available", ready: null, progress: null, sentence });
      return { ok: false, sentence };
    }
    busy = true;
    const ready = readyCopy;
    (async () => {
      try {
        set({ phase: "placing", sentence: `Putting PersonalClaw ${ready.version} in place…` });
        if (readyState(ready) !== "ok") throw new UpdateFailure("put_in_place");
        const incoming =
          where.how === "mac"
            ? await placeMac({ ready: ready.path, bundle: where.bundle, version: ready.version, arch, cdhash: ready.cdhash, run })
            : await placeAppImage({ ready: ready.path, file: where.file, version: ready.version, sha256: ready.sha256, size: ready.size, uid });
        // The app began to quit while the copy was put beside it: nothing is staged, and the copy goes.
        if (stopping) {
          fs.rmSync(incoming, { recursive: true, force: true });
          log(`the app is quitting: the update to ${ready.version} is not put in place`);
          return;
        }
        staged =
          where.how === "mac"
            ? { current: where.bundle, incoming, ext: "app", to: ready.version }
            : { current: where.file, incoming, ext: "AppImage", to: ready.version };
        discardReady();
        remember({ from: current, to: ready.version });
        set({ phase: "restarting", ready: null, sentence: `PersonalClaw ${ready.version} is in place. The app restarts now.` });
        log(`the app update to ${ready.version} is staged at ${staged.incoming}; it takes this copy's place as the app quits`);
        quit();
      } catch (err) {
        staged = null;
        discardReady();
        log(`the app update to ${ready.version} could not be put in place: ${err && err.message}`);
        set({ phase: "failed", ready: null, progress: null, sentence: err instanceof UpdateFailure ? err.message : sentenceFor("put_in_place") });
      } finally {
        // A staged copy holds the updater until the quit: nothing else starts while the app restarts.
        if (!staged) busy = false;
      }
    })();
    return { ok: true };
  }

  /** The quit's last step, beside `swapIfStaged`: a download still under way is let go, its image
   *  detached at once (`-force`) and its folder removed. A folder whose image won't detach is left
   *  for the next start's sweep, which tries again. Never throws. */
  function abandon() {
    const dir = activeDir;
    if (!dir) return;
    activeDir = null;
    let attached = false;
    try {
      for (const child of fs.readdirSync(dir).filter((n) => n.startsWith("mount-"))) {
        const mount = path.join(dir, child);
        let mounted = false;
        try {
          mounted = isMountPoint(mount);
        } catch {
          mounted = false;
        }
        if (!mounted) continue;
        try {
          if (platform !== "darwin") throw new Error("no detach here");
          runSync("/usr/bin/hdiutil", ["detach", mount, "-force", "-quiet"]);
        } catch {
          attached = true;
        }
      }
    } catch {
      // Gone already.
    }
    if (attached) {
      log(`a disk image of the update could not be detached in ${dir}; the next start tries again`);
      return;
    }
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch (err) {
      log(`the app update's folder ${dir} could not be removed: ${err && err.message}`);
    }
  }

  /** The quit's last step: put a staged copy in place, then ask for the start again: on the new
   *  copy, or on this one when the swap failed. Never throws. */
  function swapIfStaged() {
    if (!staged) return false;
    const { current: place, incoming, ext, to } = staged;
    staged = null;
    let startAgain = place;
    let swapped = false;
    try {
      const previous = freePrevious(place, current, ext);
      swapNow({ current: place, incoming, previous });
      swapped = true;
      log(`the app update took this copy's place; the old copy waits at ${previous} until the new one starts`);
    } catch (err) {
      log(`the app update could not take this copy's place: ${err.message}`);
      if (err.why === "stranded") {
        startAgain = err.detail.path;
        remember({ from: current, to, stranded: startAgain });
      }
      try {
        fs.rmSync(incoming, { recursive: true, force: true });
      } catch {
        // Left beside the app; the next start of either copy moves it to the Trash.
      }
    }
    try {
      relaunch(programIn(startAgain, where.how));
    } catch (err) {
      log(`the app could not ask to be started again: ${err && err.message}`);
    }
    return swapped;
  }

  /** The update the last copy started, as this start finds it: `{from, to, ok, sentence}`, or null.
   *  Read once: the note is removed as it is read. */
  function readAttempt() {
    let raw = null;
    try {
      raw = store ? store.getItem(ATTEMPT_KEY) : null;
    } catch {
      raw = null;
    }
    if (!raw) return null;
    try {
      store.removeItem(ATTEMPT_KEY);
    } catch {
      // The next start reads it again, and says the same.
    }
    let record = null;
    try {
      record = JSON.parse(raw);
    } catch {
      return null;
    }
    if (!record || typeof record.from !== "string" || typeof record.to !== "string") return null;
    const { from, to } = record;
    if (sameVersion(current, to)) return { from, to, ok: true, sentence: `Updated from ${from} to ${to}.` };
    if (!sameVersion(current, from)) return null;
    const stranded = typeof record.stranded === "string" && record.stranded;
    return { from, to, ok: false, sentence: stranded ? sentenceFor("stranded", { path: stranded }) : sentenceFor("put_in_place") };
  }

  /** Once this copy has started and its gateway is up: move the old and abandoned copies beside it
   *  to the Trash. */
  function trashPrevious() {
    const place = where.how === "mac" ? where.bundle : where.how === "appimage" ? where.file : "";
    if (!place) return [];
    const left = leftBeside(place);
    for (const p of left) {
      Promise.resolve()
        .then(() => trash(p))
        .then(() => log(`moved ${p} to the Trash`))
        .catch((err) => log(`could not move ${p} to the Trash: ${err && err.message}`));
    }
    return left;
  }

  const schedule = (ms) => {
    timer = setTimer(() => {
      timer = null;
      check({ asked: false }).finally(() => schedule(CHECK_EVERY_MS));
    }, ms);
  };

  return {
    /** Say how the update the last copy started ended, then check after the start and once a day.
     *  Resolves that ending, or null. */
    start() {
      const last = readAttempt();
      if (last) set({ last });
      sweepStale().catch((err) => log(`the folders an update left could not be swept: ${err && err.message}`));
      if (where.how !== "dev") schedule(FIRST_CHECK_AFTER_MS);
      return last;
    },
    /** The quit's first step: no more checks, a ready copy nobody restarted on is removed, and a
     *  download still under way keeps nothing when it ends. */
    stop() {
      stopping = true;
      if (timer) clearTimer(timer);
      timer = null;
      discardReady();
    },
    check,
    download,
    finish,
    swapIfStaged,
    abandon,
    trashPrevious,
    status: view,
  };
}

/**
 * The bridge's half of the app's update: what the app's own windows may ask, and nothing more. A
 * page that is not one of the app's bridged windows is refused (*isAppWindow*). `download` takes the
 * version the page's Update named, and starts only when it is the one on offer; `finish` takes the
 * version the page's confirm named, and goes on only when it is the one ready.
 */
function registerAppUpdateIpc({ ipcMain, channels, updater, isAppWindow, log = () => {} }) {
  const answer = (channel, act) => {
    ipcMain.handle(channel, (event, ...args) => {
      if (!event || !isAppWindow(event.sender)) {
        log(`app update: ${channel} from a page that is not the app's, refused`);
        throw new Error("not one of the app's windows");
      }
      const u = updater();
      if (!u) throw new Error("the app's updater is not running");
      return act(u, ...args);
    });
  };
  answer(channels.appUpdateStatus, (u) => u.status());
  answer(channels.appUpdateCheck, (u) => u.check({ asked: true }));
  const named = (version) => (typeof version === "string" ? version.slice(0, 64) : "");
  answer(channels.appUpdateDownload, (u, version) => u.download(named(version)));
  answer(channels.appUpdateFinish, (u, version) => u.finish(named(version)));
}

module.exports = {
  APP_NAME,
  ATTEMPT_KEY,
  BUNDLE_ID,
  CHECK_EVERY_MS,
  DOWNLOAD_HOSTS,
  FIRST_CHECK_AFTER_MS,
  MAX_ASSET_BYTES,
  MAX_REDIRECTS,
  RELEASES_API,
  RELEASES_PAGE_URL,
  USER_AGENT,
  UpdateFailure,
  assetName,
  chooseAsset,
  chooseRelease,
  compareVersions,
  downloadAsset,
  fetchReleases,
  freePrevious,
  isNewer,
  isPrerelease,
  leftBeside,
  makeAppUpdater,
  parseVersion,
  placeAppImage,
  placeMac,
  prepareMac,
  previousName,
  programIn,
  readUpdatePolicy,
  registerAppUpdateIpc,
  sameVersion,
  sentenceFor,
  swapNow,
  whereItRuns,
};
