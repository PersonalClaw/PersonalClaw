"""Programs that send what they are given to a host, and where each one's sending forms reach.

Each reads the forms that upload or send: a package to an index, an image to a registry, a
release asset or a gist to a code host, a file to cloud storage, bytes down a raw socket. Every
other form of these programs is read as it was, unvouched. Where a program also reads where it
sends from its own configuration or environment (a ``.pypirc``, a profile's endpoint,
``GH_HOST``), the host its command line names is shown, and the reach is also a host it does not
name, so no grant answers it (``run_bounds.put_to_a_person``).

:mod:`personalclaw.command_effects` asks :data:`SENDERS` before its own tables, so a sender's
reader answers for its program, and its every other form is read as the program was before.
"""

from __future__ import annotations

from personalclaw.command_effects import (
    _HOST_NAME_TEXT,
    _NETWORK,
    _READ,
    _SCHEME,
    _UNREAD,
    CommandEffects,
    _clean_host,
    _host_part,
    _host_reach,
    _longs,
    _nc,
    _python,
    _reaches,
    _Read,
    _remote_reach,
    _url_reach,
    _uv,
)
from personalclaw.command_operands import _scan, _value_words
from personalclaw.shell_syntax import Word

#: Where a package index takes an upload when the command line names no other.
PYPI_UPLOAD_HOST = "upload.pypi.org"
#: The indexes an uploader knows by name, and where each takes an upload.
_INDEX_NAMES = {"pypi": PYPI_UPLOAD_HOST, "testpypi": "test.pypi.org"}


def _sent(*hosts: str) -> CommandEffects:
    """A send to each of *hosts*, and to wherever the program's own configuration says."""
    return _UNREAD | _NETWORK | _reaches(*hosts)


def _sub(args: list[Word], *, valued: str = "", long_valued: str = "") -> tuple[str, list[Word]]:
    """The subcommand *args* begin with, past their leading options, and the words after it."""
    scan = _scan(
        args,
        valued=valued,
        long_valued=_longs(long_valued),
        unknown_short_is_flag=True,
        stop_at_operand=True,
    )
    if not scan.operands or scan.operands[0].opaque:
        return "", []
    first = scan.operands[0]
    return first.text, args[args.index(first) + 1 :]


def _index_upload(
    args: list[Word], *, url: tuple[str, ...] = (), named: tuple[str, ...] = (), valued: str = ""
) -> CommandEffects:
    """An upload to a package index: the options in *url* give its URL, those in *named* its name
    (``pypi``) or URL, and with neither it is the default index."""
    scan = _scan(
        args,
        valued=valued,
        long_valued=frozenset(o for o in (*url, *named) if o.startswith("--")),
        unknown_short_is_flag=True,
    )
    effects = _sent()
    urls = _value_words(scan, url)
    names = _value_words(scan, named)
    for word in urls:
        effects = effects | _url_reach(word)
    for word in names:
        if word.opaque or _SCHEME.search(word.text):
            effects = effects | _url_reach(word)
        elif word.text in _INDEX_NAMES:
            effects = effects | _reaches(_INDEX_NAMES[word.text])
    return effects if urls or names else effects | _reaches(PYPI_UPLOAD_HOST)


def _twine(args: list[Word]) -> CommandEffects:
    sub, rest = _sub(args)
    if sub not in ("upload", "register"):
        return _UNREAD
    return _index_upload(
        rest, url=("--repository-url",), named=("-r", "--repository"), valued="rucpi"
    )


def _uv_sending(args: list[Word]) -> CommandEffects:
    """``uv publish`` uploads its distributions; every other form is :func:`_uv`'s."""
    sub, rest = _sub(args, long_valued="directory project cache-dir config-file color")
    if sub != "publish":
        return _uv(args)
    return _index_upload(rest, url=("--publish-url",), named=("--index",), valued="utp")


def _poetry(args: list[Word]) -> CommandEffects:
    sub, rest = _sub(args, valued="C", long_valued="directory project")
    if sub != "publish":
        return _UNREAD
    return _index_upload(rest, named=("-r", "--repository"), valued="rup")


