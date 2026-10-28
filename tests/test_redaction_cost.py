"""`redact_credentials` was quadratic in one unbroken run of scheme characters (#2637).

Measured on `origin/main`, one unbroken alphanumeric token, each doubling ~4×:

    8 KB    0.029s          256 KB     30.475s
   16 KB    0.109s   (×3.7)
   32 KB    0.439s   (×4.0)
   64 KB    1.752s   (×4.0)

**The carrier was not the credential patterns.** Profiled at 64 KB, `.sub` on the old
single-regex `_URL_USERINFO_RE` — `([A-Za-z][A-Za-z0-9+.\\-]*://)([^/?#\\s@]+)@` — was 1.641s of
1.648s, **99.6%**. `_CREDENTIAL_PATTERNS`, the obvious suspect, was 0.004s (0.2%); the base64 pass
was 0.002s. A backtracking engine attempts the match at every offset, and at each one that
variable-length scheme run re-scans the rest of the run before failing for want of a `://`.

Why that is a *user-visible* defect and not a slow function: `redact_credentials` sits on live turn
paths — `acp/translate.py` alone redacts five fields per agent message — so one tool result carrying
a long hash, a minified line, or a base64 blob bought seconds of dead air per message, with nothing
to distinguish "the model is thinking" from "the app is wedged".

The fix anchors the scan on `://` and recovers the scheme by walking backward, which is why the
whole of this file is a **differential** test: the obvious alternative fix — capping the run length
and masking anything over the cap — would have been worse for the common case. A long run that IS a
credential is already replaced whole; a long run that is innocuous (a base64 image, a minified
bundle) is preserved verbatim, and a cap would silently mask it. So the requirement here is not
"still redacts credentials", it is **byte-identical output on every input**, against a reference
that says the rule as plainly as it can be said.

The rule has since changed on purpose. It stopped at the FIRST `@`, so a password holding one
(`https://ada:p@ss@host`) left its tail, `ss@host`, in every line it masked, and it did not see an
scp-style `user:password@host:path` at all. The credential now runs to the last `@` of the
authority, or past an authority a password cut short, and an scp-style login is masked too. The
reference below was rewritten with the rule, from plain loops over the text, so the fast scan in
`address_logins.py` is still held to byte-identity on every input here.

`TestTheFastScanRestsOnThreeFacts` pins what the fast scan's correctness rests on:

  * `:` is not a scheme character, so the run of scheme characters ending at a `://` can only be
    the maximal one, and the backward walk may take it without trying a shorter split.
  * the authority class excludes every character that ends an authority and holds `@`, so its one
    possessive run IS the authority, and the last `@` in it is `str.rfind`'s answer.
  * the scheme class and the scheme pattern agree character by character.

Each corpus is one table test per property (docs/maintainers/testing.md): every row is a subtest
named by its input, and each test pins its row count, so a dropped row fails. The cost tests stay
one test per shape, so each shape's cost is still measured on its own.
"""

from __future__ import annotations

import itertools
import random
import re
import statistics
import string
import time

import pytest
from table_rows import row

from personalclaw import address_logins as A
from personalclaw import security as S

pytestmark = pytest.mark.security


# ── the reference: the rule, said as plainly as it can be ──
# Plain loops over the text, no memo and no possessive run, so the fast scan's shortcuts are what
# is under test. It shares only leaf grammars with `address_logins.py` and `security.py` (what a
# host is, the tag, the shape-based patterns), never the scanning. The shape-based pass is spelled
# as it stood before #2717, a fresh leftmost `replace` per match, which the splice tests below hold
# the fast one to.

_SCHEME_CHAR = set(string.ascii_letters + string.digits + "+.-")
#: What ends an authority, besides whitespace and a `:` that begins another `://`.
_AUTHORITY_END = set('/?#"<>`{}|\\^')


def _ends_authority(text: str, i: int) -> bool:
    ch = text[i]
    return ch.isspace() or ch in _AUTHORITY_END or (ch == ":" and text.startswith("//", i + 1))


