---
name: nb2lite-image
description: Generate and edit images with Google's Gemini image models (Nano Banana 2.1, Nano Banana 2/Pro, NB2Lite) via the stateful Interactions API. Use when the user asks to generate, edit, refine, or iterate on images, do multi-turn image editing with interaction IDs, edit a local image file, tune aspect ratio, image size, or thinking level, or set up/debug the NB2Lite MCP agent. Triggers include "generate an image", "edit this image", "nano banana", "nb2lite", "flash-lite-image", "Interactions API", "interaction id", "aspect ratio", "thinking level".
---

# Gemini image generation & stateful editing

Generate and iteratively edit images with Google's Gemini image models through
the **Interactions API**, which persists each turn's visual context server-side
so edits preserve character, style, lighting, and pixel continuity instead of
re-rolling the scene.

Two ways to act:

1. **Preferred — MCP agent tools.** If the `nb2lite-agent` MCP server is
   connected, use its tools (catalog below). They wrap the API with per-model
   validation, mime-type detection, retries, and concurrency-safe saving.
2. **Fallback — direct SDK.** If it is not connected, either offer to install it
   (see "Installing the server") or call the API directly with the
   `google-genai` Python SDK using `references/gemini-interactions-api.md`.

## Standard workflow

1. **Config first.** Call `get_help`. It reports the auth mode, active model,
   output directory, and `HOST_OUTPUT_DIR`. If auth is missing, stop and tell the
   user — every other tool will fail.
2. **Generate.** `generate_image(prompt, …)` saves the image and returns the
   path plus an **interaction ID**.
3. **Iterate statefully.** `edit_image(previous_interaction_id, edit_prompt)`
   applies incremental changes. Each edit returns a **new** ID — always chain the
   most recent one, never the original.
4. **Edit local files.** `edit_local_image(image_path, edit_prompt)` for images
   that did not come from a prior interaction. It also returns an ID, so
   follow-up edits can chain from it.
5. **Deliver.** Report the saved path. Under Docker with `HOST_OUTPUT_DIR` set,
   the reported path is already the host-visible one — use it as given.

## Tool catalog

**Generation:** `generate_image(prompt, aspect_ratio="1:1", thinking_level, image_size, mime_type, store=True)`

**Editing:** `edit_image(previous_interaction_id, edit_prompt, thinking_level, image_size)` — stateful, no `aspect_ratio` (inherited).
`edit_local_image(image_path, edit_prompt, aspect_ratio, thinking_level, image_size, mime_type, store=True)` — inline base64 upload.

**Diagnostics:** `get_help()` — live configuration and full tool reference.

## Model capabilities

Set by `GEMINI_MODEL_NAME`; default `gemini-nano-banana-2.1`.

| Model | `image_size` | `thinking_level` |
| :--- | :--- | :--- |
| `gemini-nano-banana-2.1` *(default)* | 1K, 2K, 4K | minimal, low, medium, high |
| `gemini-3.1-flash-image` | 1K, 2K | minimal, low, medium, high |
| `gemini-3-pro-image` | 1K, 2K, 4K | low, medium, high |
| `gemini-3.1-flash-lite-image` | 1K only | low, high |

**Aspect ratios** (all models): `1:1` (default), `3:2`, `2:3`, `3:4`, `4:3`,
`4:5`, `5:4`, `9:16`, `16:9`, `21:9`.

`image_size` is case-sensitive — `1k` is rejected, `1K` is correct. A model not
in this table is **not** validated locally; the request goes through and the
API's error is the answer.

## Best practices & cautions

- **Keep edit prompts incremental.** Describe only the change ("make the sky dark
  blue with lightning"), not the whole scene — the interaction context holds the
  rest.
- **Don't change aspect ratio mid-session.** Switching between stateful turns
  degrades pixel continuity. Pick it at generation time.
- **Chain the latest interaction ID.** A stale ID silently forks the session from
  an older state rather than erroring.
- **Match thinking level to the job.** Lower for fast drafts; higher for complex
  rendering, accurate text layout, or character composition. Valid values differ
  per model — see the table.
- **Tool failures raise MCP errors.** A failure surfaces as a protocol error, not
  as text — you will not silently get a fake success.
- **Generations are billable.** Batch related edits into one well-specified
  prompt when the user cares about cost, and prefer lower thinking levels for
  drafts.
- **`store=False` forfeits later editing.** The turn returns an image but its
  context is not persisted, so it cannot be used as `previous_interaction_id`.
- **Never commit secrets.** `.env` and `.mcp.json` may carry keys.

## Installing the server

Preferred — the plugin, which installs this skill *and* registers the server as a
Docker container:

```
/plugin marketplace add tkhduracell/nb2lite-skill-claude
/plugin install nb2lite-image@nb2lite-skill-claude
```

Requires Docker and `GEMINI_API_KEY` exported in the environment (the manifest
passes it by name, so the key never enters the manifest or git).

Fallback — server only, no skill, no Docker:

```bash
pip install -r requirements.txt
claude mcp add nb2lite-agent \
  --env GEMINI_API_KEY=<key> \
  --env GEMINI_MODEL_NAME=gemini-nano-banana-2.1 \
  --env IMAGE_OUTPUT_DIR=./images \
  -- python3 /path/to/server.py
```

Then restart Claude Code and approve the server; `/mcp` should list
`nb2lite-agent`.

### Configuration

`GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `GEMINI_MODEL_NAME`,
`IMAGE_OUTPUT_DIR`, `HOST_OUTPUT_DIR` (display-only host path for Docker
mounts), `GEMINI_TIMEOUT_SECONDS`, `GEMINI_RETRY_ATTEMPTS`,
`MAX_INPUT_IMAGE_MB`. ADC is available behind `GOOGLE_GENAI_USE_ENTERPRISE=1`
with `GOOGLE_CLOUD_PROJECT`, though image output on that path is unverified —
prefer the API key.

## Bundled files

- `references/gemini-interactions-api.md` — the Interactions API guide: stateful
  vs. stateless architecture, parameter cheat sheet, Python SDK examples, and
  multi-turn editing practice. Read it when working without the MCP tools,
  composing raw SDK calls, or answering API questions.
