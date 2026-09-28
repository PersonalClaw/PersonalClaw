"""URL userinfo is a credential shape every pattern was blind to (#406, #751, #280).

`_CREDENTIAL_PATTERNS` is entirely SHAPE- or NAME-based: it recognises provider
key formats (`fake-anthropic-1…`, `ghp_…`) and `name = value` assignments. A credential
carried POSITIONALLY, in the userinfo slot of a URL, matches neither — so
`https://user:s3cr3t@github.com/a/b.git` survived every surface in this tree that
"redacts": the diagnostics log stream, the SEL audit `resources` field, agent
output, and the ConfirmationRequest preview.

Measured on `origin/main` before any change:

    https://user:s3cr3t@github.com/acme/repo.git   -> unchanged     LEAK
    git clone https://alice:hunter2@git.example…   -> unchanged     LEAK
    ssh://deploy:pa55@host:22/repo                 -> unchanged     LEAK
    postgres://admin:dbpass@db.internal:5432/app   -> unchanged     LEAK
    https://oauth2:fake-github-token-1…@github.com/a/b.git    -> redacted     …by ACCIDENT

**That last row is why this class survived a test suite.** It was caught only
because the password happened to be a GitHub token whose SHAPE one of the
patterns knows. The existing
`test_diagnostics_log_redaction::test_redact_log_text_helper` claimed to cover
"git credentials" and planted exactly that shape, so it passed for the wrong
reason — a check on `s3cr3t` in the same position fails on main. That test now
carries both cases.

#751 asked for the diagnostics log stream to call the redactors. It already does;
its stated repro still leaked purely because of the missing pattern here. Fixing
the pattern once closes both.

The positional rule then stopped at the FIRST `@`. A password may hold one, so
`https://ada:p@ss@host` came out as `https://[REDACTED: url credential]@ss@host`:
the password's tail, in every detail and log line that masked it. And an
scp-style `user:password@host:path`, which has no `://` to anchor on, was not
masked at all. The credential now runs to the last `@` before the host, and an
scp-style login is masked the same way.
"""

from __future__ import annotations

import logging

import pytest

from personalclaw.security import redact_credentials, redact_url_userinfo

_TAG = "[REDACTED: url credential]"

#: Credential-bearing URLs whose secret is NOT a recognisable provider-key shape,
#: so nothing but a positional rule can catch them. `hunter2` and `s3cr3t` are
#: deliberate: a planted string the patterns already match would make every
#: assertion below vacuous, which is how this shipped.
LEAKY = [
    "https://user:s3cr3t@github.com/acme/repo.git",
    "git clone https://alice:hunter2@git.example.com/x.git",
    "ssh://deploy:pa55@host:22/repo",
    "postgres://admin:dbpass@db.internal:5432/app",
    "http://svc:pw@internal.host/path",
]

#: Text that must come through UNTOUCHED. Redacting any of these makes the logs
#: worse, not safer.
CLEAN = [
    "https://github.com/acme/repo.git",
    "mail alice@example.com about it",
    "https://api.example.com/x?to=a@example.com",
    "see docs at https://example.com/a/b#frag",
    "git@github.com:owner/repo.git",
]


class TestTheSecretIsRemoved:
    @pytest.mark.parametrize("text", LEAKY, ids=lambda t: t[:34])
    def test_an_arbitrary_password_in_a_url_is_redacted(self, text):
        out, warnings = redact_credentials(text)
        assert out != text, "the URL credential is still in the text"
        assert warnings, "a redaction happened but was not reported"

    @pytest.mark.parametrize("text", LEAKY, ids=lambda t: t[:34])
    def test_the_secret_itself_is_gone(self, text):
        """The strong form: not just "the text changed" but "the secret is absent"."""
        secret = text.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
        assert secret and secret not in redact_credentials(text)[0]

    def test_a_bare_token_as_userinfo_is_redacted_too(self):
        """`https://<PAT>@github.com/…` is the documented GitHub form, so treating
        userinfo as secret only when it has two colon-separated parts would miss
        the most common real case."""
        text = "https://fake-github-token-2@github.com/a/b.git"
        assert "fake-github-token-2" not in redact_credentials(text)[0]

    def test_the_planted_secrets_are_NOT_ones_the_old_patterns_matched(self):
        """The floor that makes every test above mean something.

        `password=hunter2` passes through `redact_credentials` untouched — the
        patterns are shape-based. So if these secrets were recognisable on their
        own, the suite would pass with the positional rule deleted. Each is
        checked in isolation, out of URL position, and must survive.
        """
        for text in LEAKY:
            secret = text.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
            assert (
                redact_credentials(secret)[0] == secret
            ), f"{secret!r} is matched on its own, so its URL test is vacuous"


