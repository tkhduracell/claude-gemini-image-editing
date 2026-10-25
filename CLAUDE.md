# CLAUDE.md — Gemini Image Editing skill & gemini-image MCP server

Guidance for Claude Code when working in this repository.

## Project overview

This repository packages one Claude Code skill (`gemini-image-editing`) and one MCP
server (`gemini-image`, an `MCPServer` app in `server.py`) for Google's Gemini
image models, driven through the stateful Interactions API. Together they:

1. **Generate images** — text-to-image with configurable aspect ratio, image
   size, and thinking level.
2. **Edit iteratively** — multi-turn stateful edits chained by interaction ID
   (`edit_image`), and inline base64 edits of local files (`edit_local_image`).

The tool surface is exactly four tools: `generate_image`, `edit_image`,
`edit_local_image`, `get_help`. Do not add or rename tools without updating
`SKILL.md`, `README.md`, and the plugin manifest together.

Fork of [xbill9/nb2lite-skill-claude](https://github.com/xbill9/nb2lite-skill-claude),
Apache-2.0. Keep `LICENSE` in sync with the license in
`.claude-plugin/plugin.json`. [MODERNIZATION.md](MODERNIZATION.md) records what
changed in this fork and why.

## Repository layout

There is **one** copy of everything. Earlier versions kept four synced copies of
`server.py` plus a `refresh_skill.py` sync tool; that is gone. Edit the file in
place — there are no snapshots to refresh and no `make skill`.

| Path | Role |
| :--- | :--- |
| `server.py` | The MCP server. Authoritative and unique. |
| `test_agent.py` | Mocked unit tests — no network, no API key. |
| `requirements.txt` | Pinned deps. |
| `skills/gemini-image-editing/` | The skill, in the layout the plugin loader requires. |
| `SKILL.md`, `references/` | **Symlinks** into `skills/gemini-image-editing/`. |
| `.claude/skills/gemini-image-editing` | Symlink, so the skill is active in this repo. |
| `.claude-plugin/` | Plugin + marketplace manifests. |
| `GEMINI.md` | Interactions API developer guide. |
| `skills-lock.json` | Upstream provenance of the vendored reference doc. |

`SKILL.md` and `references/` at the repo root are symlinks — editing either path
edits the same file. The real files live under `skills/gemini-image-editing/` because
the plugin loader requires that layout.

**Generated, gitignored:** `/images/` (output), `.env`, `.mcp.json`. Never commit
these.

## Distribution

Primary path is the **plugin**, whose manifest launches the server as a **Docker
container**. The plugin ships the skill *and* the server; `claude mcp add` ships
only the server, which is why it is the fallback.

```
/plugin marketplace add tkhduracell/claude-gemini-image-editing
/plugin install gemini-image-editing@claude-gemini-image-editing
```

The manifest passes `-e GEMINI_API_KEY` **by name with no value** on purpose:
Docker inherits it from the environment, so the key never enters the manifest or
git. Do not "fix" this by adding a value.

Image: `ghcr.io/tkhduracell/gemini-image`. `VERSION` in the `Makefile` must match
`version` in `plugin.json` **and** the tag pinned in its `args` — CI enforces the
first two.

## Common commands

```bash
make install   # pip install -r requirements.txt
make run       # python server.py
make test      # mocked unittest suite
make lint      # ruff check + ruff format --check
make validate  # claude plugin validate .
make docker-build / docker-buildx / docker-push
```

No virtualenvs for repo tooling: use the system `python3`. If deps are missing,
warn with the `pip install -r requirements.txt` command rather than creating a
venv. (Scratch venvs outside the project tree are fine for verification.)

## Configuration

| Variable | Default | Notes |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | — | Required unless using ADC. |
| `GEMINI_MODEL_NAME` | `gemini-nano-banana-2.1` | See model table below. |
| `IMAGE_OUTPUT_DIR` | `.` | Where files are written. |
| `HOST_OUTPUT_DIR` | unset | **Display only** — see below. |
| `GEMINI_TIMEOUT_SECONDS` | `120` | |
| `GEMINI_RETRY_ATTEMPTS` | `3` | 429/5xx, exponential backoff. |
| `MAX_INPUT_IMAGE_MB` | `20` | Checked before reading the file. |
| `GOOGLE_GENAI_USE_ENTERPRISE` | unset | Opt in to ADC. |
| `GOOGLE_CLOUD_PROJECT` / `_LOCATION` | — / `global` | Required with the above. |

**`HOST_OUTPUT_DIR` exists because of Docker.** Inside the container a saved file
is at `/images/gen_….png`, which the host cannot open. When set, the server
reports the host-visible path. It never affects where the file is written.

**Never hardcode a real GCP project name** anywhere in this repo — docs, code,
comments, or help text. Use `your-project-id`.

## Technical standards

### Models and capabilities

`MODEL_CAPS` in `server.py` is the source of truth:

| Model | `image_size` | `thinking_level` |
| :--- | :--- | :--- |
| `gemini-nano-banana-2.1` *(default)* | 1K, 2K, 4K | minimal, medium, high |
| `gemini-3.1-flash-image` | 1K, 2K, 4K | minimal, high |
| `gemini-3-pro-image` | 1K, 2K, 4K | low, high |
| `gemini-3.1-flash-lite-image` | 1K only | minimal, high |

These are **measured against the live API**, not copied from the docs — the
published tables are wrong about thinking levels for every model above. No
level works everywhere: `low` is rejected by the 3.1 family *and* by
nano-banana-2.1, while `minimal` is rejected by `gemini-3-pro-image`. Omit
`thinking_level` and the server picks one the active model accepts.

Aspect ratios (all models): `1:1`, `3:2`, `2:3`, `3:4`, `4:3`, `4:5`, `5:4`,
`9:16`, `16:9`, `21:9`. `image_size` is case-sensitive — the API rejects `1k`.

**The pass-through rule is deliberate: a model NOT in `MODEL_CAPS` is not
validated locally.** The request goes to the API and its error is the answer. Do
not replace this with a closed allowlist — that is the bug this fork fixed. The
previous version hardcoded 5 aspect ratios and `{low, high}` and rejected valid
requests locally.

`gemini-nano-banana-2.1` and `gemini-3.1-flash-lite-image` are **short-term
availability** models; a weekly workflow flags a vanished default.

### Request — `client.interactions.create(...)`

| Parameter | Notes |
| :--- | :--- |
| `model` | From `GEMINI_MODEL_NAME`. |
| `input` | Text, or a list of `{"type":"image","data":<b64>,"mime_type":…}` and `{"type":"text","text":…}` parts. |
| `response_format` | `{"type":"image"}` plus optional `aspect_ratio`, `image_size`, `mime_type`. Omit `aspect_ratio` on stateful edits — it is inherited. |
| `generation_config` | `{"thinking_level": …}`, model-dependent. |
| `previous_interaction_id` | Chain the **latest** turn's ID; a stale ID silently forks from an older state. |
| `store` | Must be `True` for any turn you may edit later. |

`HttpOptions.timeout` is in **milliseconds** — the SDK divides by 1000
internally. `GEMINI_TIMEOUT_SECONDS` is converted on the way in. A seconds value
passed raw would mean a 120 ms timeout and break every call.

### Response

- `interaction.id` — every turn returns a **new** ID.
- `interaction.output_image` — may be absent (report the ID, do not crash); has
  `.mime_type` and `.data` (base64 `str` **or** raw bytes — handle both).
  Requires `google-genai >= 2.3.0`.
- Saved as `<prefix>_<unix-ts>_<uuid8>.<ext>` under `IMAGE_OUTPUT_DIR`. The
  extension defaults to **png** and is derived from the mime type;
  `mimetypes.guess_extension` is only trusted for `image/*`, since
  `application/octet-stream` maps to `.bin`.

### Errors

Tools raise MCP `ToolError`
(`from mcp.server.mcpserver.exceptions import ToolError`). Earlier versions
returned `🔴 …` strings, which an agent could read as success — do not
reintroduce that.

### MCP version

This targets **mcp 2.x**: `from mcp.server.mcpserver import MCPServer`. The
`mcp.server.fastmcp` package was removed in 2.0 (FastMCP → MCPServer), so the
pin `mcp>=2.3.0,<3` is load-bearing — 1.x will not import this server.

### Practices

- **Keep edit prompts incremental** — describe only the change; stored context
  holds the rest.
- **Don't change aspect ratio between stateful turns** — it degrades pixel
  continuity. Pick it at generation time.
- **Generations are billable.** Batch related edits; prefer lower thinking levels
  for drafts.
- **Never commit secrets.** `.env`, `.mcp.json` are gitignored; keep it that way.

## Coding standards

- Prefer the standard library; do not assume third-party libraries are
  installed, and do not add dependencies for convenience.
- Keep `ruff check` and `ruff format` clean. CI pins **ruff 0.17.2** — match it
  locally (`pip install 'ruff==0.17.2'`), since a newer ruff adds rules and an
  older one misses them, and either way local and CI disagree.
- Tests must not make network calls.

## Related documentation

- `SKILL.md` — the skill: workflow, tool catalog, best practices
- `README.md` — install paths, env vars, tool reference, Docker, CI
- `MODERNIZATION.md` — what changed in this fork and why
- `GEMINI.md`, `references/gemini-interactions-api.md` — Interactions API guide
- [Interactions API reference](https://ai.google.dev/api/interactions-api) ·
  [image generation](https://ai.google.dev/gemini-api/docs/interactions/image-generation)
