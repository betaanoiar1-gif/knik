# NEXUS

لوحة تحكم واحدة لتشغيل **Free Claude Code** و**OpenCode** و**Skills** كما هي، مع إعداد KiosAPI من الواجهة ومن دون كتابة المفتاح في Git.

NEXUS is a control plane. It does not replace the upstream engines with stubs.

- Free Claude Code is the unmodified AGPL snapshot in `third_party/free-claude-code`, started through `free_claude_code.cli.entrypoints:serve`.
- OpenCode is the MIT submodule in `third_party/opencode`, started through the real `opencode serve` binary and its v2 HTTP API.
- Skills are the unmodified MIT snapshot in `third_party/skills`. Installation symlinks the original directory into the path each engine already scans.

Default KiosAPI settings, editable in the UI:

- host: `https://mp.kaggle.net`
- model: `kiosapi/north-mini-code`

A live call to that host was **not** made in this environment. There is no KiosAPI credential here. Request shape, retries, and error mapping are tested against a local mock. Do not treat a green test run as a successful provider call.

## License

The NEXUS control plane is AGPL-3.0-only because it vendors and serves Free Claude Code. OpenCode and Skills stay MIT. See `NOTICE` and `docs/LICENSES.md`.

NEXUS is not affiliated with Anthropic, Anomaly, Matt Pocock, Kaggle, or the KiosAPI operators.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
export NEXUS_AUTH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
export NEXUS_DATA_DIR="$PWD/.nexus-data"
.venv/bin/nexus serve --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787`. Loopback clients are accepted. Put the KiosAPI key in the password field. It is stored in `$NEXUS_DATA_DIR/secrets.json` with mode `0600` and is not written to the repository.

To bind beyond loopback you must set `NEXUS_AUTH_TOKEN`. The server refuses a public bind without it.

Google Colab steps are in `notebooks/NEXUS_Colab.ipynb` and `docs/COLAB.md`.

## What the UI actually does

| Control | API | Effect |
| --- | --- | --- |
| Start / stop engine | `POST /api/engines/{id}/start` | Starts or stops that engine process only |
| Save settings | `PUT /api/settings` | Rewrites non-secret settings. No rebuild |
| Save key | `PUT /api/kiosapi/secret` | Stores the key outside Git |
| Dry probe | `POST /api/kiosapi/probe` `dry_run=true` | Shows the redacted request. No network call |
| Live probe | `POST /api/kiosapi/probe` `dry_run=false` | Sends one bounded request if a key exists |
| Install skill | `POST /api/skills/install` | Symlink only. Original `SKILL.md` is not edited |
| Run / cancel task | `POST /api/tasks`, `POST /api/tasks/{id}/cancel` | Dispatches to the selected engine and records redacted logs |

The mock engine appears only when `enable_mock_engine` is true. It is labeled `test-double` and is not a coding engine.

## Engine runtimes

Free Claude Code upstream requires Python **3.14.7**. NEXUS will not start it on another interpreter. Set `NEXUS_FCC_PYTHON` after installing that exact version, for example with `uv python install 3.14.7`.

OpenCode needs the OpenCode 2 binary. The pinned source is v2.0.27 (`80fb31ebb7e8dfa5c26fc51ca81da8e06b618a52`). The matching published package is `@opencode/cli@2.0.27`. Point `NEXUS_OPENCODE_BIN` at it, or put `opencode` on `PATH`.

```bash
npm install @opencode/cli@2.0.27
```

Neither runtime is downloaded automatically by `pip install -e .`.

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest
.venv/bin/python scripts/record_upstream_checks.py
```

`docs/TEST_REPORT.md` records what was actually executed. Free Claude Code pytest passed on Python 3.14.7. OpenCode's turbo unit graph was run with Bun 1.4.2; two desktop tests still fail here because the Electron binary cannot be downloaded. NEXUS tests passing is a separate result from those upstream suites.

## Layout

```text
nexus/                  control plane, adapters, UI
third_party/free-claude-code/   unmodified AGPL snapshot
third_party/opencode/           git submodule, unmodified
third_party/skills/             unmodified MIT snapshot
notebooks/NEXUS_Colab.ipynb
tests/
```