def _reference_url_spans(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, scheme)`` of each URL credential, found the slow and obvious way."""
    spans: list[tuple[int, int, str]] = []
    covered = 0
    i = 0
    while (sep := text.find("://", i)) != -1:
        begin = end = sep + 3
        while end < len(text) and not _ends_authority(text, end):
            end += 1
        i = end
        if sep < covered:
            continue  # inside the credential just found
        run = sep
        while run > 0 and text[run - 1] in _SCHEME_CHAR:
            run -= 1
        first = next((k for k in range(run, sep) if text[k] in string.ascii_letters), None)
        if first is None:
            continue  # no scheme
        at = text.rfind("@", begin, end)
        host = text[max(at + 1, begin) : end]
        if (at != -1 or ":" in host) and not A._URL_HOST_RE.fullmatch(host):
            # Cut short inside a password: read on, to whitespace or the quote the URL is in.
            stop = len(text)
            if first and text[first - 1] in "\"'":
                close = text.find(text[first - 1], end)
                stop = len(text) if close == -1 else close
            stop = next((k for k in range(end, stop) if text[k].isspace()), stop)
            later = text.rfind("@", end, stop)
            if later != -1:
                at = later
        if at <= begin:
            continue  # no credential, or an empty one
        covered = at + 1
        spans.append((first, covered, text[first:sep]))
    return spans


def _reference_redact_url_userinfo(text: str) -> tuple[str, list[str]]:
    spans = _reference_url_spans(text)
    if not spans:
        return text, []
    out: list[str] = []
    pos = 0
    for start, end, scheme in spans:
        out.append(text[pos:start] + f"{scheme}://{A.URL_USERINFO_TAG}@")
        pos = end
    out.append(text[pos:])
    return "".join(out), [f"Redacted credential in a {scheme} URL" for _s, _e, scheme in spans]


def _reference_scp_spans(text: str) -> list[tuple[int, int]]:
    """Where each scp-style login lies: the last `@` of a whitespace-free run that a host and its
    `:` follow, back to the first `name:` there that starts the run or follows an opener."""
    spans: list[tuple[int, int]] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        stop = pos
        while stop < len(text) and not text[stop].isspace():
            stop += 1
        run, base, pos = text[pos:stop], pos, stop
        at = next(
            (
                k
                for k in range(len(run) - 1, 0, -1)
                if run[k] == "@"
                and (host := A._SCP_HOST_RE.match(run, k + 1))
                and A._names_an_scp_host(host.group()[:-1])
            ),
            None,
        )
        if at is None:
            continue
        for colon in (c for c in range(at) if run[c] == ":"):
            begin = colon
            while begin and run[begin - 1] in A._SCP_USER_CHARS:
                begin -= 1
            if (
                begin < colon
                and (run[begin].isalnum() or run[begin] == "_")
                and (not begin or run[begin - 1] in A._SCP_OPENERS)
                and not run.startswith("//", colon + 1)
            ):
                spans.append((base + begin, base + at))
                break
    return spans


def _reference_redact_credentials(text: str) -> tuple[str, list[str]]:
    result, warnings = _reference_redact_url_userinfo(text)
    spans = _reference_scp_spans(result)
    if spans:
        out, pos = [], 0
        for begin, end in spans:
            out.append(result[pos:begin] + A.URL_USERINFO_TAG)
            pos = end
        result = "".join(out) + result[pos:]
        warnings += ["Redacted credential in an scp-style address"] * len(spans)
    result, webhook_warnings = S.redact_webhook_urls(result)
    warnings += webhook_warnings
    for m in S._CREDENTIAL_PATTERNS.finditer(result):
        matched = m.group()
        result = result.replace(matched, "[REDACTED: credential]", 1)
        warnings.append(f"Redacted credential pattern: {matched[:20]}...")
    for m in S._B64_CHUNK_RE.finditer(text):
        chunk = m.group()
        if S._decode_b64_safe(chunk):
            result = result.replace(chunk, "[REDACTED: encoded credential]", 1)
            warnings.append(f"Redacted base64-encoded credential ({len(chunk)} chars)")
    return result, warnings


# ── the corpus ──

#: Real credential shapes. If the rewrite changed WHICH spans are masked, these fail first.
CREDENTIALS = [
    "https://user:s3cr3t@github.com/acme/repo.git",
    "ssh://deploy:pa55@host:22/repo",
    "postgres://admin:dbpass@db.internal:5432/app",
    "https://fake-github-token-1@github.com/a/b.git",
    "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "sk-ant-api03-" + "z" * 30,
    "fake-openai-project-1" + "Q" * 24,
    "api_key=abcdefghij",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln",
    "-----BEGIN RSA PRIVATE KEY-----",
    "fake-bot-token-1",
    "AIza" + "b" * 35,
    "client_secret: swordfish99",
    # A password holding `@`, `:` or `/`, raw, and an scp-style login.
    "https://ada:p@ss@git.example.com/acme/repo.git",
    "https://ada:pa:ss@git.example.com/acme/repo.git",
    "https://ada:pa/ss@git.example.com/acme/repo.git",
    "https://ada:p@s:s/w@git.example.com/acme/repo.git",
    "https://ada:p@ss/w0rd@git.example.com/acme/repo.git",
    '{"remote":"https://ada:p@ss/w@git.example.com","by":"ops@example.com"}',
    "fatal: unable to access 'https://ada:pa/ss@git.example.com/r.git/': URL rejected",
    "deploy:p@ss/w0rd@build.example.com:acme/repo.git",
    "rsync -av ada:hunter2@backup.example.com::data/srv",
    "HTTPS_PROXY=ada:pa:ss@proxy.example.com:3128",
    "scp ada:s3cr3t@[2001:db8::1]:/srv/x .",
]

#: Near-misses. A rule that fired on these would make every log worse, so they pin the other edge.
NEAR_MISSES = [
    "mail alice@example.com about it",
    "git@github.com:owner/repo.git",
    "https://api.example.com/x?to=a@example.com",
    "https://github.com/acme/repo.git",
    "see docs at https://example.com/a/b#frag",
    "1://user@host",  # no letter starts the run, so no scheme, so no match
    "://user@host",
    "-://user@host",
    ".://user@host",
    "the password is unset",
    "fake-key-short",
    "bearer x",
    "https://registry.example.com/@scope/pkg",
    "https://registry.example.com:8443/@scope/pkg",
    "http://localhost/@user",
    "pip install git+https://github.com/org/repo.git@v1.2#egg=x",
    '{"url":"https://api.example.com","mail":"ops@example.com"}',
    "image nginx:1.25@sha256:0123456789abcdef0123456789abcdef",
    "css2?family=Inter:wght@100..900: a variable font",
    "family=Roboto:wght@400:",
    # A URL is no scp-style login, whatever `@name:` its path holds (a chat handle, here).
    "https://matrix.example/#/@alice:matrix.example",
    "https://example.com/p@host.example:x",
]

#: Long innocuous runs — the shapes this defect actually fired on. Each is preserved VERBATIM
#: today, which is exactly why a length cap was the wrong fix.
LONG_INNOCUOUS = [
    "".join(random.Random(1).choice(string.hexdigits.lower()) for _ in range(4096)),
    "".join(random.Random(2).choice(string.ascii_letters + string.digits) for _ in range(4096)),
    "data:image/png;base64," + "iVBORw0KGgoAAAANSUhEUg" * 120,
    "a" * 2048 + "://" + "b" * 2048,
    "x" * 3000 + "://user@host",
    "https://" + "u" * 3000 + "@host/path",
    "-" * 1000 + "." * 1000 + "+" * 1000,
]

#: Structural adversaries: the boundaries the backward walk has to land on exactly.
ADVERSARIAL = [
    "",
    " ",
    "\n\t",
    "://",
    "://@",
    "://x@",
    "a://b@x://y@z",
    "a+b://u@h",
    "1abc://user@h",
    "x1://user@h",
    # A `:` immediately before the scheme. These are the cases that tell whether the backward walk
    # stops where the old character class stopped: `:` is not a scheme character, so the run is
    # `y`, not `x:y`, and the warning names `y`. Added after a mutation that put `:` into
    # `_SCHEME_CHARS` was caught only by the structural pin and not by this corpus.
    "x:y://u@h",
    "1:2://u@h",
    "http:://u@h",
    "mailto:x://u@h",
    "a.b-c+d://u@h",
    "http://a@b http://a@b http://a@b",
    f"https://{A.URL_USERINFO_TAG}@host/x",
    f"repo_url: https://{A.URL_USERINFO_TAG}@github.com/a/b.git",
    "q://u@h " * 200,
    "s://" + "a" * 100 + "@h",
    # The cut-short reading: its quote, its whitespace, and a run that holds no `@`.
    "a://b:c/d",
    "a://b:c/d@e",
    "a://b:c/d@e f@g",
    '"a://b:c/d@e"@f',
    "'a://b:c/d@e'@f",
    "a://b:c/a://b:c/a://b:c/@",
    # A run found to hold no `@` is not looked in again; the next run is.
    "a://b:c/d a://b:c/d@e",
    "a://b:c/d@e a://b:c/f a://b:c/g@h",
    "'a://b:c/d' a://b:c/d@e",
    "a://b:c/d '@e' a://b:c/f@g",
    "a://@b:c/d@e",
    "a://b:/d@e",
    "a://b:1/d@e",
    "a://[::1]:1/d@e",
    "a://[::1:/d@e",
    "a://u@db/app@x",
    "a://u@db:1/app@x",
    "a://u@localhost/app@x",
    # The scp reading: the first login of the run, the last `@` a host follows, and openers.
    "a:b@c:d",
    "a:b@c:d e:f@g:h",
    "=a:b@c:d",
    "(a:b@c:d)",
    "x/a:b@c:d",
    "a:b@c.d:e,f:g@h:i",
    "a:b@sha256:c",
    "a:b@1:c",
    "a:b@1.2.3.4:c",
    "a:b@[::1]:c",
    ".a:b@c:d",
    "a:b@c:d@e:f",
]

#: Unicode, including the separators a naive character walk gets wrong.
UNICODE = [
    "héllo://üser@host/ünicode",
    "日本語://ユーザ@host",
    "https://ü:p@host/x",
    "\u00a0://u@h",
    "\u2028://u@h",
    "emoji 🔑 https://u:p@h/x",
    "\ufeffhttps://u:p@h/x",
]


def _mixed_document(n: int) -> str:
    """A realistic mixed document: prose, URLs, hashes, and real credentials interleaved."""
    rnd = random.Random(99)
    words = ["deploy", "gateway", "returned", "409", "conflict", "session", "resume", "lock"]
    parts: list[str] = []
    size = 0
    i = 0
    while size < n:
        i += 1
        if i % 17 == 0:
            chunk = f"https://api.example.com/v1/runs/{i}?trace=abc"
        elif i % 23 == 0:
            chunk = "sk-ant-api03-" + "".join(rnd.choice(string.ascii_letters) for _ in range(30))
        elif i % 13 == 0:
            chunk = "sha256:" + "".join(rnd.choice("0123456789abcdef") for _ in range(64))
        elif i % 29 == 0:
            chunk = "https://user:hunter2@git.example.com/acme/repo.git"
        else:
            chunk = rnd.choice(words)
        parts.append(chunk)
        size += len(chunk) + 1
    return " ".join(parts)


CORPUS = CREDENTIALS + NEAR_MISSES + LONG_INNOCUOUS + ADVERSARIAL + UNICODE


class TestTheOutputIsByteIdentical:
    """Any input where the new implementation differs is a defect in the change, not a fix."""

    def test_the_corpus_redacts_exactly_as_the_reference(self, subtests):
        assert len(CORPUS) == 111, "a row was added to or dropped from CORPUS: pin the new count"
        for text in CORPUS:
            with row(subtests, text=text):
                expected = _reference_redact_credentials(text)
                got = S.redact_credentials(text)
                assert got == expected, f"{text!r}: expected {expected!r}, got {got!r}"

    def test_a_mixed_document_redacts_exactly_as_the_reference(self, subtests):
        sizes_kb = (1, 16, 64)
        assert len(sizes_kb) == 3, "a size was added or dropped: pin the new count"
        for kb in sizes_kb:
            with row(subtests, kb=kb):
                text = _mixed_document(kb * 1024)
                got = S.redact_credentials(text)
                assert got == _reference_redact_credentials(
                    text
                ), f"the {kb} KB mixed document redacts differently from the reference"

    def test_the_url_pre_pass_alone_is_identical_too(self, subtests):
        """`redact_url_userinfo` is public — a caller that only handles URLs uses it directly, so
        its own return value is part of the contract, not just its contribution downstream."""
        assert len(CORPUS) == 111, "a row was added to or dropped from CORPUS: pin the new count"
        for text in CORPUS:
            with row(subtests, text=text):
                expected = _reference_redact_url_userinfo(text)
                got = A.redact_url_userinfo(text)
                assert got == expected, f"{text!r}: expected {expected!r}, got {got!r}"

    def test_exhaustive_over_the_characters_that_decide_a_match(self):
        """Every string up to length 5 over the alphabet the rule actually branches on.

        A hand-written corpus tests the cases its author thought of; the backward walk's failure
        modes are off-by-one boundaries nobody plants on purpose. 19,608 strings.
        """
        alphabet = "a1:/@.-"
        checked = 0
        for n in range(6):
            for tup in itertools.product(alphabet, repeat=n):
                text = "".join(tup)
                assert S.redact_credentials(text) == _reference_redact_credentials(text), text
                checked += 1
        assert checked == sum(len(alphabet) ** n for n in range(6))

    def test_exhaustive_over_quotes_and_whitespace_too(self):
        """The same over what bounds a cut-short credential and an scp-style run: a quote before a
        scheme, and whitespace. 9,331 strings."""
        alphabet = 'a:/@" '
        checked = 0
        for n in range(6):
            for tup in itertools.product(alphabet, repeat=n):
                text = "".join(tup)
                assert S.redact_credentials(text) == _reference_redact_credentials(text), text
                checked += 1
        assert checked == sum(len(alphabet) ** n for n in range(6))

    def test_randomised_over_credential_fragments(self):
        """Fuzz over fragments chosen to collide: separators, tags, and real key prefixes."""
        pieces = list("aZ19:/@?#.-+_= \n'\",;([<") + [
            "://",
            "http",
            "https",
            "user",
            "sk-ant-api03-" + "A" * 25,
            "ghp_" + "B" * 24,
            "AKIA" + "C" * 16,
            "api_key=",
            "bearer ",
            "password:",
            A.URL_USERINFO_TAG,
            "A" * 45,
            "A" * 44 + "==",
            "@host.example:",
            "sha256",
            "[::1]",
            "localhost",
            ":8080",
        ]
        rnd = random.Random(20260907)
        for _ in range(4000):
            text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(0, 12)))
            assert S.redact_credentials(text) == _reference_redact_credentials(text), repr(text)


#: Every end an authority run has to stop at: each of `_AUTHORITY_END`, whitespace, and a `://`.
AUTHORITY_ENDS = sorted(_AUTHORITY_END) + [" ", "\n", "\t", "://"]


class TestTheFastScanRestsOnThreeFacts:
    """Pinned because the fast scan is only correct while these hold.

    Add `:` to the scheme class, or let the authority class take a character that ends an
    authority, and the fast scan stops agreeing with the reference — silently, on inputs no
    functional test plants.
    """

    def test_a_colon_is_not_a_scheme_character(self):
        """So the run ending at a `://` can only be the maximal one, and the backward walk is
        allowed to take it without considering any shorter split."""
        assert ":" not in A._SCHEME_CHARS

    def test_the_authority_run_stops_at_every_character_that_ends_one(self, subtests):
        """So the one possessive run is the whole authority, and nothing past it is read as one."""
        assert len(AUTHORITY_ENDS) == 16, "an authority end was added or dropped: pin the new count"
        for end in AUTHORITY_ENDS:
            with row(subtests, end=end):
                m = A._URL_AUTHORITY_RE.match(f"://a@b{end}c@d")
                got = m.group("authority") if m else None
                assert got == "a@b", f"{end!r}: expected the authority 'a@b', got {got!r}"

    def test_the_authority_run_holds_every_at_sign_in_it(self):
        """So the last `@` of the authority is `rfind`'s answer: `ada:p@ss@host` is one login."""
        m = A._URL_AUTHORITY_RE.match("://ada:p@ss@host:22/x")
        assert m is not None and m.group("authority") == "ada:p@ss@host:22"

    def test_the_scheme_class_and_the_pattern_agree(self):
        """`_SCHEME_CHARS` is a `str` for `rstrip` and the reference reads a character class. A
        divergence between them is the one way the backward walk can find the wrong run start."""
        old_class = re.compile(r"[A-Za-z0-9+.\-]")
        for ch in A._SCHEME_CHARS:
            assert old_class.fullmatch(ch), ch
        for code in range(0x20, 0x7F):
            ch = chr(code)
            assert bool(old_class.fullmatch(ch)) == (ch in A._SCHEME_CHARS), ch

    def test_the_tag_is_still_unmatchable_by_construction(self):
        """The tag holds a space straight after its `:`, so an authority read from it is
        `[REDACTED:` with nothing after it, and an scp-style login needs a `name:` in the run
        before the `@`, which the tag's last word does not have."""
        assert "[REDACTED: " in A.URL_USERINFO_TAG
        once = f"https://{A.URL_USERINFO_TAG}@host/x and {A.URL_USERINFO_TAG}@host:path"
        assert S.redact_credentials(once) == (once, [])