class TestTheHostSurvives:
    """Removing the secret must not remove the ability to read the log.

    A whole-match replacement would have turned a diagnosable "clone of
    github.com/acme/repo failed" into `[REDACTED: credential]`, which is why this
    is a dedicated pre-pass rather than another alternative in
    `_CREDENTIAL_PATTERNS`.
    """

    def test_scheme_host_and_path_are_kept(self):
        out, _ = redact_url_userinfo("https://user:s3cr3t@github.com/acme/repo.git")
        assert out == "https://[REDACTED: url credential]@github.com/acme/repo.git"

    def test_the_port_is_kept(self):
        out, _ = redact_url_userinfo("ssh://deploy:pa55@host:22/repo")
        assert out.endswith("@host:22/repo")

    def test_the_warning_names_the_scheme(self):
        _, warnings = redact_url_userinfo("postgres://admin:dbpass@db/app")
        assert any("postgres" in w for w in warnings)


class TestNothingElseIsTouched:
    @pytest.mark.parametrize("text", CLEAN, ids=lambda t: t[:34])
    def test_text_with_no_url_credential_is_unchanged(self, text):
        """Vacuity floor in the other direction. A rule that redacted every `@`
        would satisfy every test above and wreck the logs — a bare email is not a
        credential, and an `@` in a query string or an scp-like remote is not a
        userinfo delimiter."""
        assert redact_url_userinfo(text)[0] == text


class TestIdempotence:
    """`redact_credentials` is idempotent because none of its passes can match a mask:
    applied twice to a composed `api_key: [REDACTED: …]` line it keeps the line. A new
    pass must not add a way for a second application to corrupt text."""

    def test_the_pre_pass_is_idempotent(self):
        once, _ = redact_url_userinfo("https://user:s3cr3t@github.com/a/b.git")
        assert redact_url_userinfo(once)[0] == once

    def test_it_is_idempotent_by_CONSTRUCTION_not_by_a_guard(self):
        """The tag holds a space straight after its `:`, and whitespace ends an
        authority, so what a second pass reads after `://` is `[REDACTED:` with
        nothing after it: a second match is impossible rather than merely prevented.
        Pinned so nobody "simplifies" the tag into something matchable."""
        from personalclaw.security import _URL_AUTHORITY_RE, _URL_USERINFO_TAG

        assert _URL_USERINFO_TAG.startswith("[REDACTED: ")
        m = _URL_AUTHORITY_RE.search(f"https://{_URL_USERINFO_TAG}@host/x")
        assert m is not None and m.group("authority") == "[REDACTED:"
        assert redact_url_userinfo(f"https://{_URL_USERINFO_TAG}@host/x")[1] == []

    def test_a_composed_line_holding_a_redacted_url_survives(self):
        """The documented hazard's shape, applied to this pass's output: a caller
        that screens a value then builds `key: value` then screens again."""
        url, _ = redact_url_userinfo("https://user:s3cr3t@github.com/a/b.git")
        composed = f"repo_url: {url}"
        assert redact_credentials(composed)[0] == composed


class TestTheAuditLogIsScreened:
    """The SEL audit `resources` field is the one surface that cannot be repaired
    afterwards: the log is HMAC-chained and append-only, so rewriting a row breaks
    the chain. It did not call the redactors at all."""

    def test_sel_log_redacts_resources_and_error(self):
        import inspect

        from personalclaw.dashboard.handlers import apps as A

        src = inspect.getsource(A._sel_log)
        assert "redact_credentials(resources)" in src
        assert "redact_credentials(error)" in src
        assert "resources=safe_resources" in src


# ── the source that gets persisted in the first place (#406 + #280) ──────────


