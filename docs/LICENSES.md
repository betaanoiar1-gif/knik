# Licenses

| Component | License | Notice |
| --- | --- | --- |
| NEXUS control plane | AGPL-3.0-only | `LICENSE`, `NOTICE` |
| Free Claude Code `4e99ddaa5cf453d4ef22fae7c17f7f3aacc3c52b` | AGPL-3.0-only | `third_party/free-claude-code/LICENSE` |
| OpenCode `80fb31ebb7e8dfa5c26fc51ca81da8e06b618a52` | MIT | `third_party/opencode/LICENSE` |
| Skills `49dd158d1076134a641b33efb035946536778336` | MIT | `third_party/skills/LICENSE` |

Free Claude Code is included as an unmodified source snapshot so the corresponding source is in this repository. OpenCode is included as a git submodule at the pinned commit; `git submodule update --init --depth 1 third_party/opencode` fetches it. Skills are an unmodified snapshot, including their `LICENSE`.

MIT code is not relicensed. It can be taken alone under MIT. The combined network service is distributed under AGPL-3.0-only because it incorporates and serves the AGPL program. No upstream file inside the three trees was patched.