#: Linear cost grows ×4 for ×4 input and quadratic cost ×16: a growth past this is not linear.
_NOT_LINEAR = 8.0


#: How many ticks of the thread clock each sample spans at least, so a clock coarser than one run
#: (Windows' ticks in about 15.6 ms) still reads it to within a hundredth.
_TICKS = 100


#: How many rounds :func:`_growth` measures, each a sample of the small input and then the large.
_ROUNDS = 5


def _cpu_cost(redact, text: str, clock=time.thread_time, resolution: float | None = None) -> float:
    """The CPU time this thread spent in one run of *redact* over *text*: one sample, which repeats
    the run until *clock* has advanced :data:`_TICKS` of its *resolution* (the thread clock's own by
    default), and divides by the runs it made.

    CPU time, not wall time. On a CI runner whose 4 CPUs ran 4 workers, the three runs of the
    large scp input, back to back, sat inside one stretch with no CPU for them (run 40477167976),
    and their best came out ×17.3 the small input's, for a reading measured at ×3.4-×3.9 for ×4
    input at every size up to 320k units. A thread's CPU time leaves out the time it waited for a
    CPU, or for the GIL behind another thread, and counts the work, which a quadratic reading
    still multiplies by 16 (``test_a_quadratic_redactor_fails_the_bound``)."""
    least = _TICKS * (resolution or time.get_clock_info("thread_time").resolution)
    runs, started = 0, clock()
    while True:
        redact(text)
        runs += 1
        spent = clock() - started
        if spent >= least:
            return spent / runs


