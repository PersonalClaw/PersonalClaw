# Platforms

Where PersonalClaw runs and what to know per platform. The recommended install
paths (`uv tool`, the bootstrap one-liner, Docker Compose) are the same
everywhere — see [Getting started](getting-started.md). This page covers only
the platform-specific gotchas.

## Support matrix

Every row names the mechanism that **proves** it. No row claims "supported" without
one, and the token points at something you can go read or re-run:

- `CI:<job>` — a job in `.github/workflows/` that runs the suite (or a release
  smoke) on that platform. Green on `main`/nightly is the evidence.
- `checklist:<section>` — a documented manual walkthrough; evidence is a recorded
  run, not a CI job.
- `community` — reported working by users; **not** verified by us.

| Platform | Support | Proof |
|---|---|---|
| Linux x86-64 | first-class | `CI:full/matrix (ubuntu-latest)` + `CI:release/images smoke (linux/amd64)` |
| Linux arm64 | first-class | `CI:full/matrix (ubuntu-24.04-arm)` + `CI:release/images smoke (linux/arm64)` |
| macOS Apple silicon | first-class | `CI:full/matrix (macos-14, macos-latest)` |
| macOS Intel | best-effort | `community` — no Intel runner in CI; the x86-64 Python/wheel path is the same as Linux x86-64 |
| Windows via WSL2 | supported | `checklist:Windows via WSL2` (this page) |
| Windows via Docker Desktop | unverified | no recorded walkthrough. The [Docker Compose path](getting-started.md#docker-compose) is expected to work (Docker Desktop runs a Linux VM, and the release images are multi-arch), but nobody has driven it end to end, so this row does **not** claim support. PLATFORM-REACH B1.1 (atom `PR-6`) owns writing that section. |
| Windows native | not supported | — see [windows-native-audit](../roadmap/research/windows-native-audit.md) |

The arm64 rows became CI-backed in PLATFORM-REACH A1.3 (arm jobs in `full.yml`) and
A2.1 (per-arch release smoke); before that they were aspirational.

---

## The `[models]` extra, per architecture

`pip install 'personalclaw[models]'` pulls the local-embedding stack. Wheel
availability — not PersonalClaw — is what varies by arch. Read from the committed
`uv.lock` (the resolver's own record, so it stays honest as versions move):

| Package | x86-64 | arm64 (macOS) | arm64 (Linux) | Note |
|---|---|---|---|---|
| `faiss-cpu` | ✅ wheel | ✅ `macosx_14_0_arm64` | ✅ `manylinux_2_28_aarch64` + `musllinux_1_2_aarch64` | musllinux wheel means Alpine works too |
| `torch` | ✅ wheel | ✅ `macosx_14_0_arm64` | ✅ `manylinux_2_28_aarch64` | CPU build; no CUDA on arm |
| `sentence-transformers` | ✅ | ✅ | ✅ | `py3-none-any` — pure Python, arch-independent |

**So `[models]` installs from wheels on every arch we claim** — no source build, no
compiler needed. Verify it yourself without an arm box:

```bash
python3 - <<'PY'
import re
blk = re.search(r'\[\[package\]\]\nname = "torch"(.*?)(?=\n\[\[package\]\]|\Z)',
                open("uv.lock").read(), re.S).group(1)
print([w for w in re.findall(r'([\w.\-]+\.whl)', blk) if "aarch64" in w or "arm64" in w])
PY
```

### RAM floor on Pi-class boards

The embedding stack, not the gateway, is what strains small boards. The gateway
itself is light; `torch` + a loaded embedding model is the heavy part.

- **< 2 GB RAM** — skip the extra. Install plain `personalclaw` and use a remote
  provider for embeddings. Everything except local embedding works unchanged.
- **2–4 GB (Pi 4/5 class)** — `[models]` can work, but add swap before first use;
  the model load is the spike, not steady state:
  ```bash
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  # persist: echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```
  Prefer a small model, and expect the first ingest to be slow.
- **≥ 4 GB** — no special handling.

If you hit an OOM kill during ingest rather than at startup, it is the model load —
add swap or drop the extra; it is not a database or gateway problem.

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
