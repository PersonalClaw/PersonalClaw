"""The login an address carries, found so it can be masked.

Two readings, one module: a URL's userinfo (``https://ada:pw@host/…``), and the login of an
scp-style address (``ada:pw@host:path``), which has no ``://`` to anchor on.
``security.redact_credentials`` masks what they find before its shape-based passes run, and
``security.strip_url_userinfo`` takes a URL's login out of an address that has to keep working.

The scan is held byte for byte to a plain reference implementation of the rule in
``tests/test_redaction_cost.py``, so it can be read and changed on its own.
"""

import re
from collections.abc import Iterator

# 🔴 URL USERINFO — the one credential shape every shape-based pattern is blind to.
#
# `security._CREDENTIAL_PATTERNS` is entirely SHAPE- or NAME-based: it recognises provider key
# formats (`sk-ant-…`, `ghp_…`) and `name = value` assignments. A credential carried POSITIONALLY,
# in the userinfo slot of a URL, matches neither — so `https://user:s3cr3t@github.com/a/b.git`
# survived every surface in this tree that "redacts" (#406): the diagnostics log stream, the SEL
# audit `resources` field, agent output, and the ConfirmationRequest preview.
#
# Measured before writing this:
#
#   https://user:s3cr3t@github.com/acme/repo.git    -> unchanged        LEAK
#   git clone https://alice:hunter2@git.example…    -> unchanged        LEAK
#   ssh://deploy:pa55@host:22/repo                  -> unchanged        LEAK
#   postgres://admin:dbpass@db.internal:5432/app    -> unchanged        LEAK
#   https://oauth2:ghp_AAAA…@github.com/a/b.git     -> redacted        …by ACCIDENT
#
# That last row is the tell: it was caught only because the password happened to be a GitHub token
# whose SHAPE one of the patterns knows. Any other provider's secret, or any human password, went
# through untouched. A positional rule is the only thing that closes the class.
#
# **A dedicated pre-pass, not another alternative in `_CREDENTIAL_PATTERNS`.** That regex's matches
# are replaced whole, so folding userinfo in would swallow the scheme and host too and turn a
# diagnosable "clone of github.com/acme/repo failed" into `[REDACTED: credential]`. Removing the
# secret must not remove the ability to read the log. So only the userinfo is replaced.
#
# It runs BEFORE the shape-based pass for the same reason the accident above is bad: otherwise a
# `ghp_`-shaped password and an arbitrary one redact to visibly different text.
#
# Scope of the match, deliberately narrow:
#   * anchored on `<scheme>://`, so `alice@example.com` in prose is untouched — a bare email is not
#     a credential and redacting it would make the logs worse;
#   * the credential runs to the LAST `@` of the authority, which ends at a `/`, `?`, `#`,
#     whitespace or a character a URL cannot hold raw (`"`, `<`, `>`, a backtick, a brace, `|`,
#     `\`, `^`), as a URL in a JSON string or an HTML attribute does. A password may hold an `@`
#     (`https://ada:p@ss@host`): stopping at the first one left `ss@host` in every masked line;
#   * a raw character can cut the authority short INSIDE a password (`https://ada:pa/ss@host`).
#     What stands in the host's place is then no host (`_URL_HOST_RE`), and the credential runs on
#     to the last `@` of the whitespace-free run, or of the quotes the URL is in: masking too much
#     there costs some context, too little the password. A real host keeps its path, so
#     `https://host:8080/@scope/pkg` and `https://tok@github.com/org/repo.git@v1` stay readable;
#   * a bare `token@host` with no colon matches too. That is not over-reach: it is exactly how a
#     GitHub PAT is passed in a clone URL (`https://<token>@github.com/…`), so treating userinfo as
#     secret only when it has two parts would miss the most common real case.
# ── Why the scan is anchored on `://` ──
# The rule was once one pattern, `([A-Za-z][A-Za-z0-9+.\-]*://)([^/?#\s@]+)@`, **quadratic in the
# length of a single unbroken run of scheme characters** (#2637): at every offset the scheme run
# re-scanned the rest of the run before failing for want of a `://`. At 64 KB of one alphanumeric
# token it was 99.6% of `redact_credentials` (1.641s of 1.648s), each doubling ~4×. On a live turn
# path (`acp/translate.py` redacts five fields per agent message) one long hash, minified line or
# base64 blob bought seconds of dead air per message.
#
# So the scan is anchored on `://` — a rare literal the engine finds with a memchr-class scan — and
# nothing in it backtracks. `:` is not a scheme character, so the scheme is the maximal run of
# scheme characters before a `://`, from its first letter (`_SCHEME_FIRST_RE`); the authority is
# one possessive run of a class that excludes whatever ends it (a `:` only where it begins another
# `://`), so its last `@` is `str.rfind`'s answer; and a run a cut-short authority found no `@` in
# is not read again for the URLs after it in that run, or `a://b:c/` repeated would be quadratic.
#
# `tests/test_redaction_cost.py` holds this to a plain reference implementation of the rule, byte
# for byte: a long run that IS a credential is replaced whole and an innocuous one is kept
# verbatim, so anything short of byte-identity is a behaviour change to a security primitive.
_URL_AUTHORITY_RE = re.compile(r"://(?P<authority>(?:[^/?#\s\"<>`{}|\\^:]|:(?!//))*+)")