def _flit(args: list[Word]) -> CommandEffects:
    sub, rest = _sub(args)
    if sub != "publish":
        return _UNREAD
    return _index_upload(rest, named=("--repository",))


def _hatch(args: list[Word]) -> CommandEffects:
    sub, rest = _sub(args, valued="e", long_valued="env config cache-dir data-dir")
    if sub != "publish":
        return _UNREAD
    return _index_upload(rest, named=("-r", "--repo"), valued="ruac")


def _cargo(args: list[Word]) -> CommandEffects:
    while args and args[0].text.startswith("+"):  # `cargo +nightly publish`: the toolchain
        args = args[1:]
    sub, rest = _sub(args, valued="ZC", long_valued="color config")
    if sub != "publish":
        return _UNREAD
    scan = _scan(rest, valued="pj", long_valued=_longs("index registry token"))
    effects = _sent()
    indexes = _value_words(scan, ("--index",))
    for word in indexes:
        effects = effects | _url_reach(word)
    if indexes or "--registry" in scan.values:
        return effects
    return effects | _reaches("crates.io")


def _gem(args: list[Word]) -> CommandEffects:
    sub, rest = _sub(args)
    if sub != "push":
        return _UNREAD
    scan = _scan(rest, valued="k", long_valued=_longs("host key otp"))
    hosts = _value_words(scan, ("--host",))
    effects = _sent()
    for word in hosts:
        effects = effects | _url_reach(word, bare=True)
    return effects if hosts else effects | _reaches("rubygems.org")


def _image_registry(word: Word) -> CommandEffects:
    """The registry an image names (``host[:port]/name:tag``, or a ``docker://`` one), Docker Hub
    for one that names none."""
    if word.opaque:
        return _NETWORK
    if _SCHEME.search(word.text):
        return _url_reach(word)
    head, slash, _rest = word.text.partition("/")
    if slash and ("." in head or ":" in head or head == "localhost"):
        host = head.rsplit(":", 1)[0] if head.count(":") == 1 else head
        return _host_reach(host, word, len(head))
    return _reaches("docker.io")


def _image_push(args: list[Word]) -> CommandEffects:
    """``docker push`` and ``podman push`` (and ``image push``): the registry each image names;
    ``compose push``, the images its file names."""
    sub, rest = _sub(
        args, valued="cHl", long_valued="config context host log-level tlscacert tlscert tlskey"
    )
    if sub in ("image", "compose", "manifest"):
        group, (sub, rest) = sub, _sub(rest)
        if group == "compose" and sub == "push":
            return _sent()
    if sub != "push":
        return _UNREAD
    scan = _scan(rest, long_valued=_longs("platform creds authfile cert-dir format"))
    effects = _sent()
    # `podman push IMAGE DESTINATION` sends to its destination; one image goes where it names.
    for word in scan.operands[1:2] or scan.operands[:1]:
        effects = effects | _image_registry(word)
    return effects


#: gh's forms that upload: a release and its assets, a gist.
_GH_SENDS = {
    "release": frozenset({"create", "upload", "edit"}),
    "gist": frozenset({"create", "edit"}),
}


def _gh(args: list[Word]) -> CommandEffects:
    group, rest = _sub(args)
    sends = _GH_SENDS.get(group)
    if sends is None:
        return _UNREAD
    action, rest = _sub(rest, valued="R", long_valued="repo")
    if action not in sends:
        return _UNREAD
    scan = _scan(rest, valued="R", long_valued=_longs("repo"), unknown_short_is_flag=True)
    effects = _sent("github.com")
    for word in _value_words(scan, ("-R", "--repo")):
        parts = word.text.split("/")
        if not word.opaque and len(parts) == 3:  # `HOST/OWNER/REPO`
            effects = effects | _host_reach(parts[0], word, len(parts[0]))
    return effects


def _aws(args: list[Word]) -> CommandEffects:
    scan = _scan(
        args,
        long_valued=_longs("endpoint-url profile region output query cli-read-timeout"),
        unknown_short_is_flag=True,
    )
    words = [w.text for w in scan.operands]
    copies = any(
        (a, b) in {("s3", "cp"), ("s3", "sync"), ("s3", "mv"), ("s3api", "put-object")}
        for a, b in zip(words, words[1:])
    )
    if not copies:
        return _UNREAD
    effects = _sent()
    endpoints = _value_words(scan, ("--endpoint-url",))
    for word in endpoints:
        effects = effects | _url_reach(word)
    return effects if endpoints else effects | _reaches("s3.amazonaws.com")


