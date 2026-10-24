# 🌌 Gemini Interactions API Reference

The **Interactions API** is the next-generation stateful endpoint for Gemini's multi-turn, high-efficiency models. Unlike stateless generations, interactions allow you to store and iteratively refine context—enabling stateful image editing with `gemini-3.1-flash-lite-image`.

---

## 🏗️ Stateful vs. Stateless Architecture

Traditional image generation models are completely stateless: every change requires submitting a brand new text prompt and starting from scratch, resulting in a loss of pixel consistency, character continuity, and structural alignment.

With the **Interactions API**:
1. **The Core Generation**: Generates an initial high-fidelity image and returns a unique `interaction_id` representing that session's state on Google's servers.
2. **Context Persistence**: When editing, you pass the `previous_interaction_id`. The model retrieves the precise context and latent state from your previous turn.
3. **Refinement**: It applies your natural language edit directly onto the existing canvas, maintaining exceptional continuity (e.g., matching the same character, clothing, background structure, lighting, and art style).

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer / Agent
    participant API as Gemini Interactions API
    participant Store as Session Context Store

    Dev->>API: Generate Image (prompt, store=True)
    API->>Store: Persist visual context
    API-->>Dev: Return Image + interaction_id (e.g., int_123)

    Dev->>API: Edit Image (previous_interaction_id=int_123, edit_prompt, store=True)
    API->>Store: Retrieve context for int_123
    API->>Store: Persist updated visual context
    API-->>Dev: Return Edited Image + new interaction_id (e.g., int_456)
```

---

## 🎨 Gemini 3.1 Flash Lite Image (`gemini-3.1-flash-lite-image`) Cheat Sheet

Gemini 3.1 Flash Lite Image (`gemini-3.1-flash-lite-image`) is a high-efficiency image generation model designed for speed, scale, and high-fidelity editing.

### 🔑 Essential Parameters

| Parameter | Type | Description |
| :--- | :--- | :--- |
| **`model`** | `str` | Any supported image model — see the model table below. |
| **`input`** | `str` \| `list` | Text description or a list containing base64 images and text prompts. |
| **`response_format`** | `dict` | e.g. `{"type": "image"}`. Supports `"aspect_ratio"`, `"image_size"` (`1K`/`2K`/`4K`, uppercase K required), and `"mime_type"` (`image/png`, `image/jpeg`). |
| **`generation_config`**| `dict` | Configuration for `thinking_level`. Valid values are `'minimal'`, `'low'`, `'medium'`, `'high'`, but **which are accepted varies by model** — see the table below. |
| **`previous_interaction_id`** | `str` | Performs stateful image editing/refinement on a prior turn's image. |
| **`store`** | `bool` | Set to `True` to allow subsequent stateful edits on the image. |

### 🐍 Python SDK Example

```python
import base64
from google import genai

client = genai.Client()

# 1. Generate an initial image
interaction = client.interactions.create(
    model="gemini-3.1-flash-lite-image",
    input="An astronaut cooking instant ramen in zero gravity, retro anime style",
    response_format={"type": "image", "aspect_ratio": "16:9"},
    generation_config={"thinking_level": "high"},
    store=True
)

# 2. Stateful Edit: Modify the existing image
edited_interaction = client.interactions.create(
    model="gemini-3.1-flash-lite-image",
    previous_interaction_id=interaction.id,
    input="Make the spacesuit neon green and add floating ramen chopsticks",
    response_format={"type": "image"},
    store=True
)

# 3. Local Image Edit (In-line Base64 Upload)
with open("my_sketch.png", "rb") as f:
    b64_data = base64.b64encode(f.read()).decode("utf-8")

local_edit = client.interactions.create(
    model="gemini-3.1-flash-lite-image",
    input=[
        {"type": "image", "data": b64_data, "mime_type": "image/png"},
        {"type": "text", "text": "Turn this rough drawing into a high-fidelity 3D model render"}
    ],
    response_format={"type": "image", "aspect_ratio": "4:3"},
    store=True
)
```

---

## 🛠️ Mapping API Concepts to the MCP Server

The tools in this repository wrap the raw Python SDK calls with robustness features designed for direct tool-use by LLM agents:

### 1. Model Selection
Our server abstracts model naming using the `GEMINI_MODEL_NAME` environment variable (defaulting to `"gemini-nano-banana-2.1"`). This allows easy hot-swapping or testing newer model revisions without code changes.

| Model | `image_size` | `thinking_level` |
| :--- | :--- | :--- |
| `gemini-nano-banana-2.1` *(default)* | 1K, 2K, 4K | minimal, medium, high |
| `gemini-3.1-flash-image` | 1K, 2K, 4K | minimal, high |
| `gemini-3-pro-image` | 1K, 2K, 4K | low, high |
| `gemini-3.1-flash-lite-image` | 1K only | minimal, high |

Measured against the live API, not copied from the published docs, which are
wrong about thinking levels for every model listed. No level is universal:
`low` is rejected by the 3.1 family and by nano-banana-2.1, while `minimal` is
rejected by `gemini-3-pro-image`.

Aspect ratios (all models): `1:1`, `3:2`, `2:3`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9`.

A model **not** in this table is not validated locally — the request is passed through and the API's response is authoritative.

### 2. File Persistence & Security
All response images are received as base64-encoded or raw byte data. The server automatically detects the returned mime-type (PNG, JPEG, WebP) and handles the physical file write to a directory specified by the developer via `IMAGE_OUTPUT_DIR`.

### 3. Concurrency Protection
To prevent file name collisions when multiple agent instances or parallel calls generate images simultaneously, the server appends a high-entropy 8-character hex UUID to every output file:
```
gen_1780123456_a3b2c1d0.png
```

### 4. Universal Diagnostics
The server exposes a `get_help` tool that reports its live configuration — API key status, the active model, and the absolute output directory — plus a full reference of every exposed tool. Agents should call it first when a request fails or the setup is in doubt. Tool failures are returned as `🔴 ...` text strings rather than protocol errors, so callers must check for them before reporting success.

---

## 💡 Best Practices for Stateful Editing

* **Specify `store=True`**: Always set `store=True` in your API calls, or use the MCP tools (which have it enabled by default) if you plan to do multi-turn editing.
* **Keep Edit Prompts Incremental**: Instead of repeating the entire original description in your edit prompt, focus only on the incremental changes (e.g. use `"make the sky dark blue with lightning"` rather than repeating `"a landscape under a dark blue sky with lightning"`).
* **Align Aspect Ratios**: During stateful refinement turns, avoid changing the aspect ratio, as changing ratios between turns can degrade pixel continuity.
* **Optimize Thinking Budgets**:
  * Use lower levels for ultra-fast previews and prototyping.
  * Use `'high'` when you need complex rendering, text layout accuracy, or advanced character composition.
  * Which levels a model accepts varies, and no level works everywhere — see the table above. Omitting `thinking_level` lets the server pick one the active model accepts.

---

## 🔗 Useful Links
- [Interactions API Reference Guide](https://ai.google.dev/api/interactions-api)
- [Raw Interactions API Details](https://ai.google.dev/api/interactions.md.txt)
- [Image generation guide](https://ai.google.dev/gemini-api/docs/interactions/image-generation) — `response_format`, sizes, aspect ratios
- [Project repository](https://github.com/tkhduracell/claude-gemini-image-editing) — the `gemini-image` MCP server ([server.py](server.py)), the `gemini-image-editing` Claude Code skill ([SKILL.md](SKILL.md)), and setup docs ([README.md](README.md))
