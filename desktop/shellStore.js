/**
 * The shell's own storage scope — the ONE place two paired gateways can bleed into each other,
 * and therefore the only place the desktop shell has to namespace anything (COMPANION-APPS T3.3).
 *
 * The companion guide is explicit that the served SPA needs no help here: it is origin-bound, so
 * browser origin isolation already partitions every `localStorage`/`sessionStorage` bucket per
 * gateway for free. What is NOT partitioned is the shell's own persisted state, because one
 * `desktop/main.js` process spans all N gateways. This file is that scope, and it implements the
 * five-member `KeyValueStore` interface `web/src/lib/endpoints.ts` declares so `endpointScope()`
 * and `clearEndpointState()` work over it verbatim.
 *
 * 🔑 ABSENT, EMPTY, UNPARSEABLE AND UNREADABLE ARE FOUR DIFFERENT FACTS. `read()` never folds them
 * into "no endpoints", because the right behaviour differs for each:
 *
 *   - **absent** — a first launch. Spawn-local, say nothing.
 *   - **empty** — a store exists but holds nothing. Spawn-local, and the switcher can honestly say
 *     "no gateways paired yet" rather than implying it forgot some.
 *   - **unparseable** — something wrote garbage over it. Spawn-local, tell the user, and
 *     **do not overwrite the file**: it is the only copy of whatever was there, and a shell that
 *     silently resets it destroys the evidence and the endpoints in the same stroke.
 *   - **unreadable** — a permissions or I/O failure. Distinct from unparseable because the
 *     content may be perfectly fine and the fix is not "re-pair everything".
 *
 * 🔒 PERMISSIONS ARE THE TRUST BOUNDARY, AND THIS SAYS SO HONESTLY. The store lives at
 * `$PERSONALCLAW_HOME/desktop/shell-store.json`, mode `0600` inside a `0700` directory, written
 * atomically. `inspect()` reports whether that still holds. It deliberately does NOT add an HMAC:
 * any key the shell could use to verify the file would sit in the same directory under the same
 * ownership, so a same-user attacker — who can also rewrite `main.js` itself — would simply
 * re-sign. What a MAC cannot do, and a permission check can, is catch the case that is actually
 * defensible: a file another *local user* or a non-owner process can write. That is why
 * `connectMode.js` treats a store with loose permissions as carrying **no confirmed endpoints**
 * instead of trusting rows it finds there.
 */

const fs = require("fs");
const os = require("os");
const path = require("path");

/** `read()` outcomes. A caller may switch on these and must handle all five. */
const STORE_ABSENT = "absent";
const STORE_EMPTY = "empty";
const STORE_UNPARSEABLE = "unparseable";
const STORE_UNREADABLE = "unreadable";
const STORE_OK = "ok";

const STORE_STATUSES = Object.freeze([
  STORE_ABSENT,
  STORE_EMPTY,
  STORE_UNPARSEABLE,
  STORE_UNREADABLE,
  STORE_OK,
]);

const STORE_DIR_NAME = "desktop";
const STORE_FILE_NAME = "shell-store.json";

/** Where the store lives for a given PersonalClaw home. */
function storePath(home) {
  return path.join(home, STORE_DIR_NAME, STORE_FILE_NAME);
}

/**
 * Read the raw store file and say WHICH of the five things happened.
 *
 * `fsMod` is injected so tests drive real files in a temp dir (they do) or a fake (they can),
 * without either being able to reach the user's real home.
 */
function readStoreFile(file, { fsMod = fs } = {}) {
  let text;
  try {
    text = fsMod.readFileSync(file, "utf8");
  } catch (err) {
    if (err && err.code === "ENOENT") return { status: STORE_ABSENT, data: {}, reason: "no_file" };
    return {
      status: STORE_UNREADABLE,
      data: {},
      reason: err && err.code ? String(err.code) : "read_failed",
    };
  }
  if (!text || !text.trim()) return { status: STORE_EMPTY, data: {}, reason: "zero_length" };
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    return {
      status: STORE_UNPARSEABLE,
      data: {},
      reason: "invalid_json",
      detail: err && err.message ? String(err.message) : "",
    };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { status: STORE_UNPARSEABLE, data: {}, reason: "not_an_object" };
  }
  // Values are strings by the `KeyValueStore` contract. A non-string value is a foreign writer,
  // reported rather than coerced — coercion would let `{"companion:endpoints": {...}}` masquerade
  // as a stored registry with different parse semantics.
  const data = {};
  const foreign = [];
  for (const [k, v] of Object.entries(parsed)) {
    if (typeof v === "string") data[k] = v;
    else foreign.push(k);
  }
  if (foreign.length) {
    return { status: STORE_UNPARSEABLE, data, reason: "non_string_values", detail: foreign.join(",") };
  }
  return { status: STORE_OK, data, reason: "" };
}

/**
 * Is the store still owner-only?
 *
 * `{exists, ownedByUs, ownerOnly, mode, dirOwnerOnly, safe, reason}`. `safe` is the single
 * question callers ask; the parts are reported so a warning can say which one failed.
 */