def _gsutil(args: list[Word]) -> CommandEffects:
    sub, _rest = _sub(args, valued="ohu")
    return _sent("storage.googleapis.com") if sub in ("cp", "mv", "rsync") else _UNREAD


def _gcloud(args: list[Word]) -> CommandEffects:
    group, rest = _sub(args, long_valued="project account configuration")
    action, _rest = _sub(rest)
    if group != "storage" or action not in ("cp", "mv", "rsync"):
        return _UNREAD
    return _sent("storage.googleapis.com")


#: ``az storage``'s services, and the forms that send to one.
_AZ_SERVICES = frozenset({"blob", "file", "fs"})
_AZ_SENDS = frozenset({"upload", "upload-batch", "sync", "copy"})


def _az(args: list[Word]) -> CommandEffects:
    scan = _scan(
        args,
        long_valued=_longs("account-name container-name name file source destination output"),
        unknown_short_is_flag=True,
    )
    words = [w.text for w in scan.operands[:3]]
    if words[:1] != ["storage"] or not _AZ_SENDS & set(words[1:]):
        return _UNREAD
    effects = _sent()
    service = words[1] if len(words) > 1 and words[1] in _AZ_SERVICES else ""
    for word in _value_words(scan, ("--account-name",)):
        account = word.text.lower()
        if service and not word.opaque and _HOST_NAME_TEXT.fullmatch(account):
            kind = "dfs" if service == "fs" else service
            effects = effects | _reaches(f"{account}.{kind}.core.windows.net")
    return effects


def _azcopy(args: list[Word]) -> CommandEffects:
    sub, rest = _sub(args)
    if sub not in ("copy", "cp", "sync"):
        return _UNREAD
    effects = _UNREAD
    for word in _scan(rest, unknown_short_is_flag=True).operands:
        if word.opaque or _SCHEME.search(word.text):
            effects = effects | _url_reach(word)
    return effects


# socat's addresses, by their type: those that connect to the host they name, those that connect
# through a proxy or SOCKS server first, and those on this machine. Any other type listens, or is
# one this reading does not know: either reaches a host it does not name.
_SOCAT_CONNECTS = frozenset(
    "tcp tcp4 tcp6 tcp-connect tcp4-connect tcp6-connect udp udp4 udp6 udp-connect udp4-connect "
    "udp6-connect udp-sendto udp4-sendto udp6-sendto udp-datagram udp4-datagram udp6-datagram "
    "sctp sctp4 sctp6 sctp-connect sctp4-connect sctp6-connect dccp dccp4 dccp6 dccp-connect "
    "openssl openssl-connect ssl ip-sendto ip4-sendto ip6-sendto ip-datagram ip4-datagram "
    "ip6-datagram".split()
)
_SOCAT_VIA = frozenset("socks4 socks4a socks5 socks5-connect proxy proxy-connect".split())
_SOCAT_HERE = frozenset(
    "stdio stdin stdout stderr file open gopen create pipe pty fd exec system shell readline "
    "unix unix-connect unix-client unix-sendto unix-listen unix-recv unix-recvfrom abstract "
    "abstract-connect abstract-client abstract-sendto abstract-listen abstract-recv".split()
)


def _socat_address(word: Word) -> CommandEffects:
    if word.opaque:
        return _NETWORK
    text = word.text.split(",", 1)[0]
    if text == "-" or (":" not in text and "/" in text):
        return _READ  # standard input and output, or a file
    kind, _colon, rest = text.partition(":")
    kind = kind.lower()
    if kind in _SOCAT_HERE:
        return _READ
    if 0 <= word.glob_at:
        return _NETWORK
    if kind in _SOCAT_CONNECTS:
        host = _clean_host(_host_part(rest, needs_colon=False) or "")
        return _reaches(host) if host else _NETWORK
    if kind in _SOCAT_VIA:
        hosts = [_clean_host(part) for part in rest.split(":")[:2]]
        named = [host for host in hosts if host]
        return _reaches(*named) if len(named) == 2 else _NETWORK
    return _NETWORK