def _growth(redact, small: str, large: str, **clock) -> float:
    """How many times *large* costs *small* in CPU time: the median, over :data:`_ROUNDS` rounds,
    of a sample of the large input over a sample of the small one taken just before it
    (:func:`_cpu_cost`, given *clock*).

    In rounds, not each input's samples back to back: on a CI runner the three samples of the
    large input, one run each and taken in one stretch of about 85 ms, each cost twice what a run
    did around them, while the small input's, taken after, did not, and a linear cost read ×8.0
    (run 40746112284, python-suite 1/3 on Linux). A stretch like that falls on one round, or
    across two, and the median leaves it out; a quadratic cost reads ×16 in every round."""
    ratios = []
    for _ in range(_ROUNDS):
        cheap = _cpu_cost(redact, small, **clock)
        dear = _cpu_cost(redact, large, **clock)
        ratios.append(dear / max(cheap, 1e-6))
    return statistics.median(ratios)


class TestTheCostTracksNothingQuadratic:
    """Driven from the single-token curve, not a document-size fixture.

    512 KB of prose redacted in 40ms on `origin/main` and proved nothing: the cost tracked the
    length of one unbroken run, not the byte count, which is why a size ceiling was never going to
    bound it.
    """

    @staticmethod
    def _unbroken(n: int) -> str:
        rnd = random.Random(1234)
        return "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(n))

    def test_the_fixture_really_is_one_unbroken_run(self):
        """Vacuity floor. A token containing `/` or `_` breaks into short scheme runs and costs
        nothing even on `origin/main` — measured at 0.011s for 64 KB of the base64 alphabet
        against 1.752s for the same size of alphanumerics. Get this wrong and the bound below
        passes on a tree that still has the defect.
        """
        token = self._unbroken(8192)
        assert len(token) == 8192
        assert not set(token) - set(A._SCHEME_CHARS), "the fixture is not one unbroken scheme run"

    def test_a_quarter_megabyte_single_token_is_bounded(self):
        """A coarse floor, not a benchmark. This shape cost 30.475s before; it now costs ~0.025s.
        If this ever reds, the scan is quadratic again — the number is not the knob.
        """
        token = self._unbroken(256 * 1024)
        started = time.perf_counter()
        out, warnings = S.redact_credentials(token)
        elapsed = time.perf_counter() - started
        assert out == token and warnings == [], "an innocuous token must survive verbatim"
        assert elapsed < 3.0, f"256 KB of one unbroken token took {elapsed:.2f}s"

    def test_quadrupling_the_run_does_not_multiply_the_cost_by_sixteen(self):
        """The shape assertion the absolute bound cannot make: ×4 input, ×4 cost, not ×16."""
        small = self._unbroken(64 * 1024)
        large = self._unbroken(256 * 1024)
        ratio = _growth(S.redact_credentials, small, large)
        assert ratio < _NOT_LINEAR, f"cost grew ×{ratio:.1f} for ×4 input — that is not linear"


