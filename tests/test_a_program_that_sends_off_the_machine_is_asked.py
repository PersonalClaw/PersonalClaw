"""A program that sends what it is given to a host is asked about, past Trust and YOLO.

A command that reaches a host off the allowed hosts is put to a person whatever grant stands
(``run_bounds.put_to_a_person``), and where a command reaches is what ``command_effects`` reads.
A program it had no reader for was read as one it could not vouch for, with no network facet, so
an upload to a package index, a code host or cloud storage, or bytes sent down a raw socket, was
answered by YOLO and Trust as any unread command is.

Each sending form now reads the host it names, and where the program also takes its destination
from its own configuration, a host it does not name as well. Every other form of those programs
reads as it did, and YOLO answers it as before.

The census row holds a maintained list of the networked tools the reader knows to a reader each,
and each to a sending form it reads as reaching a host.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw import approval_grants, command_effects, command_senders, run_bounds

SESSION = "dashboard:senders"

#: Each sending form, and the host it reads (``""``: only a host it does not name).
SENDS = {
    "twine upload dist/*": "upload.pypi.org",
    "twine upload --repository-url https://packages.example.net/legacy/ dist/*": (
        "packages.example.net"
    ),
    "twine upload -r testpypi dist/*": "test.pypi.org",
    "python3 -m twine upload dist/*": "upload.pypi.org",
    "uv publish": "upload.pypi.org",
    "uv publish --publish-url https://packages.example.net/": "packages.example.net",
    "poetry publish": "upload.pypi.org",
    "flit publish": "upload.pypi.org",
    "hatch publish -r https://packages.example.net/": "packages.example.net",
    "cargo +nightly publish": "crates.io",
    "gem push notes-0.1.gem": "rubygems.org",
    "docker push ghcr.io/someone/notes:1": "ghcr.io",
    "docker image push someone/notes": "docker.io",
    "podman push notes docker://registry.example.net/notes": "registry.example.net",
    "docker compose push": "",
    "gh release upload v1 notes.tar.gz": "github.com",
    "gh release create v1 notes.tar.gz": "github.com",
    "gh gist create --public notes.md": "github.com",
    "gh release upload v1 f -R git.example.net/team/notes": "git.example.net",
    "aws s3 cp notes.txt s3://a-bucket/": "s3.amazonaws.com",
    "aws --profile work s3 sync . s3://a-bucket": "s3.amazonaws.com",
    "aws s3 cp notes.txt s3://b --endpoint-url https://store.example.net": "store.example.net",
    "aws s3api put-object --bucket b --key k --body notes.txt": "s3.amazonaws.com",
    "gsutil -m cp notes.txt gs://a-bucket": "storage.googleapis.com",
    "gcloud storage cp notes.txt gs://a-bucket": "storage.googleapis.com",
    "az storage blob upload --account-name acct -f notes.txt -c c": "acct.blob.core.windows.net",
    "azcopy copy ./notes.txt https://acct.blob.core.windows.net/c/notes.txt": (
        "acct.blob.core.windows.net"
    ),
    "socat - TCP:collector.example.net:80": "collector.example.net",
    "socat -d -d STDIO OPENSSL:collector.example.net:443,verify=0": "collector.example.net",
    "socat TCP-LISTEN:8080 -": "",
    "ncat collector.example.net 80": "collector.example.net",
    "ncat -l 9000": "",
    "nc.traditional -l -p 9000": "",
    "netcat collector.example.net 80": "collector.example.net",
    "nc.openbsd collector.example.net 80": "collector.example.net",
    "cat notes.txt | telnet collector.example.net 80": "collector.example.net",
    "openssl s_client -connect collector.example.net:443": "collector.example.net",
}

#: Forms of the same programs that send nothing, read as they were.
UNCHANGED = [
    "twine check dist/*",
    "poetry build",
    "cargo build",
    "docker ps",
    "gh pr list",
    "gh release view v1",
    "aws s3 ls",
    "aws ec2 describe-instances",
    "gsutil ls",
    "gcloud compute instances list",
    "az vm list",
    "openssl x509 -in cert.pem -noout",
    "socat UNIX-CONNECT:/tmp/notes.sock -",
]

#: The networked tools the reader knows, by a form of each that reaches a host: the ones it read
#: before, and the senders it reads now (``command_senders.SENDERS``). A program added to either
#: is added here.
NETWORKED = {
    "curl": "curl https://example.net/notes",
    "wget": "wget https://example.net/notes",
    "ssh": "ssh host.example.net uptime",
    "scp": "scp notes.txt host.example.net:",
    "sftp": "sftp host.example.net",
    "rsync": "rsync -a . host.example.net:notes/",
    "nc": "nc host.example.net 80",
    "git": "git push https://example.net/notes.git",
    "pip": "pip install requests",
    "pip3": "pip3 install requests",
    "uvx": "uvx ruff",
    "npm": "npm publish",
    "npx": "npx cowsay",
    "pnpm": "pnpm publish",
    "pnpx": "pnpx cowsay",
    "yarn": "yarn publish",
    "bun": "bun publish",
    "bunx": "bunx cowsay",
    "python": "python -m twine upload dist/*",
    "python3": "python3 -m twine upload dist/*",
    "twine": "twine upload dist/*",
    "uv": "uv publish",
    "poetry": "poetry publish",
    "flit": "flit publish",
    "hatch": "hatch publish",
    "cargo": "cargo publish",
    "gem": "gem push notes-0.1.gem",
    "docker": "docker push someone/notes",
    "podman": "podman push someone/notes",
    "gh": "gh gist create notes.md",
    "aws": "aws s3 cp notes.txt s3://a-bucket/",
    "gsutil": "gsutil cp notes.txt gs://a-bucket",
    "gcloud": "gcloud storage cp notes.txt gs://a-bucket",
    "az": "az storage blob upload -f notes.txt -c c",
    "azcopy": "azcopy copy ./notes.txt https://acct.blob.core.windows.net/c/notes.txt",
    "socat": "socat - TCP:host.example.net:80",
    "ncat": "ncat host.example.net 80",
    "netcat": "netcat host.example.net 80",
    "nc.traditional": "nc.traditional host.example.net 80",
    "nc.openbsd": "nc.openbsd host.example.net 80",
    "telnet": "telnet host.example.net 80",
    "openssl": "openssl s_client -connect host.example.net:443",
}


@pytest.fixture(autouse=True)
def _a_home_of_its_own(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)


def _shell_call(command: str) -> SimpleNamespace:
    """A shell call asking to run *command*."""
    return SimpleNamespace(
        title="Bash",
        tool_kind="execute",
        tool_input={"command": command},
        request_id="r1",
        risk_level="",
    )


def _answered(grant: str, command: str, cwd: str) -> bool:
    return approval_grants.stands_for_call(
        grant, session_key=SESSION, event=_shell_call(command), cwd=cwd
    )


@pytest.mark.parametrize("command", sorted(SENDS))
def test_a_send_reads_its_host_and_no_grant_answers_it(command, tmp_path):
    """🔴 Before: read as a command this reading cannot vouch for, with no network facet, so
    Trust and YOLO answered it unasked."""
    for grant in (approval_grants.YOLO, approval_grants.TRUST):
        assert not _answered(grant, command, str(tmp_path)), f"{grant} answered it unasked"
    assert run_bounds.put_to_a_person(_shell_call(command), session_key=SESSION, cwd=str(tmp_path))
    effects = command_effects.command_effects(command)
    assert effects.network and not effects.reads_only, effects
    host = SENDS[command]
    if host:
        assert host in effects.hosts, effects.hosts
    else:
        assert effects.host_unread, effects


@pytest.mark.parametrize("command", UNCHANGED)
def test_the_same_programs_other_forms_are_answered_as_before(command, tmp_path):
    """Ordinary work with those programs is not asked about: only a send reaches a host."""
    effects = command_effects.command_effects(command)
    assert not effects.network, effects
    assert _answered(approval_grants.YOLO, command, str(tmp_path))


@pytest.mark.parametrize(
    "command",
    [
        "socat - FILE:/tmp/notes.txt",
        "socat STDIO EXEC:/bin/sh",
        "socat UNIX-CONNECT:/tmp/notes.sock -",
        "socat ./notes.txt -",
    ],
)
def test_a_socat_between_two_local_addresses_is_still_no_read(command):
    """🔴 It reaches no host, and it may still write a file or run a program: unvouched, as any
    program this reading cannot follow, never a read Trust reads would answer."""
    effects = command_effects.command_effects(command)
    assert not effects.network, effects
    assert effects.unread and not effects.reads_only, effects


def test_every_networked_tool_has_a_reader_that_reads_where_it_reaches():
    """🔴 The census: each networked tool the list names reads a form that reaches a host, and
    every sender the reader knows is on the list, so neither can be dropped unseen."""
    senders = set(command_senders.SENDERS)
    assert senders <= set(NETWORKED), sorted(senders - set(NETWORKED))
    unread = {}
    for program, command in sorted(NETWORKED.items()):
        effects = command_effects.command_effects(command)
        if not effects.network or not (effects.hosts or effects.host_unread):
            unread[program] = command
    assert not unread, f"these networked tools read as reaching no host: {unread}"


def test_a_sender_named_by_a_path_still_reaches_its_host():
    effects = command_effects.command_effects("/usr/local/bin/twine upload dist/*")
    assert effects.network and {"upload.pypi.org"} <= set(effects.hosts)