class TestGitSourceValidation:
    """The refusal that stops a credential URL being stored at all. Belt to
    `_sel_log`'s braces: the refusal stops new ones, the screening covers any that
    arrive by another route."""

    ALLOWED = [
        "https://github.com/acme/cool-app.git",
        "https://github.com/acme/cool-app",
        "file:///tmp/repo/apps.git",
        "git@github.com:owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git://github.com/owner/repo.git",
    ]
    REFUSED = [
        "https://user:s3cr3t@github.com/a/b.git",
        "ssh://deploy:pa55@host/repo.git",
        "https://fake-github-token-3@github.com/a/b",
        "not-a-git-url",
        "",
        "javascript:alert(1)",
        "https://",
    ]

    @pytest.mark.parametrize("url", ALLOWED, ids=lambda u: u[:32])
    def test_a_real_remote_form_is_accepted(self, url):
        """Vacuity floor, and the one that matters most here. `file://` is used by
        the catalog's own test fixtures and `git@host:path` is the commonest ssh
        remote — a rule that only understood `https://` would break both, and
        `ssh://git@host` shows why the refusal has to tell a USERNAME from a
        SECRET."""
        from personalclaw.apps.catalog import _validate_git_source

        assert _validate_git_source(url) == url.strip()

    @pytest.mark.parametrize("url", REFUSED, ids=lambda u: (u or "(empty)")[:32])
    def test_a_credential_or_a_non_remote_is_refused(self, url):
        from personalclaw.apps.catalog import _validate_git_source

        with pytest.raises(ValueError):
            _validate_git_source(url)

    def test_the_refusal_says_why(self):
        """A 400 a user cannot act on is a 400 they retype. The password case names
        the audit log, because that is the reason it cannot simply be accepted and
        redacted later."""
        from personalclaw.apps.catalog import _validate_git_source

        with pytest.raises(ValueError, match="append-only"):
            _validate_git_source("https://user:s3cr3t@github.com/a/b.git")
        with pytest.raises(ValueError, match="git@github.com"):
            _validate_git_source("not-a-git-url")


# ── a password holding `@`, `:` or `/`, and an scp-style login ───────────────


#: ``(text, what it masks to)``. Each password holds an `@`, a `:` or a `/`, raw. An
#: `@` in one used to leave the rest of it behind, and a `/` all of it.
SPECIAL = [
    (
        "https://ada:p@ss@git.example.com/acme/repo.git",
        f"https://{_TAG}@git.example.com/acme/repo.git",
    ),
    (
        "https://ada:pa:ss@git.example.com/acme/repo.git",
        f"https://{_TAG}@git.example.com/acme/repo.git",
    ),
    (
        "https://ada:pa/ss@git.example.com/acme/repo.git",
        f"https://{_TAG}@git.example.com/acme/repo.git",
    ),
    (
        "https://ada:p@ss/w0rd@git.example.com/acme/repo.git",
        f"https://{_TAG}@git.example.com/acme/repo.git",
    ),
    (
        "https://ada:p@s:s/w@git.example.com/acme/repo.git",
        f"https://{_TAG}@git.example.com/acme/repo.git",
    ),
    ("ssh://deploy:p@ss@build.example.com:22/repo", f"ssh://{_TAG}@build.example.com:22/repo"),
    (
        "fatal: unable to access 'https://ada:pa/ss@git.example.com/r.git/': URL rejected",
        f"fatal: unable to access 'https://{_TAG}@git.example.com/r.git/': URL rejected",
    ),
    (
        '{"remote":"https://ada:p@ss/w@git.example.com","by":"ops@example.com"}',
        '{"remote":"https://' + _TAG + '@git.example.com","by":"ops@example.com"}',
    ),
]

#: ``(text, what it masks to)``: an scp-style address, whose login has no `://` before it.
SCP = [
    ("deploy:hunter2@git.example.com:acme/repo.git", f"{_TAG}@git.example.com:acme/repo.git"),
    (
        "git clone deploy:p@ss/w0rd@git.example.com:acme/repo.git",
        f"git clone {_TAG}@git.example.com:acme/repo.git",
    ),
    (
        "rsync -av ada:pa:ss@backup.example.com::data/srv",
        f"rsync -av {_TAG}@backup.example.com::data/srv",
    ),
    (
        "HTTPS_PROXY=ada:hunter2@proxy.example.com:3128",
        f"HTTPS_PROXY={_TAG}@proxy.example.com:3128",
    ),
    ("scp ada:s3cr3t@[2001:db8::1]:/srv/x .", f"scp {_TAG}@[2001:db8::1]:/srv/x ."),
]

