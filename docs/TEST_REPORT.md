# Test report

Date: 2026-10-12. Control plane: Python 3.11.2. Free Claude Code tests: Python 3.14.7 built from the CPython tag. OpenCode tests: Bun 1.4.2. Node v22.22.3. No KiosAPI credential.

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
| Free Claude Code `pytest` | passed later on Python 3.14.7. See below. The first record in `docs/UPSTREAM_TEST_RESULTS.json` is the earlier skip. |
| OpenCode `bun turbo test` | executed later with Bun 1.4.2. See below. |
| `opencode` | `@opencode/cli@2.0.27` installed and started from NEXUS |

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

## Free Claude Code original tests

Python 3.14.7 was built from `https://github.com/python/cpython` tag `v3.14.7` because the GitHub release CDN was not reachable. `uv sync --locked` installed the locked dependencies, including the development group, into `third_party/free-claude-code/.venv`. The package version was pretended as `0.0.0` because the snapshot has no `.git` metadata for hatch-vcs. The tests still imported the vendored source.

Command, matching `.github/workflows/tests.yml`:

```text
uv run --locked pytest -v --tb=short -o thread_dump_timeout=15 --durations=50
```

Result: `9045 passed, 17 skipped` in 153.21s. Exit code 0. This is the upstream `tests/` suite. The Playwright e2e job and the smoke suite were not run.

## OpenCode original tests

Bun 1.4.2 came from the npm package `@oven/bun-linux-x64@1.4.2`. The GitHub release zip redirects to a host this environment cannot reach. `bun install --frozen-lockfile` installed 2161 packages and failed to download two: `@solidjs/start` from `pkg.pr.new`, and `ghostty-web` from the GitHub API. `ghostty-web` was fetched from `codeload.github.com` at the locked commit and the upstream patch was applied. `@solidjs/start` is used by console, enterprise, and stats apps, which are not in the unit-test task graph.

`bun run test:effect-simplification-rules`: 10 passed. `bun run lint:effect-simplifications`: exit 0.

The CI command `GITHUB_ACTIONS=false bun turbo test` stopped at the first failure. A second run used `--concurrency=1 --continue=dependencies-successful` so the rest of that same task graph could finish. Results:

| Task | Result |
| --- | --- |
| `@opencode/codemode#test` | 1398 passed |
| `@opencode/httpapi-codegen#test` | 98 passed |
| `@opencode/function#test` | 5 passed |
| `@opencode/ui#test` | 152 passed |
| `@opencode/session-ui#test` | 249 passed |
| `@opencode/app#test` | 749 passed, then 168 passed |
| `@opencode/latex#test` | 123 passed |
| `@opencode/tui#test` | 1463 passed, 2 skipped |
| `@opencode/ai#test` | 1793 passed, 28 skipped |
| `@opencode/server#test` | 82 passed, 1 skipped |
| `@opencode/cli#test` | 326 passed, 9 skipped |
| `@opencode/core#test` | 6281 passed, 365 skipped, 2 failed, then the two failures were retested |
| `@opencode/desktop#test` | 113 passed, 1 skipped, 2 failed |

The two core failures were `bash -lc bun` exiting 127 because a login shell did not see Bun. After linking Bun into `/usr/local/bin`, `bun run script/test.ts test/worktree.test.ts` reported 35 passed, 0 failed. No OpenCode source file was changed.

The two desktop failures are `unable to verify the first certificate` while `@electron/get` downloads the Electron 44.4.5 binary. The npm package does not contain that binary, and the release host is not reachable from here. Those two tests were not rerun successfully.

Package scripts outside the turbo `test` graph were also run directly. `plugin` 9 passed, `util` 180 passed, `http-recorder` 53 passed, `plugin-browser` 44 passed, `simulation` 47 passed, `theme` 2 passed. `gui-extensions` failed once with a missing `ghostty-web` link, then passed 177 with 1 skipped after the link was added. `client` had 220 passed and 4 failed. `sdk` had 35 passed and 4 failed. `merman` had 504 passed and 1 failed. Those three were not part of the CI `turbo test` graph and were not patched.

## Real engine start from NEXUS

`nexus serve --host 0.0.0.0 --port 8787` was running. `POST /api/engines/free-claude-code/start` launched `/.../.venv/bin/python -c "from free_claude_code.cli.entrypoints import serve; serve()"` on Python 3.14.7. `GET` of that process's `/health` returned `{"status":"healthy"}`. `POST /api/engines/opencode/start` launched `opencode serve --hostname 127.0.0.1`. `GET /api/info` returned version `2.0.27`. The processes were then stopped. This is not a KiosAPI completion.

## Secrets

No KiosAPI key was present in the environment, in `secrets.json`, or in accessible GitHub Actions secrets. A live call to `https://mp.kaggle.net` was not made.

A probe with a fake key, not printed here, showed: `secrets.json` mode `0600` and outside the repository; the OpenCode config file contains `{env:NEXUS_KIOSAPI_API_KEY}` and not the key; the OpenCode process environment receives the key because `packages/core/src/config/variable.ts` resolves `{env:VAR}` from `process.env`. Shell tools inherit that environment. The design was inspected and not changed. A project path that contains `secrets.json` is rejected. `git ls-files` does not track `secrets.json`.

## Not verified

- A real HTTP call to `https://mp.kaggle.net` with model `kiosapi/north-mini-code`. No credential was available.
- Free Claude Code Playwright e2e and smoke jobs.
- OpenCode desktop tests that need the Electron binary.
- A prompt that reaches KiosAPI through either engine.
- Claude Code CLI harness launch (`fcc-claude`). The launcher source is present; the `claude` binary was not installed.