def _socat(args: list[Word]) -> CommandEffects:
    kept: list[Word] = []
    skip = False
    for word in args:  # `-lf FILE` and `-lp NAME` take the next word; every other value is attached
        if skip:
            skip = False
        elif word.text in ("-lf", "-lp"):
            skip = True
        else:
            kept.append(word)
    operands = _scan(kept, unknown_short_is_flag=True).operands
    effects = _UNREAD
    for word in operands[:2]:
        effects = effects | _socat_address(word)
    return effects if len(operands) == 2 else effects | _NETWORK


def _nc_like(valued: str, long_valued: str = "") -> _Read:
    """A netcat: where its first operand, or its ``--proxy``, says it connects; anywhere when it
    listens or names no host."""

    def read(args: list[Word]) -> CommandEffects:
        scan = _scan(
            args, valued=valued, long_valued=_longs(long_valued), unknown_short_is_flag=True
        )
        effects = _UNREAD
        for word in _value_words(scan, ("--proxy",)):
            effects = effects | _remote_reach(word, needs_colon=False)
        if scan.seen & {"-U", "--unixsock"}:
            return effects  # a socket on this machine
        if scan.seen & {"-l", "--listen"} or not scan.operands:
            return effects | _NETWORK
        return effects | _remote_reach(scan.operands[0], needs_colon=False)

    return read


def _telnet(args: list[Word]) -> CommandEffects:
    scan = _scan(args, valued="beklnSX", unknown_short_is_flag=True)
    if not scan.operands:
        return _UNREAD | _NETWORK  # it opens a connection when it is told to, to any host
    return _UNREAD | _remote_reach(scan.operands[0], needs_colon=False)


def _openssl(args: list[Word]) -> CommandEffects:
    """``openssl s_client``: the host ``-connect`` (or ``-host``, ``-proxy`` or its operand)
    names; every other form of openssl is read as it was."""
    sub, rest = _sub(args)
    if sub != "s_client":
        return _UNREAD
    effects = _UNREAD
    found = False
    for k, word in enumerate(rest[:-1]):
        if word.text in ("-connect", "-host", "-proxy"):
            effects = effects | _remote_reach(rest[k + 1], needs_colon=False)
            found = True
    last = rest[-1] if rest else None
    if not found and last is not None and not last.text.startswith("-"):
        return effects | _remote_reach(last, needs_colon=False)
    return effects if found else effects | _NETWORK


def _python_sending(read: _Read) -> _Read:
    """A Python interpreter, where ``-m twine`` is twine."""

    def python(args: list[Word]) -> CommandEffects:
        if len(args) >= 2 and args[0].text == "-m" and args[1].text == "twine":
            return _UNREAD | _twine(args[2:])
        return read(args)

    return python


#: The programs read as sending what they are given to a host, by the reader that says where.
#: ``tests/test_a_program_that_sends_off_the_machine_is_asked.py`` holds each to a form it reads.
SENDERS: dict[str, _Read] = {
    "twine": _twine,
    "uv": _uv_sending,
    "poetry": _poetry,
    "flit": _flit,
    "hatch": _hatch,
    "cargo": _cargo,
    "gem": _gem,
    "docker": _image_push,
    "podman": _image_push,
    "gh": _gh,
    "aws": _aws,
    "gsutil": _gsutil,
    "gcloud": _gcloud,
    "az": _az,
    "azcopy": _azcopy,
    "socat": _socat,
    "ncat": _nc_like(
        "cegGimopsxw",
        "proxy proxy-type proxy-auth sh-exec exec lua-exec allow allowfile deny denyfile "
        "source-port source idle-timeout wait output hex-dump max-conns ssl-cert ssl-key "
        "ssl-trustfile ssl-ciphers ssl-servername ssl-alpn",
    ),
    "netcat": _nc_like("cegGiopqsw"),
    "nc.traditional": _nc_like("cegGiopqsw"),
    "nc.openbsd": _nc,
    "telnet": _telnet,
    "openssl": _openssl,
    "python": _python_sending(_python),
    "python3": _python_sending(_python),
}