#: Holding an `@` and a `:` does not make a text a login. Each comes through as it was.
NOT_A_LOGIN = [
    "git@github.com:owner/repo.git",
    "https://registry.example.com/@scope/pkg",
    "https://registry.example.com:8443/@scope/pkg",
    "pip install git+https://github.com/org/repo.git@v1.2#egg=x",
    '{"url":"https://api.example.com","mail":"ops@example.com"}',
    "pulled nginx:1.25@sha256:0123456789abcdef0123456789abcdef",
    "the css2?family=Inter:wght@100..900: a variable font",
    "https://matrix.example/#/@alice:matrix.example",
]


class TestAPasswordMayHoldAnAtAColonOrASlash:
    @pytest.mark.parametrize(("text", "masked"), SPECIAL, ids=lambda t: t[:40])
    def test_the_whole_password_is_masked_and_the_host_kept(self, text, masked):
        assert redact_credentials(text)[0] == masked

    def test_a_real_host_keeps_the_at_sign_its_path_holds(self):
        """The credential ends at the host, so an `@` after it is the path's: a pip ref here."""
        text = "pip install git+https://tok@github.com/org/repo.git@v1.2#egg=x"
        masked = f"pip install git+https://{_TAG}@github.com/org/repo.git@v1.2#egg=x"
        assert redact_credentials(text)[0] == masked


class TestAnScpStyleLoginIsMasked:
    @pytest.mark.parametrize(("text", "masked"), SCP, ids=lambda t: t[:40])
    def test_the_login_is_masked_and_the_host_and_path_kept(self, text, masked):
        assert redact_credentials(text)[0] == masked

    @pytest.mark.parametrize("text", NOT_A_LOGIN, ids=lambda t: t[:40])
    def test_text_that_only_looks_like_one_is_unchanged(self, text):
        assert redact_credentials(text) == (text, [])

    def test_a_second_pass_changes_nothing(self):
        once = redact_credentials(SCP[1][0])[0]
        assert redact_credentials(once) == (once, [])


class TestEveryMaskerGetsTheRule:
    """The masks built on `redact_credentials` carry the rule to where the text goes: a detail
    that is shown, sent or stored, and every log line."""

    TEXT = (
        "clone of https://ada:p@ss/w0rd@git.example.com/acme/repo.git failed; "
        "retry with deploy:hunter2@build.example.com:acme/repo.git"
    )
    #: What of the two passwords must not be left: each, and the tail the old rule kept.
    LEFT = ("p@ss/w0rd", "ss/w0rd", "hunter2")
    KEPT = ("@git.example.com/acme/repo.git", "@build.example.com:acme/repo.git")

    def _holds_no_password(self, out: str) -> None:
        for secret in self.LEFT:
            assert secret not in out, (secret, out)
        for kept in self.KEPT:
            assert kept in out, (kept, out)

    def test_redact_credentials(self):
        self._holds_no_password(redact_credentials(self.TEXT)[0])

    def test_redact_or_withhold(self):
        from personalclaw.security import redact_or_withhold

        self._holds_no_password(redact_or_withhold(self.TEXT))

    def test_the_display_and_model_masks(self):
        from personalclaw.security import redact_for_display, redact_for_model

        self._holds_no_password(redact_for_display(self.TEXT))
        self._holds_no_password(redact_for_model(self.TEXT))

    def test_a_log_record_the_masking_formatter_writes(self):
        from personalclaw.security import MaskingFormatter

        record = logging.LogRecord(
            "personalclaw.example", logging.WARNING, __file__, 1, "git said: %s", (self.TEXT,), None
        )
        line = MaskingFormatter("%(levelname)s %(message)s").format(record)
        assert line.startswith("WARNING git said: ")
        self._holds_no_password(line)

    def test_what_a_child_printed(self):
        from personalclaw.security import mask_child_output

        self._holds_no_password(mask_child_output(self.TEXT, limit=None))

    @pytest.mark.parametrize(
        "proxy",
        ["http://ada:p@ss@proxy.example.com:3128", "http://ada:p@ss/w0rd@proxy.example.com:3128"],
    )
    def test_a_proxy_address_keeps_working_without_any_of_its_login(self, proxy):
        """Taken out whole, not only up to its first `@`: what was left of it was sent to the
        proxy as the user name."""
        from personalclaw.security import strip_url_userinfo

        assert strip_url_userinfo(proxy) == "http://proxy.example.com:3128"
