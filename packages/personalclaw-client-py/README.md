# personalclaw-client

An async Python client for the [PersonalClaw](https://personalclaw.dev) Gateway.

`personalclaw-client` is the small, dependency-light library that apps and
external tools use to talk to a running PersonalClaw gateway — send messages,
read status, resolve an app's data directory — without vendoring the core
package. It is a package of its own, released with the gateway and at the
gateway's version: every release bumps both, whether or not the client changed,
so `personalclaw-client` X.Y.Z is the client that shipped with gateway X.Y.Z.

## Install

```sh
pip install personalclaw-client
# or
uv pip install personalclaw-client
```

## Usage

```python
import os

from personalclaw_client import PersonalClawClient

# The owner token: the value after `?token=` in the link `personalclaw token` prints.
async with PersonalClawClient(token=os.environ["PERSONALCLAW_TOKEN"]) as pc:
    ok = await pc.ping()
    status = await pc.get_status()
    await pc.create_session("session-1")
    await pc.send_message("session-1", "hello")
```

The client sends the token as `Authorization: Bearer`.

## Errors

Client calls raise `PersonalClawError` (with a machine-readable `ErrorCode`) on
gateway/transport failures — catch it to distinguish "gateway unreachable" from
"bad request".

## Links

- Homepage: https://personalclaw.dev
- Source & issues: https://github.com/PersonalClaw/PersonalClaw
- License: MIT
