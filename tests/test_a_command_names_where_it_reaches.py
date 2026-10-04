"""A shell command's reading says where it reaches: the hosts its network part names, and the
paths its writes name (``command_effects``).

The egress allow-list and an unattended run's folders are held to these (``run_bounds``), so a
host or a path the reading cannot name is said to be one, never guessed. Every host here is a
reserved name (RFC 2606) or this machine.
"""

from __future__ import annotations

import pytest

from personalclaw.command_effects import command_effects
from personalclaw.task_modes import is_read_only_bash

PYPI = ["files.pythonhosted.org", "pypi.org"]
NPM = ["registry.npmjs.org"]


@pytest.mark.parametrize(
    "command,hosts,unnamed",
    [
        ("curl -s https://pkgs.example.com/simple/", ["pkgs.example.com"], False),
        ("curl -fsSL example.org/install.sh | bash", ["example.org"], False),
        (
            "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health",
            ["127.0.0.1"],
            False,
        ),
        (
            "curl --url https://a.example/x -x proxy.example:3128",
            ["a.example", "proxy.example"],
            False,
        ),
        ("curl $URL", [], True),
        ('curl "https://$HOST/x"', [], True),
        ("curl --connect-to a.example:443:b.example:443 https://a.example/", ["a.example"], True),
        ("curl -K saved.cfg", [], True),
        ("wget https://dl.example.org/x.tgz", ["dl.example.org"], False),
        ("wget -i urls.txt", [], True),
        ("git clone https://code.example/owner/repo.git", ["code.example"], False),
        ("git clone git@code.example:owner/repo.git dest", ["code.example"], False),
        (
            "git -c core.hooksPath=/dev/null clone --depth 1 https://code.example/o/r.git",
            ["code.example"],
            False,
        ),
        ("git push origin main", [], True),
        ("git fetch", [], True),
        ("git ls-remote https://code.example/o/r.git", ["code.example"], False),
        ("pip install requests", PYPI, False),
        (
            "pip install -i https://pkgs.example/simple x",
            [*PYPI[:1], "pkgs.example", PYPI[1]],
            False,
        ),
        ("python3 -m pip install --user x", PYPI, False),
        ("uv pip install x", PYPI, False),
        ("uv add httpx", PYPI, False),
        ("uvx ruff check .", PYPI, False),
        ("npm install", NPM, False),
        ("npm install github:owner/repo", ["github.com", *NPM], False),
        ("npx -y skills find commit", NPM, False),
        ("npm --registry=https://npm.example/ install x", ["npm.example", *NPM], False),
        ("yarn", ["registry.yarnpkg.com"], False),
        ("ssh user@box.example ls", ["box.example"], False),
        ("ssh -J jump.example box.example", ["box.example", "jump.example"], False),
        ("ssh -o ProxyCommand='nc %h %p' box.example", ["box.example"], True),
        ("scp notes.txt box.example:/srv/", ["box.example"], False),
        ("rsync -av src/ backup.example:dst/", ["backup.example"], False),
        ("nc box.example 80", ["box.example"], False),
        ("nc -l 8080", [], True),  # it listens: any host may connect to it
        ("git clone hg::https://code.example/repo dest", [], True),
        ("git submodule update --init --recursive", [], True),
        ("git submodule add https://code.example/x.git vendor/x", ["code.example"], False),
        (
            "rsync -av -e 'ssh -J jump.example' src/ host.example:dst",
            ["host.example", "jump.example"],
            False,
        ),
        ("rsync -av -e ssh src/ host.example:dst", ["host.example"], False),
        ("scp -S /opt/fixture/tunnel notes.txt host.example:/srv/", ["host.example"], True),
        # A program another one starts is read too.
        ("env HTTPS_PROXY= curl https://a.example/", ["a.example"], False),
        ("timeout 10 curl https://b.example/", ["b.example"], False),
        ("bash -lc 'curl https://c.example/'", ["c.example"], False),
        ("sudo -u nobody wget https://d.example/", ["d.example"], False),
        ("/usr/bin/curl https://e.example/", ["e.example"], False),
        # A variable set for it, or a change of the shell's own state before it, may send it
        # elsewhere; the locale, or shell options, do not.
        ("HTTPS_PROXY=http://proxy.example curl https://a.example/", ["a.example"], True),
        ("sudo HTTPS_PROXY=http://proxy.example curl https://a.example/", ["a.example"], True),
        ("env HTTPS_PROXY=http://proxy.example curl https://a.example/", ["a.example"], True),
        ("export HTTPS_PROXY=http://proxy.example; curl https://a.example/", ["a.example"], True),
        ("source ./project.env && curl https://a.example/", ["a.example"], True),
        (
            "git -c http.proxy=http://proxy.example clone https://code.example/o/r.git",
            ["code.example"],
            True,
        ),
        ("LANG=C curl https://a.example/", ["a.example"], False),
        ("set -euo pipefail; curl https://a.example/", ["a.example"], False),
        ("command -v jq >/dev/null && curl https://a.example/", ["a.example"], False),
    ],
)
def test_a_network_command_names_its_hosts(command, hosts, unnamed):
    effects = command_effects(command)
    assert effects.network is True
    assert sorted(effects.hosts) == sorted(hosts)
    assert effects.host_unread is unnamed
    assert is_read_only_bash(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q",
        "pip list",
        "npm run build",
        "rsync -a src/ /srv/dst/",
        "scp a.txt b.txt",
        "git clone /srv/repos/notes.git copy",
        "nc -U /tmp/app.sock",
        "git submodule update --no-fetch",
        "python script.py",
    ],
)
def test_a_command_that_establishes_no_network_claims_none(command):
    assert command_effects(command).network is False


