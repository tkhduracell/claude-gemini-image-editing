# 🌌 Gemini Image Editing

[![Default model: gemini-nano-banana-2.1](https://img.shields.io/badge/Default%20model-gemini--nano--banana--2.1-orange.svg)](#-models)
[![API: Interactions API](https://img.shields.io/badge/API-Interactions%20API-blue.svg)](GEMINI.md)
[![Protocol: MCP](https://img.shields.io/badge/Protocol-MCP-green.svg)](#-mcp-server-integration)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-lightgrey.svg)](LICENSE)

A Model Context Protocol (MCP) server and Claude Code skill for Google's Gemini
image models, driven through the stateful **Interactions API**.

Unlike stateless image models, the Interactions API persists each turn's visual
context server-side and returns an `interaction_id`. Passing that ID back as
`previous_interaction_id` lets the model edit the existing canvas while
preserving character, style, lighting, and pixel continuity — no re-prompting
the whole scene.

## 🙏 Credits

This project is a fork of **[xbill9/nb2lite-skill-claude](https://github.com/xbill9/nb2lite-skill-claude)**
by [@xbill9](https://github.com/xbill9), which originated the NB2Lite skill, the
MCP server design, and the four-tool surface this project still builds on. All
credit for the original work goes there, and it remains Apache-2.0 with upstream
copyright retained.

This fork renames the project and modernizes it: the current model lineup,
Docker-first install, pinned dependencies, mcp 2.x, real MCP errors, retries,
and CI. See [MODERNIZATION.md](MODERNIZATION.md) for the full rationale.

---

## ✨ Features

- 🎨 **Generate & edit** — text-to-image, stateful multi-turn edits by interaction ID, and inline editing of local files.
- 🔄 **Stateful continuity** — chain interaction IDs to keep character, style, and composition stable across edits.
- 🧠 **Per-model capabilities** — aspect ratio, `image_size`, and `thinking_level` are validated against what each model actually supports, and unknown (future) models pass straight through to the API rather than being rejected by a stale allowlist.
- 🐳 **Docker-first** — the plugin launches a pinned container; nothing to install on the host but Docker.
- 🔐 **Keys by reference** — the API key is never written into a manifest or into git.
- ⚠️ **Real MCP errors** — failures raise protocol errors instead of returning success-shaped text an agent might mistake for a result.

---

## 🧩 Models

Set with `GEMINI_MODEL_NAME`. Default: **`gemini-nano-banana-2.1`**.

| Model | Product | `image_size` | `thinking_level` |
| :--- | :--- | :--- | :--- |
| `gemini-nano-banana-2.1` *(default)* | Nano Banana 2.1 | 1K, 2K, 4K | minimal, medium, high |
| `gemini-3.1-flash-image` | Nano Banana 2 | 1K, 2K, 4K | minimal, high |
| `gemini-3-pro-image` | Nano Banana Pro | 1K, 2K, 4K | low, high |
| `gemini-3.1-flash-lite-image` | Nano Banana 2 Lite | 1K only | minimal, high |

These are **measured against the live API**, not copied from the docs — the
published tables are wrong about thinking levels for every model above. No
level works everywhere: `low` is rejected by the 3.1 family *and* by
nano-banana-2.1, while `minimal` is rejected by `gemini-3-pro-image`. Omit
`thinking_level` and the server picks one the active model accepts.

**Aspect ratios** (all models): `1:1`, `3:2`, `2:3`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`.

> [!NOTE]
> `gemini-nano-banana-2.1` and `gemini-3.1-flash-lite-image` are flagged by
> Google as **short-term availability** — they may be shut down sooner than
> standard stable models. `gemini-3.1-flash-image` and `gemini-3-pro-image`
> carry shutdown dates no earlier than 2027-05-28. A weekly
> [model-drift workflow](.github/workflows/model-drift.yml) opens an issue if
> the configured default disappears.

`image_size` values are case-sensitive — the API rejects lowercase `1k`.

---

## ⚙️ Environment configuration

| Variable | Description | Default |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | API key for the Gemini Developer API. | *required* |
| `GOOGLE_API_KEY` | Fallback if `GEMINI_API_KEY` is unset. | *optional* |
| `GEMINI_MODEL_NAME` | Model to use (see table above). | `gemini-nano-banana-2.1` |
| `IMAGE_OUTPUT_DIR` | Where images are written. | `.` |
| `HOST_OUTPUT_DIR` | **Display only.** The host path that `IMAGE_OUTPUT_DIR` is mounted from, so reported paths are ones you can actually open. See [Docker](#-docker). | *unset* |
| `GEMINI_TIMEOUT_SECONDS` | Per-request timeout. | `120` |
| `GEMINI_RETRY_ATTEMPTS` | Retries on 429/5xx, with exponential backoff. | `3` |
| `MAX_INPUT_IMAGE_MB` | Rejects oversized local inputs before reading them into memory. | `20` |
| `GOOGLE_GENAI_USE_ENTERPRISE` | Opt in to ADC/enterprise auth instead of an API key. See [Authentication](#-authentication). | *unset* |
| `GOOGLE_CLOUD_PROJECT` | Project ID — required when the above is set. | — |
| `GOOGLE_CLOUD_LOCATION` | Location for the enterprise path. | `global` |

---

## 🚀 Install

### Plugin + Docker (recommended)

This is the primary path. It installs the **skill** (so the agent knows how to
chain interaction IDs and when not to change aspect ratio) **and** registers the
MCP server, which runs as a container.

```bash
docker pull ghcr.io/tkhduracell/gemini-image:0.2.0
export GEMINI_API_KEY="$(cat ~/gemini.key)"   # or however you store it
```

Then in Claude Code:


```
/plugin marketplace add tkhduracell/claude-gemini-image-editing
/plugin install gemini-image-editing@claude-gemini-image-editing
```

The plugin manifest passes `-e GEMINI_API_KEY` **by name, with no value**, so
Docker inherits it from the environment and the key never lands in the manifest
or in git. Images are written to `./images` in the project.

### Fallback: pip + `claude mcp add`

For a server-only install with no Docker. Note this does *not* install the
skill — only the four tools.

```bash
git clone https://github.com/tkhduracell/claude-gemini-image-editing.git
cd claude-gemini-image-editing
pip install -r requirements.txt

claude mcp add gemini-image \
  --env GEMINI_API_KEY="$(cat ~/gemini.key)" \
  --env GEMINI_MODEL_NAME=gemini-nano-banana-2.1 \
  --env IMAGE_OUTPUT_DIR=./images \
  -- python3 "$PWD/server.py"
```

Requires Python 3.10+. Restart Claude Code and approve the server; `/mcp` should
list `gemini-image`.

---

## 🔐 Authentication

**Default — API key.** Set `GEMINI_API_KEY`. This is the supported and tested
path.

**Optional — Application Default Credentials.** The Interactions API also has an
enterprise path that uses ADC instead of a key. Note it is `enterprise=True`,
*not* the `vertexai=True` flag you might expect:

```bash
gcloud auth application-default login
export GOOGLE_GENAI_USE_ENTERPRISE=1
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=global
```

Needs `roles/aiplatform.user`, plus the Agent Platform API and billing enabled
on the project.

> [!WARNING]
> **Image output over the enterprise path is unverified.** Google's enterprise
> developer guide documents `response_format` only for JSON schemas and does not
> confirm that `{"type": "image"}` works there. The code supports it behind the
> env flag, but it has not been confirmed to return images. Use the API key path
> unless you have tested otherwise.

---

## 🐳 Docker

Published to the **GitHub Container Registry** as
`ghcr.io/tkhduracell/gemini-image`, built and pushed by CI on a `v*` tag. GHCR
authenticates with the workflow's built-in `GITHUB_TOKEN`, so there is no
registry secret to configure and a fork publishes to its own namespace.

The image contains only `server.py` and its pinned dependencies — no keys, no
Claude Code. `.dockerignore` is deny-all-then-allow, so `.env`, `.mcp.json`, and
key files cannot enter the build context.

It runs as **uid 1000**, not root, so images written through a bind mount are
not root-owned on the host.

```bash
docker run --rm -i \
  -e GEMINI_API_KEY \
  -e GEMINI_MODEL_NAME \
  -e HOST_OUTPUT_DIR="$PWD/images" \
  -v "$PWD/images:/images" \
  ghcr.io/tkhduracell/gemini-image:0.2.0
```

### The output-path gotcha

Inside the container, a saved image is at `/images/gen_….png` — a path the host
cannot open. Set `HOST_OUTPUT_DIR` to the host side of the mount and the server
reports the host-visible path instead. It affects reporting only; the write
still goes to `IMAGE_OUTPUT_DIR`.

### Building

```bash
make docker-build    # local, single-arch
make docker-buildx   # multi-arch build, no push
make docker-push     # multi-arch build + push (needs docker login)
```

---

## 🤖 MCP server integration

### Exposed tools

#### `generate_image`
Text → image. Saves locally and returns the saved path plus an interaction ID.

| Argument | Type | Notes |
| :--- | :--- | :--- |
| `prompt` | `str` | **Required.** Description of the image. |
| `aspect_ratio` | `str` | Default `1:1`. See the ratio list above. |
| `thinking_level` | `str` | Model-dependent; see the model table. |
| `image_size` | `str` | `1K`/`2K`/`4K`, model-dependent. Uppercase K required. |
| `mime_type` | `str` | `image/png` or `image/jpeg`. |
| `store` | `bool` | Default `True`. `False` means the result cannot be edited statefully later. |

#### `edit_image`
Stateful edit of a previous turn, preserving continuity.

| Argument | Type | Notes |
| :--- | :--- | :--- |
| `previous_interaction_id` | `str` | **Required.** Chain the **most recent** ID — a stale one silently forks from an older state. |
| `edit_prompt` | `str` | **Required.** Describe only the change. |
| `thinking_level` | `str` | Model-dependent. |
| `image_size` | `str` | Model-dependent. |

Takes no `aspect_ratio` — stateful edits inherit the previous turn's ratio.

#### `edit_local_image`
Uploads a local file inline as base64 and applies an edit. Same optional
arguments as `generate_image`, plus `image_path`. Inputs over
`MAX_INPUT_IMAGE_MB` are rejected before being read into memory.

#### `get_help`
Reports live configuration — auth mode, active model, output directory,
`HOST_OUTPUT_DIR` — and a full tool reference. Takes no arguments; call it first
when diagnosing setup.

### Output files

`<prefix>_<unix-ts>_<uuid8>.<ext>` (`gen_`, `edit_`, `edit_local_`) under
`IMAGE_OUTPUT_DIR`. The UUID suffix keeps concurrent writes collision-free. The
extension follows the returned mime type, defaulting to `.png`.

### Errors

Tools raise MCP `ToolError`, so failures arrive as protocol errors rather than
as text that looks like a successful result.

---

## 🧰 Development

```bash
make install   # pip install -r requirements.txt
make run       # python server.py
make test      # 54-test unittest suite, fully mocked (no network, no key)
make lint      # ruff check + ruff format --check
make validate  # claude plugin validate .
make clean
```

### Dependency pins

```
google-genai>=2.33.0,<3   # interaction.output_image needs >= 2.3.0
mcp>=2.3.0,<3
```

Dependabot watches pip, GitHub Actions, and the Docker base image weekly.

### Repository layout

| Path | Role |
| :--- | :--- |
| `server.py` | The MCP server — the single authoritative copy. |
| `test_agent.py` | Mocked unit tests. |
| `skills/gemini-image-editing/` | The skill (plugin layout). `SKILL.md` and `references/` at the repo root are symlinks to it. |
| `.claude/skills/gemini-image-editing` | Symlink, so the skill is active when working in this repo. |
| `.claude-plugin/` | Plugin + marketplace manifests. |
| `references/gemini-interactions-api.md` | Vendored Interactions API guide (provenance in `skills-lock.json`). |
| `GEMINI.md` | Full Interactions API developer guide. |

> [!NOTE]
> Earlier versions kept four synced copies of `server.py` plus a
> `refresh_skill.py` sync tool and several bootstrap scripts
> (`init.sh`, `set_env.sh`, `project-setup.sh`). All removed — the plugin
> manifest launches Docker, so the skill no longer bundles the server.

### CI

| Workflow | Trigger |
| :--- | :--- |
| `ci.yml` | Push/PR — lint, tests on Python 3.10–3.13, manifest validation. |
| `docker.yml` | PR builds amd64; a `v*` tag builds multi-arch and pushes to GHCR. |
| `model-drift.yml` | Weekly — flags a vanished default model. |

Secret-dependent jobs skip cleanly on a fork with no secrets, and never run on
fork PRs.

---

## 📚 Documentation

- [SKILL.md](SKILL.md) — the skill: workflow, tool catalog, best practices
- [GEMINI.md](GEMINI.md) — Interactions API developer guide
- [INSTALL-VERIFIED.md](INSTALL-VERIFIED.md) — step-by-step install walkthrough with recorded output, plus troubleshooting
- [MODERNIZATION.md](MODERNIZATION.md) — what changed in this fork and why
- [CLAUDE.md](CLAUDE.md) — guidance for Claude Code working in this repo
- [Interactions API reference](https://ai.google.dev/api/interactions-api) · [image generation](https://ai.google.dev/gemini-api/docs/interactions/image-generation)

## 📄 License

Apache-2.0 — see [LICENSE](LICENSE). Forked from
[xbill9/nb2lite-skill-claude](https://github.com/xbill9/nb2lite-skill-claude);
upstream copyright retained.
