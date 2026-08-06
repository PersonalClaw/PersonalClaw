# Platforms

Where PersonalClaw runs and what to know per platform. The recommended install
paths (`uv tool`, the bootstrap one-liner, Docker Compose) are the same
everywhere — see [Getting started](getting-started.md). This page covers only
the platform-specific gotchas.

| Platform | Support | Proof |
|---|---|---|
| Linux x86-64 | first-class | CI test + release smoke |
| Linux arm64 | first-class | CI test + release smoke |
| macOS (Apple silicon / Intel) | first-class | CI test |
| Windows via WSL2 | supported | this guide + release checklist |
| Windows via Docker Desktop | supported | this guide |
| Windows native | not supported | — |

---

## Windows via WSL2

Windows has no native build. The supported path is **WSL2** (Windows Subsystem
for Linux, version 2): a real Linux kernel inside Windows where PersonalClaw
runs as an ordinary Linux install. The Windows-side browser reaches the
dashboard through WSL2's automatic localhost forwarding.

If you would rather not run a Linux shell at all, use the
[Docker Compose path](getting-started.md#docker-compose) on Docker Desktop
instead (Docker Desktop itself uses a WSL2 backend, but you never touch the
Linux shell). The rest of this section is for running PersonalClaw directly in
WSL2.

### 1. Install in WSL2

From a WSL2 shell (Ubuntu or any distro), install exactly as on Linux — with
`uv`, which brings its own Python 3.12:

```bash
uv tool install personalclaw
personalclaw setup      # interactive: name + first provider credential
personalclaw gateway
```

`personalclaw doctor` prints a `platform: WSL detected` line and tells you
whether the background service will work (see below).

### 2. Keep your home on ext4, NOT on /mnt/c — this matters

Store `~/.personalclaw/` on the WSL **ext4** filesystem (i.e. under your Linux
home, `/home/<you>`), **not** under `/mnt/c` (the mounted Windows drive).

The `/mnt/c` mount crosses the Windows/Linux filesystem boundary (a 9P network
protocol), and small random I/O across it is dramatically slower — often
10-20x. PersonalClaw's SQLite databases and FTS index do exactly that kind of
I/O, so a home on `/mnt/c` makes chat history, memory, and search crawl.

Leave `PERSONALCLAW_HOME` unset (defaults to `~/.personalclaw`) or point it at
another ext4 path. Do not set it to a `/mnt/c/...` path.

### 3. Opening the dashboard (localhost forwarding + wslview)

WSL2 automatically forwards `localhost` between Windows and the Linux VM, so the
dashboard URL the gateway prints (`http://localhost:10000/...`) opens directly
in a **Windows** browser.

On boot the gateway prints the URL prominently and then tries to open it. Inside
WSL there is no Linux browser to launch, so PersonalClaw hands the URL to
[`wslview`](https://github.com/wslutilities/wslu) (from the `wslu` package),
which opens it in your Windows default browser. Most WSL distros ship `wslu`; if
`wslview` is missing, install it (`sudo apt install wslu`) or just click the URL
the gateway printed. Auto-open never blocks startup — a missing `wslview` is not
an error.

### 4. Background service needs systemd (opt-in on WSL2)

`personalclaw service install` registers a systemd unit so the gateway starts on
boot and restarts on failure. WSL2 runs systemd only when you opt in. Enable it
once:

1. Create or edit `/etc/wsl.conf` inside your distro:

   ```ini
   [boot]
   systemd=true
   ```

2. From **Windows** (PowerShell or CMD), fully restart the distro so the change
   takes effect:

   ```powershell
   wsl --shutdown
   ```

   Reopen your WSL shell. `personalclaw doctor` should now report
   `service: systemd active`.

Without systemd the background service will not persist. In that case, either
run the gateway in a foreground shell (`personalclaw gateway`) whenever you need
it, or start it on Windows login via **Task Scheduler** with a
`wsl -d <distro> -- personalclaw gateway` action.
