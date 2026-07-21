# personalclaw-client

An async Python client for the [PersonalClaw](https://personalclaw.dev) Gateway.

`personalclaw-client` is the small, dependency-light library that apps and
external tools use to talk to a running PersonalClaw gateway — send messages,
read status, resolve an app's data directory — without vendoring the core
package. It is versioned and published independently of the core gateway so a
tool can pin a stable client contract.

## Install

```sh
pip install personalclaw-client
# or
uv pip install personalclaw-client
```

## Usage

```python
from personalclaw_client import PersonalClawClient

async with PersonalClawClient(app_name="my-app") as pc:
    ok = await pc.ping()
    status = await pc.get_status()
    await pc.send_message("session-1", "hello")
```

## Errors

Client calls raise `PersonalClawError` (with a machine-readable `ErrorCode`) on
gateway/transport failures — catch it to distinguish "gateway unreachable" from
"bad request".

## Links

- Homepage: https://personalclaw.dev
- Source & issues: https://github.com/PersonalClaw/PersonalClaw
- License: MIT
