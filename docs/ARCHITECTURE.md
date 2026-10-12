# Architecture

NEXUS is a process supervisor and a small HTTP control plane. The engines remain separate programs.

```text
browser
  │  bearer token, or loopback when bound to 127.0.0.1
  ▼
NEXUS FastAPI  (nexus/app.py)
  ├─ Free Claude Code adapter ── subprocess ── python3.14 -c "entrypoints.serve"
  │     env: FCC_CUSTOM_PROVIDERS, MODEL, HOST=127.0.0.1, MESSAGING_PLATFORM
  │     tasks: POST http://127.0.0.1:<port>/v1/messages
  ├─ OpenCode adapter ── subprocess ── opencode serve --hostname 127.0.0.1
  │     config file has no API key; key is {env:NEXUS_KIOSAPI_API_KEY}
  │     tasks: POST /api/session, POST /api/session/:id/prompt, POST .../interrupt
  └─ Skills catalog ── read-only scan of third_party/skills
        install: symlink into <project>/.claude/skills or .opencode/skills
```

## Why the adapters differ

Free Claude Code custom providers are defined in `src/free_claude_code/config/custom_providers.py`. Anthropic traffic is posted to `{base_url}/messages` with `x-api-key`. OpenAI chat traffic uses the OpenAI SDK against `base_url`. NEXUS writes `FCC_CUSTOM_PROVIDERS` in that schema and sets `MODEL` to `custom_<32 hex>/<exact model>`. FCC splits on the first slash, so the upstream model stays `kiosapi/north-mini-code`.

OpenCode 2 does not use that schema. Its provider packages live in `packages/ai/src/providers/`. NEXUS selects:

- `@opencode/ai/providers/anthropic-compatible` for `anthropic_messages`
- `@opencode/ai/providers/openai-compatible` for `openai_chat`

It does **not** reuse FCC's launcher package `@opencode/ai/providers/openai/responses`. That package talks to FCC's `/v1/responses` proxy, not to KiosAPI.

If the selected auth style is bearer and the wire format is Anthropic, FCC is refused with `incompatible_auth` because its Anthropic transport cannot send a bearer token. OpenCode can, via `authToken`. The UI does not silently switch formats.

## Credential separation

| Secret | FCC process | OpenCode process | Mock engine | HTTP responses |
| --- | --- | --- | --- | --- |
| KiosAPI key | inside `FCC_CUSTOM_PROVIDERS` only | `NEXUS_KIOSAPI_API_KEY` only | never | redacted |
| FCC proxy token | `ANTHROPIC_AUTH_TOKEN` | not set | never | not returned |
| OpenCode server password | not set | `OPENCODE_SERVER_PASSWORD` | never | not returned |
| NEXUS control token | removed | removed | never | not returned |

Each engine gets `HOME` pointed at its own directory under the NEXUS data dir, so it does not read or write the user's `~/.fcc` or `~/.config/opencode`.

## Failure isolation

Starting one engine is a separate call. An exception is caught, stored on that engine, and returned as HTTP 409. The other engine objects are not stopped. A failed task is a row in the task ledger; the next task can still be submitted.

## What is not claimed

- KiosAPI at `https://mp.kaggle.net` was not called.
- Free Claude Code's pytest suite was not executed (Python 3.14.7 is not installed).
- OpenCode's bun test suite was not executed (`bun` and workspace `node_modules` are absent).
- A button is not proof that an engine binary is installed. The engine status says `runtime_ready: false` until the interpreter or binary is actually found.
