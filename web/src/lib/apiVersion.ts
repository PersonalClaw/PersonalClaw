// The SPA's declaration of the wire version this bundle was built for.
//
// This file MIRRORS `src/personalclaw/api_version.py`, which is the ONE origin of
// the number, the supported window, and the bump rule. It does not get to have an
// opinion: `tests/test_api_version_one_origin.py` reds if `CLIENT_API_VERSION` or
// `API_VERSION_HEADER` here disagrees with `API_VERSION` / `VERSION_HEADER` there,
// and it reds if any other file in `web/src` binds an api-version literal.
//
// Why declare at all: without it, a stale cached bundle against a newer gateway
// fails later at whichever field quietly changed shape — a blank screen. Declaring
// the version turns that into a `400 api_version_unsupported` naming both versions
// and which side to upgrade, which the UI can show and act on.
//
// One declaration site by construction: `api.ts` spreads `apiVersionHeaders` into
// the shared header object every request helper already uses, so no call site
// carries the number and none can be forgotten.

/** The wire shape this bundle speaks. Mirrors `api_version.API_VERSION`. */
export const CLIENT_API_VERSION = 1

/** Mirrors `api_version.VERSION_HEADER`. */
export const API_VERSION_HEADER = 'X-PersonalClaw-API-Version'

/** Spread into the shared request headers — the SPA's single declaration site. */
export const apiVersionHeaders: Record<string, string> = {
  [API_VERSION_HEADER]: String(CLIENT_API_VERSION),
}

// The refusal itself needs nothing here: it arrives as PL-8's
// `{error: {code, message}}`, and `errText.ts` already lifts `error.message` into
// the `ApiError` every helper throws — so "this client was built for API version N;
// this gateway speaks M-K. Upgrade the client — reload the page…" is what the user
// reads, wherever that failure surfaces. A `code` constant here would be an export
// nothing imports; the code's origin is `HTTP_ERROR_CODES` in Python.