function inspect(file, { fsMod = fs, uid } = {}) {
  const me = uid === undefined ? (typeof process.getuid === "function" ? process.getuid() : null) : uid;
  let st;
  try {
    st = fsMod.statSync(file);
  } catch (err) {
    if (err && err.code === "ENOENT") {
      return { exists: false, ownedByUs: true, ownerOnly: true, mode: null, dirOwnerOnly: true, safe: true, reason: "absent" };
    }
    return { exists: false, ownedByUs: false, ownerOnly: false, mode: null, dirOwnerOnly: false, safe: false, reason: "stat_failed" };
  }
  const mode = st.mode & 0o777;
  // `getuid` is absent on Windows, where POSIX ownership does not apply. Reported as unknown
  // rather than asserted safe — see `reason`.
  const ownedByUs = me === null ? true : st.uid === me;
  const ownerOnly = (mode & 0o077) === 0;

  let dirOwnerOnly = true;
  let dirReason = "";
  try {
    const dst = fsMod.statSync(path.dirname(file));
    dirOwnerOnly = (dst.mode & 0o022) === 0 && (me === null || dst.uid === me);
    if (!dirOwnerOnly) dirReason = "directory_writable_by_others";
  } catch {
    dirOwnerOnly = false;
    dirReason = "directory_unreadable";
  }

  const safe = ownedByUs && ownerOnly && dirOwnerOnly;
  let reason = "";
  if (!ownedByUs) reason = "owned_by_another_user";
  else if (!ownerOnly) reason = "readable_or_writable_by_others";
  else if (!dirOwnerOnly) reason = dirReason;
  else if (me === null) reason = "ownership_not_checkable_on_this_platform";
  return { exists: true, ownedByUs, ownerOnly, mode, dirOwnerOnly, safe, reason };
}

/** Write the whole store atomically at `0600`, creating a `0700` directory if needed. */
function writeStoreFile(file, data, { fsMod = fs } = {}) {
  const dir = path.dirname(file);
  fsMod.mkdirSync(dir, { recursive: true, mode: 0o700 });
  // Same-directory temp + rename: a crash mid-write leaves the previous store intact rather than
  // a truncated one, and rename within a directory is atomic on every platform we ship to.
  const tmp = path.join(dir, `.${STORE_FILE_NAME}.${process.pid}.${Date.now()}.tmp`);
  fsMod.writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
  try {
    fsMod.renameSync(tmp, file);
  } catch (err) {
    try {
      fsMod.unlinkSync(tmp);
    } catch {
      /* the temp file is the lesser problem */
    }
    throw err;
  }
  // `writeFileSync`'s mode is masked by umask on creation, and an existing file keeps its old
  // mode entirely, so this is not redundant.
  try {
    fsMod.chmodSync(file, 0o600);
  } catch {
    /* reported by `inspect`, not fatal */
  }
}

/**
 * A `KeyValueStore` (the five-member interface `endpoints.ts` declares) backed by one JSON file.
 *
 * Loaded once at construction and written through on every mutation, because the whole store is a
 * handful of short strings and a read-modify-write per key would be slower and racier than this.
 * `status`/`storeReason` carry the load outcome so a caller can tell the four cases apart AFTER
 * construction — a constructor that threw on a corrupt store would take the switcher down with it.
 */
function openShellStore({ home, fsMod = fs, uid, log } = {}) {
  const resolvedHome = home || process.env.PERSONALCLAW_HOME || path.join(os.homedir(), ".personalclaw");
  const file = storePath(resolvedHome);
  const loaded = readStoreFile(file, { fsMod });
  const perms = inspect(file, { fsMod, uid });
  const data = { ...loaded.data };
  let writable = true;

  const persist = () => {
    if (!writable) return false;
    try {
      writeStoreFile(file, data, { fsMod });
      return true;
    } catch (err) {
      writable = false;
      if (log) log(`shell store is not writable (${err && err.message}); changes are this-session only`);
      return false;
    }
  };

  return {
    file,
    /** One of `STORE_STATUSES`. */
    status: loaded.status,
    storeReason: loaded.reason || "",
    storeDetail: loaded.detail || "",
    permissions: perms,
    /** True once a write has failed; the shell keeps working in memory. */
    get readOnly() {
      return !writable;
    },
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null;
    },
    setItem(key, value) {
      data[String(key)] = String(value);
      persist();
    },
    removeItem(key) {
      delete data[String(key)];
      persist();
    },
    get length() {
      return Object.keys(data).length;
    },
    key(index) {
      const keys = Object.keys(data);
      return index >= 0 && index < keys.length ? keys[index] : null;
    },
  };
}

module.exports = {
  STORE_ABSENT,
  STORE_EMPTY,
  STORE_UNPARSEABLE,
  STORE_UNREADABLE,
  STORE_OK,
  STORE_STATUSES,
  STORE_DIR_NAME,
  STORE_FILE_NAME,
  storePath,
  readStoreFile,
  writeStoreFile,
  inspect,
  openShellStore,
};