#: A host: a dotted name or address, a bracketed address or `localhost`, with a port of digits or
#: none, or a one-label name with a port. After a credential anything else in its place is the rest
#: of a password (`p@ss/w` leaves `ss`); with none, only what holds a `:` is (`ada:pa/ss`), since
#: `http://db/app` is an ordinary URL.
_URL_HOST_RE = re.compile(
    r"(?:(?i:localhost)|\[[0-9A-Fa-f:.]+(?:%[\w.-]+)?\]|[\w-]+(?:\.[\w-]+)+\.?)(?::[0-9]+)?"
    r"|[\w-]+:[0-9]+"
)

_WHITESPACE_RE = re.compile(r"\s")

#: The scheme character class, spelled as a `str` because the backward walk is `str.rstrip` — one C
#: pass over the run instead of a Python loop. These are the characters a scheme may CONTAIN;
#: `_SCHEME_FIRST_RE` is the ones it may BEGIN with. Both have to keep matching the old pattern's
#: classes, which `TestTheFastScanRestsOnThreeFacts` pins character by character.
_SCHEME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
_SCHEME_FIRST_RE = re.compile(r"[A-Za-z]")


def _scheme_run_start(text: str, sep: int) -> int:
    """Index where the maximal run of scheme characters ending just before `sep` begins.

    Walks backward in doubling windows, so an arbitrarily long run stays linear and the common case
    (`https`, five characters) costs one small slice. No cap on the run length: a cap would be a
    silent behaviour change, and this is a security primitive.
    """
    lo = sep
    window = 64
    while True:
        start = max(0, lo - window)
        head = text[start:lo].rstrip(_SCHEME_CHARS)
        if head or start == 0:
            return start + len(head)
        lo = start
        window *= 2


#: What replaces the userinfo. Whitespace straight after its `:` ends what a second pass reads as
#: the authority (`[REDACTED:`), so it cannot match again: idempotent by construction, not by a
#: guard. No pass in `security.redact_credentials` may match a mask (see its `(?!\[REDACTED:)`).
URL_USERINFO_TAG = "[REDACTED: url credential]"
#: The marks a URL is quoted in, where the text straight before its scheme is one.
_URL_QUOTES = frozenset("\"'")


def _quoted_url_end(text: str, quote: str, start: int) -> int:
    """Where a URL between *quote* marks ends, from *start*: at the next mark, escaped or not, so
    each quoted URL is read up to where the next could begin and the reading stays linear."""
    close = text.find(quote, start)
    stop = len(text) if close == -1 else close
    space = _WHITESPACE_RE.search(text, start, stop)
    return space.start() if space else stop


def url_userinfo_spans(text: str) -> Iterator[tuple[int, int, str]]:
    """``(start, end, scheme)`` for each URL in *text* that carries a credential: where its scheme
    begins, just past the ``@`` that ends the credential, and the scheme. The one reading of it that
    :func:`redact_url_userinfo` and :func:`security.strip_url_userinfo` both use."""
    covered = 0  # just past the last credential found: a `://` before it is inside that credential
    run_end = -1  # the end of the whitespace-free run a cut-short authority was last read in
    run_bare = False  # whether that run holds no `@` past where it was read from
    for m in _URL_AUTHORITY_RE.finditer(text):
        sep = m.start()
        if sep < covered:
            continue
        first = _SCHEME_FIRST_RE.search(text, _scheme_run_start(text, sep), sep)
        if first is None:
            continue  # no scheme, so no URL — `://x@y`, or `1://x@y`
        begin, end = m.span("authority")
        at = text.rfind("@", begin, end)
        host = text[max(at + 1, begin) : end]
        if (at != -1 or ":" in host) and not _URL_HOST_RE.fullmatch(host):
            # Cut short inside a password: on to the last `@` of the run, or of the URL's quotes.
            quote = text[first.start() - 1] if first.start() else ""
            if quote in _URL_QUOTES:
                later = text.rfind("@", end, _quoted_url_end(text, quote, end))
            else:
                if end >= run_end:
                    space = _WHITESPACE_RE.search(text, end)
                    run_end = space.start() if space else len(text)
                    run_bare = False
                later = -1 if run_bare else text.rfind("@", end, run_end)
                run_bare = later == -1
            if later != -1:
                at = later
        if at <= begin:
            continue  # no credential, or an empty one (`://@host`)
        covered = at + 1
        yield first.start(), covered, text[first.start() : sep]