#: How long the stalled runner below keeps each large run off the CPU.
_STALL = 0.05


class TestTheReadingsPastTheAuthorityStayLinear:
    """A cut-short credential and an scp-style login are read past where a URL's authority ends,
    so each reading is held to linear cost on the input built to make it quadratic: one where
    every candidate reads on and finds nothing."""

    #: The shape, and how many units of it the smaller input holds.
    SHAPES = {
        # Every authority is cut short and the run holds no `@`: each URL would read to its end.
        "cut short": ("a://b:c/", 16_000),
        "cut short, quoted": ('"a://b:c/', 16_000),
        # Every `@` is tried as the one before a host, and none has a host after it.
        "scp, no host": ("@x", 60_000),
        # A login-shaped name at every comma, and one host at the end.
        "scp, every opener": (",a:", 40_000),
    }

    @staticmethod
    def _text(shape: str, units: int) -> str:
        unit, _ = TestTheReadingsPastTheAuthorityStayLinear.SHAPES[shape]
        if shape == "scp, no host":
            return "u:" + unit * units
        if shape == "scp, every opener":
            return unit * units + "@h.example:x"
        return unit * units

    def test_each_shape_reaches_the_reading_it_is_named_for(self):
        """Vacuity floor: a shape that never reached its reading would pass the bound below on a
        tree without that reading at all. Each is masked, or not, exactly as the reading says."""
        tag = A.URL_USERINFO_TAG
        assert S.redact_credentials("a://b:c/" * 3 + "@h")[0] == f"a://{tag}@h"
        # A quoted URL reads only to the next quote, so the first finds no `@` and the second does.
        assert S.redact_credentials('"a://b:c/' * 2 + '@h"')[0] == f'"a://b:c/"a://{tag}@h"'
        assert S.redact_credentials(self._text("scp, no host", 3))[0] == "u:@x@x@x"
        assert S.redact_credentials(self._text("scp, every opener", 3))[0] == f",{tag}@h.example:x"

    @pytest.mark.parametrize("shape", sorted(SHAPES))
    def test_quadrupling_the_input_does_not_multiply_the_cost_by_sixteen(self, shape):
        units = self.SHAPES[shape][1]
        small, large = self._text(shape, units), self._text(shape, 4 * units)
        ratio = _growth(S.redact_credentials, small, large)
        assert (
            ratio < _NOT_LINEAR
        ), f"{shape}: cost grew ×{ratio:.1f} for ×4 input — that is not linear"

    def _scp_inputs(self) -> tuple[str, str]:
        units = self.SHAPES["scp, every opener"][1]
        return self._text("scp, every opener", units), self._text("scp, every opener", 4 * units)

    def test_a_quadratic_redactor_fails_the_bound(self):
        """Vacuity floor for the measure: a redactor whose CPU grows with the square of its input
        (a full count of the text per 1000 characters of it), over the scp inputs, grows ×16 and
        fails. A measure that saw none of the work would pass it."""

        def quadratic(text: str) -> None:
            for _ in range(len(text) // 1000):
                text.count("@")

        small, large = self._scp_inputs()
        ratio = _growth(quadratic, small, large)
        assert ratio >= _NOT_LINEAR, f"a quadratic redactor grew ×{ratio:.1f} and passed"

    def test_a_runner_that_stalls_the_large_runs_does_not_fail_the_bound(self):
        """🔴 Red before (wall time was the measure): the CI failure's shape, a runner that gives
        the three large runs no CPU for a stretch, here 50 ms each off the CPU before the real
        redaction, grew ×40 and more in wall time and failed a linear reading. The time with no
        CPU is not the redaction's, so it is left out."""

        def stalled(text: str):
            if len(text) > len(small):
                time.sleep(_STALL)
            return S.redact_credentials(text)

        small, large = self._scp_inputs()
        ratio = _growth(stalled, small, large)
        assert ratio < _NOT_LINEAR, f"a stalled runner grew ×{ratio:.1f}: the stall was counted"

    def test_a_stretch_that_bills_three_runs_double_does_not_fail_a_linear_reading(self):
        """🔴 Red before (each input's samples back to back): the CI failure's shape (run
        40746112284), a stretch in which three runs in a row, from the first of the large input,
        cost the thread twice their CPU. Here a stand-in redactor spends 10 ns of a fake thread
        clock per character, and 20 in the stretch, so its cost is otherwise exactly linear. With
        the three large samples back to back, all three fell in the stretch and a linear cost read
        ×8.0; in rounds it falls on one round's large sample and the next round's two, and the
        median reads ×4."""
        small, large = self._scp_inputs()
        now, calls, stretch = [0.0], [0], []

        def spend(text: str) -> None:
            calls[0] += 1
            if len(text) > len(small) and not stretch:
                stretch.append(calls[0])
            dear = bool(stretch) and stretch[0] <= calls[0] < stretch[0] + 3
            now[0] += len(text) * (2e-8 if dear else 1e-8)

        ratio = _growth(spend, small, large, clock=lambda: now[0], resolution=1e-9)
        assert stretch, "premise: the large input was measured"
        assert 3.9 < ratio < 4.1, f"a linear cost read ×{ratio:.2f} across a stretch billed double"

    def test_a_clock_coarser_than_a_run_still_reads_a_linear_cost_as_four_times(self):
        """🔴 Red before (one run a sample): on a thread clock that ticks every 15.6 ms, as
        Windows' does, a 1.2 ms run of the small scp input reads no tick, and the ratio came out
        0, a false pass, or a tick over the 1 µs floor, a false red. Here a stand-in redactor
        spends 10 ns of a fake thread clock per character, so its cost is exactly linear, and
        each sample runs until that clock has ticked :data:`_TICKS` times."""
        tick, now = 0.0156, [0.0]

        def spend(text: str) -> None:
            now[0] += len(text) * 1e-8

        def coarse() -> float:
            return now[0] // tick * tick

        small, large = self._scp_inputs()
        ratio = _growth(spend, small, large, clock=coarse, resolution=tick)
        assert 3.9 < ratio < 4.1, f"a linear cost read ×{ratio:.2f} on a {tick * 1000} ms clock"


# ── #2717: the per-match rebuild ────────────────────────────────────────────────────────────────
#
# Pass 1 used to be `finditer` with `result = result.replace(matched, tag, 1)` INSIDE the loop —
# one fresh copy of the whole document per match, O(matches x length). On a 1 MB document holding
# 23,831 credentials that loop was 1.318s of 1.367s total, and `acp/translate.py` calls this 5+
# times per agent message, so a leaked env dump cost seconds on a live turn path.
#
# 🪤 It was NOT a drop-in, which is why #2716 left it alone. `str.replace(matched, tag, 1)`
# rewrites the LEFTMOST occurrence of the matched TEXT, not the occurrence the scan found — so
# replacing the loop with a span splice is a behaviour question before it is an optimisation, and
# it needed its own equivalence argument rather than riding on #2716's.
#
# `_reference_redact_credentials` above spells pass 1 as it stood before this change, so every
# byte-identity test in this file already covers it. These add the shape those tests do not reach
# — the same credential appearing more than once, which is exactly where leftmost and found could
# diverge.

#: Documents where a credential repeats. The reason the old loop was not provably correct.
REPEATED = [
    "AKIAIOSFODNN7EXAMPLE AKIAIOSFODNN7EXAMPLE",
    "key=AKIAIOSFODNN7EXAMPLE and again key=AKIAIOSFODNN7EXAMPLE",
    "AKIAIOSFODNN7EXAMPLE fake-aws-key-id-2 AKIAIOSFODNN7EXAMPLE",
    "first AKIAIOSFODNN7EXAMPLE middle AKIAIOSFODNN7EXAMPLE last AKIAIOSFODNN7EXAMPLE",
    # A repeat whose copies are separated by ANOTHER credential's tag-to-be.
    "sk-ant-api03-" + "z" * 30 + " fake-aws-key-id-4 sk-ant-api03-" + "z" * 30,
    # Adjacent, no separator at all.
    "AKIAQQQQQQQQQQQQQQQQAKIAQQQQQQQQQQQQQQQQ",
    # The same credential inside a URL and again bare.
    "https://u:hunter2@h/x AKIAIOSFODNN7EXAMPLE https://u:hunter2@h/x AKIAIOSFODNN7EXAMPLE",
]


class TestRepeatedCredentialsRedactIdentically:
    """The span splice must agree with the old leftmost-replace on repeated credentials."""

    def test_a_repeated_credential_redacts_exactly_as_the_reference(self, subtests):
        assert len(REPEATED) == 7, "a row was added to or dropped from REPEATED: pin the new count"
        for text in REPEATED:
            with row(subtests, text=text):
                expected = _reference_redact_credentials(text)
                got = S.redact_credentials(text)
                assert got == expected, f"{text!r}: expected {expected!r}, got {got!r}"

    def test_every_copy_is_masked_not_just_the_first(self, subtests):
        """The property behind the equivalence, asserted directly rather than inferred.

        Byte-identity to a reference proves the two agree; it does not prove either is right. A
        second copy left in cleartext is the whole failure this primitive exists to prevent, so
        that is checked on its own terms.
        """
        assert len(REPEATED) == 7, "a row was added to or dropped from REPEATED: pin the new count"
        for text in REPEATED:
            with row(subtests, text=text):
                out, _ = S.redact_credentials(text)
                for cred in ("AKIA", "ASIA", "sk-ant-api03-", "hunter2"):
                    assert cred not in out, f"{cred!r} survived redaction in {text!r}"

    def test_the_fixture_really_does_repeat(self, subtests):
        """Vacuity floor: a corpus of non-repeating strings would pass the class above while
        testing nothing about the leftmost-vs-found question it exists for."""
        assert len(REPEATED) == 7, "a row was added to or dropped from REPEATED: pin the new count"
        for text in REPEATED:
            with row(subtests, text=text):
                spans = [m.group() for m in S._CREDENTIAL_PATTERNS.finditer(text)]
                assert len(spans) > len(set(spans)), f"no credential repeats in {text!r}"


class TestTheSpliceIsEquivalentUnderFuzzing:
    """Generated documents, because the divergence this change had to rule out is constructive.

    The only way leftmost and found can differ is for a replacement to help SYNTHESISE an earlier
    match — the inserted text is the fixed tag, so the filler here deliberately includes
    tag-lookalikes (`[REDACTED: credential]`, `credential]`, `[REDACTED:`) to give that every
    chance. 200,000 cases with a wider pool found zero divergence; 4,000 with a fixed seed is the
    committed regression anchor.
    """

    _CREDS = [
        "AKIA" + "A" * 16,
        "ASIA" + "C" * 16,
        "key=hunter2hunter2hunter2",
        "api_key: abcdef1234567890abcdef",
        "sk-ant-api03-" + "y" * 30,
    ]
    _FILLER = [
        " ",
        "\n",
        " and ",
        "]",
        "[",
        "[REDACTED: credential]",
        "credential]",
        "[REDACTED:",
        "=",
        ": ",
        "x",
        "REDACTED",
        "https://u:p@h/",
        "\t",
    ]

    def test_generated_documents_redact_exactly_as_the_reference(self):
        rnd = random.Random(20260908)
        pool = self._CREDS + self._FILLER
        for _ in range(4000):
            parts: list[str] = []
            for _ in range(rnd.randint(1, 9)):
                piece = rnd.choice(pool)
                # Repeat something already present, often — that is the shape under test.
                if parts and rnd.random() < 0.35:
                    piece = rnd.choice(parts)
                parts.append(piece)
            text = "".join(parts)
            assert S.redact_credentials(text) == _reference_redact_credentials(text), repr(text)

    def test_the_generator_really_produces_repeats_and_matches(self):
        """Vacuity floor on the fuzz: a generator emitting no credentials, or no repeats, would
        make the loop above green while proving nothing."""
        rnd = random.Random(20260908)
        pool = self._CREDS + self._FILLER
        with_match = repeats = 0
        for _ in range(400):
            parts: list[str] = []
            for _ in range(rnd.randint(1, 9)):
                piece = rnd.choice(pool)
                if parts and rnd.random() < 0.35:
                    piece = rnd.choice(parts)
                parts.append(piece)
            text = "".join(parts)
            spans = [m.group() for m in S._CREDENTIAL_PATTERNS.finditer(text)]
            if spans:
                with_match += 1
            if len(spans) > len(set(spans)):
                repeats += 1
        assert with_match > 200, f"only {with_match}/400 generated documents held a credential"
        assert repeats > 20, f"only {repeats}/400 held a REPEATED credential"


class TestManyMatchesIsNotQuadratic:
    """The cost half. Pass 1 was O(matches x length); it is now one pass."""

    @staticmethod
    def _many(matches: int) -> str:
        return " ".join(f"AKIA{i:016d}" for i in range(matches))

    def test_the_fixture_really_holds_the_matches_it_claims(self):
        """Vacuity floor. If the generated tokens stopped matching, the bound below would pass on
        a tree that still rebuilt the document per match — the same trap the single-token floor
        above exists for."""
        text = self._many(2000)
        found = len(S._CREDENTIAL_PATTERNS.findall(text))
        assert found == 2000, f"fixture holds {found} matches, not 2000"

    def test_a_document_full_of_credentials_is_bounded(self):
        """Coarse floor, not a benchmark. ~24k matches in 1 MB cost 1.318s in pass 1 before."""
        text = self._many(24_000)
        started = time.perf_counter()
        out, warnings = S.redact_credentials(text)
        elapsed = time.perf_counter() - started
        assert len(warnings) == 24_000, f"expected one warning per match, got {len(warnings)}"
        assert "AKIA" not in out, "a credential survived"
        assert elapsed < 2.0, f"24k credentials took {elapsed:.2f}s"

    def test_quadrupling_the_match_count_does_not_multiply_the_cost_by_sixteen(self):
        """The shape assertion: ×4 matches, ~×4 cost. This is what the old loop could not do."""
        ratio = _growth(S.redact_credentials, self._many(2000), self._many(8000))
        assert ratio < _NOT_LINEAR, f"cost grew x{ratio:.1f} for x4 matches — that is not linear"