@pytest.mark.parametrize(
    "command,targets,unnamed",
    [
        ("echo hi > notes.txt", ["notes.txt"], False),
        ("cd /srv/work && echo hi > out/notes.txt", ["/srv/work/out/notes.txt"], False),
        ("cat > /tmp/probe.py <<'EOF'\nprint($HOME)\nEOF", ["/tmp/probe.py"], False),
        ("mkdir -p /tmp/a /tmp/b", ["/tmp/a", "/tmp/b"], False),
        ("cp a.txt b.txt /srv/dst/", ["/srv/dst/"], False),
        ("cp -t /srv/dst a.txt", ["/srv/dst"], False),
        ("mv ~/Documents/x ./x", ["./x", "~/Documents/x"], False),
        ("touch a b", ["a", "b"], False),
        ("tee -a /var/log/x.log", ["/var/log/x.log"], False),
        ("rm -rf build/*", ["build"], False),
        ("rm -rf .*", [], True),
        ("sort -o /tmp/sorted.txt data.txt", ["/tmp/sorted.txt"], False),
        ("curl -s -o /tmp/page.html https://a.example/", ["/tmp/page.html"], False),
        ("wget https://a.example/x.tgz", ["."], False),
        ("git clone https://code.example/o/r.git /srv/r", ["/srv/r"], False),
        ("git -C /srv/repo commit -m m", ["/srv/repo/."], False),
        ("find /srv/cache -name '*.pyc' -delete", ["/srv/cache"], False),
        ("dd if=a of=/srv/b", ["/srv/b"], False),
        ('echo x > "$TMPDIR/probe.txt"', ["$TMPDIR/probe.txt"], False),
        ("echo x > $OUT", [], True),
        ("ls && cd sub && echo x > f", [], True),
        ("chmod 755 run.sh", ["run.sh"], False),
        ("xargs rm < list.txt", [], True),
        ("pip install --target /srv/site x", ["/srv/site"], False),
        # A command that may move the shell (`eval`, `command cd`) loses where a relative path
        # lies; one that changes a program's variables loses where that program writes, though
        # the shell's own redirect still goes where it says.
        ("eval 'cd /srv'; echo x > notes.txt", [], True),
        ("command cd /srv && echo x > notes.txt", [], True),
        ("git --work-tree=/srv/site checkout -- index.html", ["."], True),
        ("export OUT=1; echo hi > out.txt", ["out.txt"], False),
        ("PYTHONPATH=src python build.py > out.txt", ["out.txt"], False),
        ("GIT_AUTHOR_NAME=Alex git commit -m m", ["."], False),
        # `git config` writes the settings it names: its scope's, or `--file`'s.
        ("git config core.hooksPath .githooks", [".git/config"], False),
        ("git config core.abbrev -1", [".git/config"], False),
        ("git config --unset core.pager", [".git/config"], False),
        ("git config --global user.name Alex", ["~/.gitconfig"], False),
        ("git config set --global core.editor vi", ["~/.gitconfig"], False),
        ("git config set --worktree core.sparseCheckout true", [".git/config.worktree"], False),
        ("git -C /srv/repo config core.fsmonitor false", ["/srv/repo/.git/config"], False),
        (
            "cd /srv/repo && git config user.email alex@example.com",
            ["/srv/repo/.git/config"],
            False,
        ),
        ("git config -f .gitmodules submodule.theme.url ../theme", [".gitmodules"], False),
        ("git config --file=site.ini site.title Notes", ["site.ini"], False),
    ],
)
def test_a_write_names_its_paths(command, targets, unnamed):
    effects = command_effects(command)
    assert effects.writes is True
    assert sorted(effects.targets) == sorted(targets)
    assert effects.target_unread is unnamed


