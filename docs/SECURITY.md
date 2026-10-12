# Security notes

This is the review applied to the control plane, not a claim that the upstream engines are free of vulnerabilities.

## Secrets

- API keys are accepted only through `PUT /api/kiosapi/secret`, the `NEXUS_KIOSAPI_API_KEY` environment variable, or Colab `getpass`. They are not in the notebook source, `.env.example`, or tests.
- The store is `$NEXUS_DATA_DIR/secrets.json`, written via a `0600` temp file and `os.replace`.
- Logs and task events pass through `nexus.redact.Redactor` before they are stored or returned.
- Settings JSON cannot contain the key. The OpenCode config file stores `{env:NEXUS_KIOSAPI_API_KEY}`, not the key.
- `.gitignore` ignores `.env`, `.nexus-data/`, and `secrets.json`.

## Network

- Default bind is `127.0.0.1`.
- Binding any other host without `NEXUS_AUTH_TOKEN` exits with status 2.
- When the bind is loopback and `NEXUS_LOOPBACK_OPEN=1` (the default), only clients whose address is `127.0.0.1` or `::1` may skip the token. The test client host `testclient` is not treated as loopback.
- Engine child processes bind `127.0.0.1` even if FCC's own default is `0.0.0.0`.
- `--inject-ui-token` embeds the control token in the HTML for a local preview. Do not use it on a shared network, and do not type a real provider key into that page.

## Projects and processes

- A project path must be absolute, must exist, must not be `/`, `/etc`, `/proc`, `/sys`, `/dev`, `/root`, `/boot`, or `/run`, and must sit under an allowed root.
- A project that contains the secrets file is rejected.
- Child processes are started with an argument list and `start_new_session=True`. Cancellation sends `SIGTERM` to the process group, then `SIGKILL`.
- Task timeout and concurrency limits are settings (`task_timeout_seconds`, `max_concurrent_tasks`).
- KiosAPI retries stop after `kios_max_retries`. HTTP 401 and 403 are not retried. `Retry-After` is honored and capped.

## Residual risk

OpenCode 2's default `serve` mode keeps its environment for tool subprocesses. The KiosAPI key therefore exists in that process environment so `{env:NEXUS_KIOSAPI_API_KEY}` can resolve. A task that runs a shell inside OpenCode can read it. NEXUS does not copy the key into the project or into the FCC proxy token, and it does not put the NEXUS control token in the child environment. Isolate the project directory from the secrets file, which the path check enforces.

FCC's Anthropic transport sends `x-api-key`. A gateway that only accepts `Authorization: Bearer` will fail that path with a clear `incompatible_auth` or HTTP 401. Switch wire format to `openai_chat` for FCC, or use bearer with OpenCode.