def redact_url_userinfo(text: str) -> tuple[str, list[str]]:
    """Replace `<scheme>://userinfo@` with a redaction tag, keeping scheme and host.

    Separate and public so a caller that only handles URLs (an audit `resources` field, a source
    URL about to be persisted) can use it without the shape-based sweep, and so its behaviour is
    testable on its own.
    """
    warnings: list[str] = []
    out: list[str] = []
    pos = 0

    for start, end, scheme in url_userinfo_spans(text):
        out.append(text[pos:start])
        out.append(f"{scheme}://{URL_USERINFO_TAG}@")
        warnings.append(f"Redacted credential in a {scheme} URL")
        pos = end

    if not warnings:
        return text, []
    out.append(text[pos:])
    return "".join(out), warnings


# 🔴 AN SCP-STYLE ADDRESS CARRIES A LOGIN WITH NO `://` (`user:password@host:path`, as a proxy
# setting, an rsync or a remote is written), so the URL pass never saw it. Read inside one
# whitespace-free run, and only the login is replaced: the password runs to the LAST `@` that a
# host and its `:` follow, so it may hold `@`, `:` and `/`; the login is the run's first `name:`
# (from a letter, digit or `_`) that starts the run or follows an opener (a quote, bracket or
# brace, `=`, `,`, `;` or a backtick) and begins no `://`, so `git@github.com:owner/repo.git` and a
# URL are left alone; and what follows the `@` has to name a host, which an image digest
# (`name:tag@sha256:…`) or a number (`wght@400:`) does not. Linear: one scan finds the runs
# holding both an `@` and a `:`, and each character of one is read a bounded number of times.
_SCP_CANDIDATE_RE = re.compile(r"(?<!\S)(?=[^\s@]*+@)(?=[^\s:]*+:)\S++")
#: A host and its `:`: a bracketed address, or dotted labels of letters and digits (possessive).
_SCP_HOST_RE = re.compile(
    r"(?:\[[0-9A-Fa-f:.]*+\]"
    r"|[A-Za-z0-9]++(?:-++[A-Za-z0-9]++)*+(?:\.[A-Za-z0-9]++(?:-++[A-Za-z0-9]++)*+)*+):"
)
_IPV4_RE = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}")
_SCP_USER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+~-")
_SCP_OPENERS = frozenset("=\"'([{<,;`")
_IMAGE_DIGESTS = frozenset({"md5", "sha1", "sha224", "sha256", "sha384", "sha512"})


def _names_an_scp_host(name: str) -> bool:
    """A bracketed or dotted IPv4 address, or a name with a letter that is no image digest."""
    if name.startswith("[") or _IPV4_RE.fullmatch(name):
        return True
    return name.lower() not in _IMAGE_DIGESTS and any(c.isalpha() for c in name)


def _scp_login_span(run: str) -> tuple[int, int] | None:
    """``(begin, end)`` of the scp-style login in the whitespace-free *run*, or None."""
    at = run.rfind("@")
    while at > 0:
        host = _SCP_HOST_RE.match(run, at + 1)
        if host and _names_an_scp_host(host.group()[:-1]):
            break
        at = run.rfind("@", 0, at)
    else:
        return None
    colon = run.find(":", 0, at)
    while colon != -1:
        begin = colon
        while begin and run[begin - 1] in _SCP_USER_CHARS:
            begin -= 1
        named = begin < colon and (run[begin].isalnum() or run[begin] == "_")
        opens = not begin or run[begin - 1] in _SCP_OPENERS
        if named and opens and not run.startswith("//", colon + 1):
            return begin, at
        colon = run.find(":", colon + 1, at)
    return None


def redact_scp_logins(text: str) -> tuple[str, list[str]]:
    """Replace the login of every scp-style address, keeping its host and path."""
    warnings: list[str] = []
    out: list[str] = []
    pos = 0
    for m in _SCP_CANDIDATE_RE.finditer(text):
        span = _scp_login_span(m.group())
        if span is None:
            continue
        out.append(text[pos : m.start() + span[0]])
        out.append(URL_USERINFO_TAG)
        warnings.append("Redacted credential in an scp-style address")
        pos = m.start() + span[1]
    if not warnings:
        return text, []
    out.append(text[pos:])
    return "".join(out), warnings
