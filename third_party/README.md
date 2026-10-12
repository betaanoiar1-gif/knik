# Upstream snapshots

These trees are the original projects NEXUS integrates. They are not rewritten
copies and not README-only pointers.

| Path | Project | License | How it is included |
| --- | --- | --- | --- |
| `free-claude-code/` | [Alishahryar1/free-claude-code](https://github.com/Alishahryar1/free-claude-code) | AGPL-3.0-only | Unmodified snapshot. Commit recorded in `UPSTREAM.json`. |
| `skills/` | [mattpocock/skills](https://github.com/mattpocock/skills) | MIT | Unmodified snapshot. Commit recorded in `UPSTREAM.json`. |
| `opencode/` | [anomalyco/opencode](https://github.com/anomalyco/opencode) | MIT | Git submodule pinned in `.gitmodules` and `UPSTREAM.json`. |

NEXUS does not patch these trees. Configuration is applied from outside:

- Free Claude Code is started with its own `free_claude_code.cli.entrypoints:serve` entrypoint and its documented environment variables (`FCC_CUSTOM_PROVIDERS`, `MODEL`, `HOST`, `PORT`, `MESSAGING_PLATFORM`).
- OpenCode is started with its own `opencode serve` command and a process-local config that uses OpenCode 2 provider packages (`@opencode/ai/providers/anthropic-compatible` or `@opencode/ai/providers/openai-compatible`), not the Free Claude Code responses proxy package.
- Skills stay in their original `SKILL.md` layout. NEXUS only symlinks a skill directory into the engine-specific discovery path.

Clone with submodules:

```bash
git clone --recurse-submodules https://github.com/betaanoiar1-gif/knik.git
# or, after a normal clone:
git submodule update --init --depth 1 third_party/opencode
```
