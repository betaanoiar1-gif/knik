# Test report

Date: 2026-10-12. Environment: Python 3.11.2, Node v22.22.3, no Bun, no Python 3.14.7, no KiosAPI credential.

This report lists commands that were run. A skipped upstream suite is not a pass.

## NEXUS tests

Command, from a clean virtualenv at `/tmp/nexus-clean` after `pip install -r requirements.txt` and `pip install pytest`, with `PYTHONPATH` pointed at the checkout:

```text
15 passed
```

The same 15 tests passed in the project virtualenv. They cover:

- control-plane auth, including rejection of non-loopback clients when bound to `0.0.0.0`
- UI controls wired to API actions
- engine discovery and a failed engine start that leaves the other engines listed
- task routing to the mock engine, a second task after success, and a second task after failure
- cancellation and thread cleanup
- redaction of a test secret in task output and settings JSON
- path rejection and live settings updates
- KiosAPI request URL, exact model `kiosapi/north-mini-code`, 429 retry, 401 without retry, connection failure
- FCC custom-provider JSON versus OpenCode provider package, and no shared credential fields
- skill symlink install without changing the original `SKILL.md`
- independent session ids for two engines
- upstream file hashes and symbol checks

The FCC and OpenCode adapter tests launch a local stand-in interpreter or binary. They prove the adapter invokes the real entrypoint string and speaks the upstream HTTP shapes. They do not execute the upstream codebases.

## Clean import

`pip install -e .` in the clean virtualenv imported `nexus` from the checkout, so `third_party/` stays beside the package.

## Upstream checks

`python scripts/record_upstream_checks.py` wrote `docs/UPSTREAM_TEST_RESULTS.json`.

| Suite | Result |
| --- | --- |
| `third_party/skills/scripts/list-skills.sh` | passed, 38 `SKILL.md` paths |
| `node scripts/sync-plugin-version.mjs --check` | passed, `plugin.json` version 1.3.1 |
| Free Claude Code `pytest` | skipped. Upstream `requires-python == 3.14.7`. This machine has 3.11.2. Tests remain in `third_party/free-claude-code/tests`. |
| OpenCode `bun test` | skipped. `bun` is not installed and the workspace `node_modules` were not installed. Tests remain in the submodule. |
| `opencode` on the default `PATH` | not installed at the time of that script |

## Live OpenCode binary

This was run separately, after installing the published package that matches the pinned source version.

```text
npm install --prefix /tmp/opencode-cli @opencode/cli@2.0.27
/tmp/opencode-cli/node_modules/.bin/opencode --version
# opencode v2.0.27
```

`opencode serve --hostname 127.0.0.1 --port 4097` printed `server listening on http://127.0.0.1:4097`.

`GET /api/info` with basic auth `opencode:<password>` returned:

```json
{"version":"2.0.27","pid":2937,"urls":["http://127.0.0.1:4097"],"paths":{"tmp":"/tmp/opencode"},"capabilities":{"persistentPty":true}}
```

`POST /api/session` with `title` and `location.directory` returned `data.id` (`ses_ece3ffc29ffe4T95QeRDsYmwiA` on one run).

`POST /api/session/:id/prompt` with `{"text":"say pong"}` returned HTTP 200 and a user message whose payload text was `say pong`.

`POST /api/session/:id/interrupt` returned HTTP 200 and `{"interrupted":true}`.

A symlink from `/tmp/oc-project/.opencode/skills/tdd` to the unmodified `third_party/skills/skills/engineering/tdd` was accepted by a later prompt. The real server embedded the original skill text (`# Test-Driven Development`) in the prompt payload. `GET /api/skill` returned an empty list for that request; discovery at prompt time still worked. The skill file in the snapshot was not edited.

No KiosAPI request was sent during this probe. The published binary was not left running.

## Control-plane process smoke

`python -m nexus.cli serve --host 127.0.0.1 --port 8793` with `NEXUS_DATA_DIR=/tmp/nexus-smoke3` and a non-provider control token:

- `GET /api/health` returned `{"status":"ok","version":"1.0.0"}`.
- `GET /` returned the Arabic/English UI. The control token was not in the HTML.
- `GET /static/app.js` returned the handlers `start-engine`, `submit-task`, `cancel-task`, `install-skill`, and `probe-live`.
- `GET /api/engines` reported both upstream trees installed and `source_ok: true`. `runtime_ready` was false: the interpreter was Python 3.11.2, and `opencode` was not on that process's `PATH`.
- `PUT /api/settings` with `{"enable_mock_engine":true}` kept the model `kiosapi/north-mini-code` and host `https://mp.kaggle.net`.
- A mock task inside the allowed workspace finished `succeeded` with a stdout event. This is the test double, not Free Claude Code or OpenCode.
- `POST /api/kiosapi/probe` with `dry_run: true` returned the Anthropic-shaped URL and model and `verified: false`. No provider request was sent. No API key was present, so none was printed.

## Not verified

- A real HTTP call to `https://mp.kaggle.net` with model `kiosapi/north-mini-code`.
- Free Claude Code process startup on Python 3.14.7.
- Free Claude Code's pytest, smoke, and e2e suites.
- OpenCode's bun unit tests, typecheck, and a prompt that reaches KiosAPI.
- Claude Code CLI harness launch (`fcc-claude`). The launcher source is present; the `claude` binary was not installed.
