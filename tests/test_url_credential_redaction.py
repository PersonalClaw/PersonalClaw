"""URL userinfo is a credential shape every pattern was blind to (#406, #751, #280).

`_CREDENTIAL_PATTERNS` is entirely SHAPE- or NAME-based: it recognises provider
key formats (`sk-ant-…`, `ghp_…`) and `name = value` assignments. A credential
carried POSITIONALLY, in the userinfo slot of a URL, matches neither — so
`https://user:s3cr3t@github.com/a/b.git` survived every surface in this tree that
"redacts": the diagnostics log stream, the SEL audit `resources` field, agent
output, and the ConfirmationRequest preview.

Measured on `origin/main` before any change:

    https://user:s3cr3t@github.com/acme/repo.git   -> unchanged     LEAK
    git clone https://alice:hunter2@git.example…   -> unchanged     LEAK
    ssh://deploy:pa55@host:22/repo                 -> unchanged     LEAK
    postgres://admin:dbpass@db.internal:5432/app   -> unchanged     LEAK
    https://oauth2:ghp_AAAA…@github.com/a/b.git    -> redacted     …by ACCIDENT

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
"""

from __future__ import annotations

import pytest

from personalclaw.security import redact_credentials, redact_url_userinfo

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
    "https://api.example.com/x?to=a@b.com",
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
        text = "https://ghp_AAAAAAAAAAAAAAAAAAAAAAAA@github.com/a/b.git"
        assert "ghp_AAAAAAAAAAAAAAAAAAAAAAAA" not in redact_credentials(text)[0]

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
    """`redact_credentials` is NOT idempotent in general: applied twice to a
    composed `api_key: [REDACTED: …]` line it garbles the text AND loses the field
    name. A new pass must not add another way for a second application to corrupt
    text."""

    def test_the_pre_pass_is_idempotent(self):
        once, _ = redact_url_userinfo("https://user:s3cr3t@github.com/a/b.git")
        assert redact_url_userinfo(once)[0] == once

    def test_it_is_idempotent_by_CONSTRUCTION_not_by_a_guard(self):
        """The tag contains a space and the userinfo character class excludes
        whitespace, so a second match is impossible rather than merely prevented.
        Pinned so nobody "simplifies" the tag into something matchable."""
        from personalclaw.security import _URL_USERINFO_RE, _URL_USERINFO_TAG

        assert " " in _URL_USERINFO_TAG
        assert not _URL_USERINFO_RE.search(f"https://{_URL_USERINFO_TAG}@host/x")

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
        "https://ghp_AAAAAAAAAAAAAAAAAAAA@github.com/a/b",
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
