# Google Colab

Use `notebooks/NEXUS_Colab.ipynb`. The notebook does not contain an API key. If you save a copy, do not paste the key into a cell.

## What the notebook does

1. Checks Python and Node.
2. Clones this repository with the OpenCode submodule, or uses an existing checkout.
3. Installs NEXUS with `pip install -e .`.
4. Optionally installs `@opencode/cli@2.0.27`.
5. Optionally installs Python 3.14.7 with `uv` for Free Claude Code. That step is separate because it is large and was not completed in the development environment.
6. Asks for the KiosAPI key with `getpass` and exports `NEXUS_KIOSAPI_API_KEY` for the process only.
7. Creates `/content/workspaces/demo` and allows that root.
8. Starts NEXUS on `0.0.0.0` with a random control token.
9. Prints the Colab proxy URL and a link that includes the token once.
10. Calls `GET /api/diagnostics`. The same report is available from `nexus doctor`.
11. Stops the process group.

## Expected versions

| Tool | Upstream requirement | NEXUS control plane |
| --- | --- | --- |
| Python | 3.14.7 for Free Claude Code | 3.11+ |
| Node.js | 18+ is enough for the OpenCode installer and the skills version check. OpenCode's own repo pins Bun 1.4.2 for building from source. | Node 22 was used to install `@opencode/cli@2.0.27` |
| GPU | none | none. Models are remote. |

## Access

Colab's proxy reaches the notebook VM. The control plane still requires the token when it is bound to `0.0.0.0`. Do not set `--inject-ui-token` on Colab unless you accept that anyone who can open the page receives the control token. The notebook uses the token in the printed link instead.

## Stop

Run the last cell, or interrupt the kernel. The cell sends `SIGTERM` to the process group started by the notebook.