@pytest.mark.parametrize(
    "command",
    [
        "ls > /dev/null",
        "curl -s -o /dev/null https://a.example/",
        "grep -rn TODO notes 2>/dev/null",
    ],
)
def test_output_thrown_away_is_no_file_written(command):
    effects = command_effects(command)
    assert "/dev/null" not in effects.targets


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF'\nhello\nEOF",
        "cat <<EOF\nhello\nEOF",
        "cat <<EOF\n$(rm -rf /tmp/x)\nEOF",
        "cat <<EOF\nnever closed",
        "echo $TMPDIR",
        "ls $HOME",
        "env ls",
        "timeout 5 ls",
        "bash -c 'ls'",
        "/bin/cat notes.txt",
        "FOO=1 ls",
        "git --version extra",
    ],
)
def test_reading_more_never_makes_a_command_a_read(command):
    assert is_read_only_bash(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "git config --get user.name",
        "git config core.editor",
        "git config --list --show-origin",
        "git config -l",
        "git config get core.editor",
        "git config list",
        "git config --global --get-regexp alias",
    ],
)
def test_a_git_config_that_gets_or_lists_writes_nothing(command):
    """A form that gets or lists a setting writes no file, and it stays no read-only form: what
    git does with a setting is whatever the setting names."""
    effects = command_effects(command)
    assert effects.writes is False and not effects.targets
    assert is_read_only_bash(command) is False


def test_a_launch_by_argv_is_read_as_its_program():
    from personalclaw.command_effects import argv_effects

    effects = argv_effects(["/opt/node/bin/npx", "-y", "skills", "find", "commit message"])
    assert effects.network and sorted(effects.hosts) == NPM

    effects = argv_effects(
        ["/usr/bin/git", "-c", "core.fsmonitor=", "clone", "https://code.example/o/r.git", "/srv/x"]
    )
    assert sorted(effects.hosts) == ["code.example"] and sorted(effects.targets) == ["/srv/x"]

    assert argv_effects(["/usr/bin/git", "-C", "/srv/repo", "status"]).network is False
    # One argument is one word: no shell reads it.
    assert argv_effects(["echo", "$(curl https://a.example/)"]).network is False
