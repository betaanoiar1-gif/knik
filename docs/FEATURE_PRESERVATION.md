# Features kept from the upstream projects

"Preserved" here means the original file is in the tree and NEXUS did not delete or rewrite it. It does not mean that project's own test suite passed. See `docs/TEST_REPORT.md`.

## Free Claude Code

Commit `4e99ddaa5cf453d4ef22fae7c17f7f3aacc3c52b`. 907 Python files. Tests directory kept.

| Feature | Evidence | How NEXUS uses it |
| --- | --- | --- |
| `fcc-server` entrypoint | `cli/entrypoints.py` `serve` | Adapter runs that function. It does not reimplement the proxy. |
| Anthropic `/v1/messages` | `api/routes.py` | Tasks are posted to the running server's route. |
| OpenAI `/v1/responses` | `api/routes.py` | Present. Not the KiosAPI path. |
| Custom providers | `config/custom_providers.py`, `providers/custom.py` | KiosAPI is configured with the upstream schema: `openai_chat`, `openai_responses`, `anthropic_messages`. |
| Code sessions | `api/code_sessions_routes.py` | Present in the server NEXUS starts. The control plane does not replace that UI. |
| Harness launchers | `cli/launchers/*.py` including `claude.py` and `opencode.py` | Reported as capabilities when `claude` or `opencode` is on `PATH`. Not rewritten. |
| Rate limiter | `core/rate_limit.py` | Left intact. NEXUS has its own bounded client for probes. |
| Provider catalog, admin UI, messaging, voice extra | source tree | Left intact. NEXUS sets `MESSAGING_PLATFORM=none` by default so Discord is not started accidentally. The setting is editable. |

Operational choices, not source edits: `HOST=127.0.0.1`, `FCC_OPEN_BROWSER=false`, proxy auth enabled with a NEXUS-generated token that is not the KiosAPI key, isolated `HOME`.

## OpenCode

Commit `80fb31ebb7e8dfa5c26fc51ca81da8e06b618a52` on `dev`. Submodule, not a rewrite.

| Feature | Evidence | How NEXUS uses it |
| --- | --- | --- |
| `opencode serve` | `packages/cli/src/commands/handlers/serve.ts` | Adapter execs `serve --hostname 127.0.0.1 --port`. |
| Session API | `packages/protocol/src/groups/session.ts` | Create, prompt, interrupt, message list. |
| Directory header | `packages/server/src/location.ts` `x-opencode-directory` | Sent on every session call. |
| Anthropic-compatible provider | `packages/ai/src/providers/anthropic-compatible.ts` | Used for KiosAPI Anthropic wire format. |
| OpenAI-compatible provider | `packages/ai/src/providers/openai-compatible.ts` | Used for KiosAPI OpenAI wire format. |
| Skill discovery | `packages/core/src/config/plugin/skill-file.ts` and docs | Installs into `.opencode/skills/<name>/SKILL.md`. Id is the directory name. |
| Agents, MCP, plugins, TUI, desktop | source tree | Not deleted. The control plane uses server mode so a headless Colab session can drive it. The TUI is still the upstream binary. |

Verified against the published binary `@opencode/cli@2.0.27` in this environment: `serve`, `GET /api/info`, `POST /api/session`, `POST /api/session/:id/prompt`, `POST /api/session/:id/interrupt`, and a symlinked `tdd` skill embedded by the real server. Details are in `docs/TEST_REPORT.md`. Provider execution through KiosAPI was not part of that check.

## Skills

Commit `49dd158d1076134a641b33efb035946536778336`. 38 `SKILL.md` files. `plugin.json` version 1.3.1.

NEXUS reads `name` and `description` and symlinks the whole skill directory. Supporting files (`tests.md`, `mocking.md`, scripts) stay in the original directory and remain reachable through the link. OpenCode's name rule `^[a-z0-9]+(-[a-z0-9]+)*$` is checked. A skill that fails it is listed and is not installed into OpenCode. The original file is not renamed.

Claude Code / FCC discover `.claude/skills/<name>/SKILL.md`. OpenCode also discovers that path, plus `.opencode/skills` and `.agents/skills`. NEXUS installs into the engine-specific directory instead of assuming one path is enough.
