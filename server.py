import base64
import logging
import mimetypes
import os
import sys
import time
import uuid
from typing import Any, cast

from google import genai
from mcp.server.mcpserver import MCPServer

# ToolError lets a failing tool surface a real MCP protocol error instead of a
# string the calling agent would mistake for a successful result. mcp 2.x hosts
# it at mcp.server.mcpserver.exceptions (1.x called the server class FastMCP and
# put the exception under mcp.server.fastmcp); requirements.txt pins mcp>=2, so
# only the 2.x path is imported here.
from mcp.server.mcpserver.exceptions import ToolError

# Initialize the MCP server
mcp = MCPServer("Gemini Image Editing")

# Configure Logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("gemini-image")

# Global client reference, lazily initialized
client = None
MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-nano-banana-2.1")

# Aspect ratios accepted by the Interactions API image models.
ASPECT_RATIOS = {
    "1:1",
    "3:2",
    "2:3",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
}

# Image sizes are case-sensitive on the wire: the API rejects "1k"/"2k"/"4k".
IMAGE_SIZES = {"1K", "2K", "4K"}

# Output mime types supported by response_format.
MIME_TYPES = {"image/png", "image/jpeg"}

# Per-model capabilities. Allowed image sizes and thinking levels genuinely
# differ between models, so a single global allowlist is always wrong for some
# of them. A model absent from this table is NOT rejected locally - see
# _validate_inputs - because a stale allowlist blocking a valid new model is
# worse than letting the API return its own clear error.
# Capabilities measured directly against the live Interactions API on
# 2026-10-25, not taken from the docs - the published tables disagree with the
# API on thinking levels for every model here. Re-probe before trusting an
# edit to this table.
MODEL_CAPS: dict[str, dict[str, Any]] = {
    "gemini-3.1-flash-lite-image": {
        "sizes": {"1K"},
        # low/medium return HTTP 400 for this model.
        "thinking": {"minimal", "high"},
    },
    "gemini-3.1-flash-image": {
        "sizes": {"1K", "2K", "4K"},
        # low/medium return HTTP 400 for this model.
        "thinking": {"minimal", "high"},
    },
    "gemini-3-pro-image": {
        "sizes": {"1K", "2K", "4K"},
        # This model is the odd one out: it takes low/high but rejects
        # minimal/medium, the opposite of the 3.1 family.
        "thinking": {"low", "high"},
    },
    "gemini-nano-banana-2.1": {
        "sizes": {"1K", "2K", "4K"},
        # low returns HTTP 400 for this model, though minimal is accepted.
        "thinking": {"minimal", "medium", "high"},
    },
}

# Thinking level used when the caller does not pick one. There is no value that
# every model accepts - "low" is rejected by the whole 3.1 family and by
# nano-banana-2.1, while "minimal" is rejected by gemini-3-pro-image - so the
# default has to be per-model. "minimal"/"low" are the cheapest level each model
# takes; an unknown model sends no thinking_level at all and lets the API apply
# its own default.
DEFAULT_THINKING = {
    "gemini-3.1-flash-lite-image": "minimal",
    "gemini-3.1-flash-image": "minimal",
    "gemini-3-pro-image": "low",
    "gemini-nano-banana-2.1": "minimal",
}


def _resolve_thinking(
    thinking_level: str | None, model: str | None = None
) -> str | None:
    """Pick the thinking level to send: the caller's, else the model's default."""
    if thinking_level:
        return thinking_level
    return DEFAULT_THINKING.get(model or MODEL_NAME)


# Request timeout. HttpOptions.timeout is in MILLISECONDS (the SDK divides by
# 1000 internally), so the seconds-based env var is converted on use.
DEFAULT_TIMEOUT_SECONDS = 120

# Retry/backoff for transient failures, handled by the SDK's own
# HttpRetryOptions (exponential backoff with jitter) - no extra dependency.
RETRY_HTTP_STATUS_CODES = [429, 500, 502, 503, 504]
DEFAULT_RETRY_ATTEMPTS = 5

# Cap on inline base64 uploads, since _get_image_data reads the file into memory.
DEFAULT_MAX_INPUT_IMAGE_MB = 20


def _env_float(name: str, default: float) -> float:
    """Read a positive numeric env var, falling back to the default if unusable."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Non-positive %s=%r; using %s", name, raw, default)
        return default
    return value


def _timeout_seconds() -> float:
    return _env_float("GEMINI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)


def _max_input_image_bytes() -> int:
    return int(
        _env_float("MAX_INPUT_IMAGE_MB", DEFAULT_MAX_INPUT_IMAGE_MB) * 1024 * 1024
    )


def _use_enterprise() -> bool:
    return os.environ.get("GOOGLE_GENAI_USE_ENTERPRISE", "").lower() in (
        "1",
        "true",
        "yes",
    )


def _http_options() -> dict[str, Any]:
    """Build http_options with an explicit timeout and retry policy.

    Without an explicit timeout a hung API call would block the tool forever.
    """
    return {
        "timeout": int(_timeout_seconds() * 1000),  # milliseconds
        "retry_options": {
            "attempts": int(
                _env_float("GEMINI_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS)
            ),
            "http_status_codes": RETRY_HTTP_STATUS_CODES,
        },
    }


def _read_api_key() -> str | None:
    """Find the API key: env var first, then a key file.

    The file fallback exists because GUI-launched apps (the Claude desktop app
    among them) do not source ~/.zshrc or ~/.bash_profile, so a key exported
    there never reaches a server the app spawns. A file is readable either way,
    and bind-mounting it into the container keeps the key out of the process
    environment, the plugin manifest and git.
    """
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key.strip() or None

    path = os.environ.get("GEMINI_API_KEY_FILE")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError as e:
        # A configured-but-unreadable key file is a misconfiguration worth
        # surfacing, not something to silently fall through on.
        raise ValueError(f"GEMINI_API_KEY_FILE '{path}' could not be read: {e}") from e


def _get_client() -> genai.Client:
    """Helper to lazily initialize or retrieve the Gemini Client.

    Two auth paths:
    - Default: an API key from GEMINI_API_KEY (or the GOOGLE_API_KEY fallback).
    - Opt-in enterprise: Application Default Credentials, enabled with
      GOOGLE_GENAI_USE_ENTERPRISE=1 plus GOOGLE_CLOUD_PROJECT (and optionally
      GOOGLE_CLOUD_LOCATION, default "global").

    NOTE: the Interactions API enterprise path takes `enterprise=True`, not the
    `vertexai=True` flag used by the older generate_content surface. Image
    *output* over the enterprise path is NOT documented by Google and is
    unverified here; the API-key path is the supported default.
    """
    global client
    if client is None:
        if _use_enterprise():
            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            if not project:
                raise ValueError(
                    "GOOGLE_GENAI_USE_ENTERPRISE is set but GOOGLE_CLOUD_PROJECT is "
                    "missing. Set GOOGLE_CLOUD_PROJECT to your project id "
                    "(e.g. 'your-project-id')."
                )
            client = genai.Client(
                enterprise=True,
                project=project,
                location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
                http_options=cast(Any, _http_options()),
            )
        else:
            api_key = _read_api_key()
            if not api_key:
                raise ValueError(
                    "No Gemini API key. Set GEMINI_API_KEY (or GOOGLE_API_KEY), "
                    "or GEMINI_API_KEY_FILE pointing at a file containing the "
                    "key, or GOOGLE_GENAI_USE_ENTERPRISE=1 with ADC. "
                    "Note: GUI apps do not read ~/.zshrc, so an env var "
                    "exported there will not reach this server - use the key "
                    "file instead."
                )
            client = genai.Client(
                api_key=api_key, http_options=cast(Any, _http_options())
            )
    return client


def _validate_inputs(
    aspect_ratio: str | None = None,
    thinking_level: str | None = None,
    image_size: str | None = None,
    mime_type: str | None = None,
    model: str | None = None,
) -> None:
    """Validate parameters against the capabilities of the model in use.

    Only validated when the model is known (present in MODEL_CAPS). For an
    unknown model - a future release - the request passes through untouched and
    the API is the authority on what it accepts.
    """
    model_name = model or MODEL_NAME
    caps = MODEL_CAPS.get(model_name)

    if aspect_ratio and caps is not None and aspect_ratio not in ASPECT_RATIOS:
        raise ValueError(
            f"Unsupported aspect ratio '{aspect_ratio}'. "
            f"Supported ratios: {', '.join(sorted(ASPECT_RATIOS))}"
        )

    if mime_type and mime_type.lower() not in MIME_TYPES:
        raise ValueError(
            f"Unsupported mime type '{mime_type}'. "
            f"Supported: {', '.join(sorted(MIME_TYPES))}"
        )

    if image_size:
        # Uppercase "K" is required by the API; reject rather than silently fix,
        # so the caller learns the correct form. A lowercase spelling of a known
        # size is always wrong, even on an unknown model, so that check is not
        # gated on caps - but an unrecognised size (e.g. a future "8K") is left
        # to the API when the model is unknown.
        if image_size.upper() in IMAGE_SIZES and image_size not in IMAGE_SIZES:
            raise ValueError(
                f"Unsupported image size '{image_size}'. "
                f"Supported sizes: {', '.join(sorted(IMAGE_SIZES))} "
                "(uppercase 'K' is required)."
            )
        if caps is not None and image_size not in IMAGE_SIZES:
            raise ValueError(
                f"Unsupported image size '{image_size}'. "
                f"Supported sizes: {', '.join(sorted(IMAGE_SIZES))} "
                "(uppercase 'K' is required)."
            )
        if caps is not None and image_size not in caps["sizes"]:
            raise ValueError(
                f"Model '{model_name}' does not support image size "
                f"'{image_size}'. Supported: {', '.join(sorted(caps['sizes']))}"
            )

    if (
        thinking_level
        and caps is not None
        and thinking_level.lower() not in caps["thinking"]
    ):
        raise ValueError(
            f"Model '{model_name}' does not support thinking level "
            f"'{thinking_level}'. Supported levels: "
            f"{', '.join(sorted(caps['thinking']))}"
        )


def _build_response_format(
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Assemble the response_format payload from the optional output controls."""
    response_format: dict[str, Any] = {"type": "image"}
    if aspect_ratio:
        response_format["aspect_ratio"] = aspect_ratio
    if image_size:
        response_format["image_size"] = image_size
    if mime_type:
        response_format["mime_type"] = mime_type.lower()
    return response_format


def _get_image_data(image_path: str) -> dict:
    """Helper to convert local image file to base64 input dict.

    Uses robust mime-type detection and refuses oversized files up front, since
    the whole file is base64-encoded in memory.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found at path: {image_path}")

    max_bytes = _max_input_image_bytes()
    size = os.path.getsize(image_path)
    if size > max_bytes:
        raise ValueError(
            f"Input image is too large: {size / (1024 * 1024):.1f} MB exceeds the "
            f"{max_bytes / (1024 * 1024):.0f} MB limit. Resize it, or raise "
            "MAX_INPUT_IMAGE_MB."
        )

    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type or not mime_type.startswith("image/"):
        # Fallback guess for common extensions
        if image_path.lower().endswith((".jpg", ".jpeg")):
            mime_type = "image/jpeg"
        elif image_path.lower().endswith(".webp"):
            mime_type = "image/webp"
        else:
            mime_type = "image/png"

    with open(image_path, "rb") as f:
        data_b64 = base64.b64encode(f.read()).decode("utf-8")

    return {"type": "image", "data": data_b64, "mime_type": mime_type}


def _extension_for(mime_type: str) -> str:
    """Pick a file extension for an output image mime type.

    The API returns PNG by default, and mime_type may be missing from the
    response entirely - so PNG is the fallback, not JPEG.
    """
    base = mime_type.split(";")[0].strip().lower() if mime_type else ""
    # Only trust the guess for an actual image type; a non-image mime (or one
    # mimetypes maps to something like ".bin") should not name the file.
    if base.startswith("image/"):
        guessed = mimetypes.guess_extension(base)
        if guessed:
            # Normalise the ".jpe"/".jpeg" spellings mimetypes may return.
            ext = guessed.lstrip(".").lower()
            if ext in ("jpe", "jpeg"):
                return "jpg"
            return ext
    return "png"


def _display_path(full_output_path: str) -> str:
    """Return the path to report to the caller.

    Inside a container the real write path (e.g. /images/gen_1.png) is not
    openable by the host user or by Claude. HOST_OUTPUT_DIR is a display-only
    override: the file still gets written to IMAGE_OUTPUT_DIR, but the reported
    path is rewritten to sit under HOST_OUTPUT_DIR.
    """
    real_path = os.path.abspath(full_output_path)
    host_dir = os.environ.get("HOST_OUTPUT_DIR")
    if not host_dir:
        return real_path

    output_dir = os.path.abspath(os.environ.get("IMAGE_OUTPUT_DIR", "."))
    try:
        relative = os.path.relpath(real_path, output_dir)
    except ValueError:
        # Different drives on Windows - nothing sensible to rewrite.
        return real_path
    if relative.startswith(os.pardir):
        # The file is not under IMAGE_OUTPUT_DIR; don't invent a host path.
        return real_path
    return os.path.normpath(os.path.join(host_dir, relative))


def _handle_response(interaction: Any, output_prefix: str) -> str:
    """Helper to decode base64 image data and save the resulting image."""
    image_output = getattr(interaction, "output_image", None)
    if not image_output:
        return (
            f"🟢 Interaction completed successfully.\n"
            f"• Interaction ID: {interaction.id}\n"
            f"• Note: No direct image output was found in the response."
        )

    ext = _extension_for(getattr(image_output, "mime_type", "") or "")

    # Use UUID to ensure unique filenames under high concurrency
    unique_id = uuid.uuid4().hex[:8]
    output_filename = f"{output_prefix}_{int(time.time())}_{unique_id}.{ext}"

    # Allow setting a custom output directory from environment
    output_dir = os.environ.get("IMAGE_OUTPUT_DIR", ".")
    if output_dir != ".":
        os.makedirs(output_dir, exist_ok=True)

    full_output_path = os.path.join(output_dir, output_filename)

    data = getattr(image_output, "data", None)
    if not data:
        raise ValueError("No image data found in output_image.")

    if isinstance(data, str):
        image_bytes = base64.b64decode(data)
    else:
        image_bytes = data

    with open(full_output_path, "wb") as f:
        f.write(image_bytes)

    return (
        f"🟢 Image successfully saved!\n"
        f"• Saved to: {_display_path(full_output_path)}\n"
        f"• Interaction ID: {interaction.id}"
    )


@mcp.tool()
def generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    thinking_level: str | None = None,
    image_size: str | None = None,
    mime_type: str | None = None,
    store: bool = True,
) -> str:
    """Generates a new image from a text prompt.

    - prompt: The text description of the image.
    - aspect_ratio: Output ratio ('1:1', '3:2', '2:3', '3:4', '4:3', '4:5',
      '5:4', '9:16', '16:9', '21:9').
    - thinking_level: Thought-token budget ('minimal', 'low', 'medium', 'high').
      Which values are accepted depends on the model.
    - image_size: Output resolution ('1K', '2K', '4K' - uppercase 'K' required).
      Model-dependent; omit to use the model default.
    - mime_type: Output encoding ('image/png' or 'image/jpeg').
    - store: Persist this turn's context on Google's servers (default True).
      Pass False to avoid persisting it; the result then CANNOT be edited
      statefully later via edit_image, since there is no stored context to chain
      from.
    """
    try:
        _validate_inputs(
            aspect_ratio=aspect_ratio,
            thinking_level=thinking_level,
            image_size=image_size,
            mime_type=mime_type,
        )

        response_format = _build_response_format(
            aspect_ratio=aspect_ratio, image_size=image_size, mime_type=mime_type
        )

        generation_config: dict[str, Any] = {}
        resolved_thinking = _resolve_thinking(thinking_level)
        if resolved_thinking:
            generation_config["thinking_level"] = resolved_thinking.lower()

        ai_client = _get_client()
        interaction = ai_client.interactions.create(
            model=MODEL_NAME,
            input=prompt,
            response_format=response_format,
            generation_config=generation_config,
            store=store,
        )

        return _handle_response(interaction, "gen")
    except Exception as e:
        logger.exception("Image generation failed")
        raise ToolError(f"Image generation failed: {e}") from e


@mcp.tool()
def edit_image(
    previous_interaction_id: str,
    edit_prompt: str,
    thinking_level: str | None = None,
    image_size: str | None = None,
) -> str:
    """Edits a previously generated image using its interaction ID.

    The model maintains contextual elements while applying your edit.
    - previous_interaction_id: The ID from the previous turn.
    - edit_prompt: Natural language description of what to change in the image.
    - thinking_level: Thought-token budget ('minimal', 'low', 'medium', 'high').
      Which values are accepted depends on the model.
    - image_size: Output resolution ('1K', '2K', '4K' - uppercase 'K' required).
      Model-dependent; omit to inherit the previous turn's size.

    Aspect ratio is deliberately not exposed here: it is inherited from the
    previous turn, and changing it mid-chain degrades pixel continuity. This
    tool always stores the new turn, so the chain can be extended again.
    """
    try:
        _validate_inputs(thinking_level=thinking_level, image_size=image_size)

        response_format = _build_response_format(image_size=image_size)

        generation_config: dict[str, Any] = {}
        resolved_thinking = _resolve_thinking(thinking_level)
        if resolved_thinking:
            generation_config["thinking_level"] = resolved_thinking.lower()

        ai_client = _get_client()
        interaction = ai_client.interactions.create(
            model=MODEL_NAME,
            previous_interaction_id=previous_interaction_id,
            input=edit_prompt,
            response_format=response_format,
            generation_config=generation_config,
            # Stateful editing needs the chain, so this stays True.
            store=True,
        )

        return _handle_response(interaction, "edit")
    except Exception as e:
        logger.exception("Editing failed")
        raise ToolError(f"Editing failed: {e}") from e


@mcp.tool()
def edit_local_image(
    image_path: str,
    edit_prompt: str,
    aspect_ratio: str = "1:1",
    thinking_level: str | None = None,
    image_size: str | None = None,
    mime_type: str | None = None,
    store: bool = True,
) -> str:
    """Edits a local image using a text prompt description.

    - image_path: Path to the local image file. Files larger than
      MAX_INPUT_IMAGE_MB (default 20) are rejected before upload.
    - edit_prompt: Natural language description of how to edit the image.
    - aspect_ratio: Output ratio ('1:1', '3:2', '2:3', '3:4', '4:3', '4:5',
      '5:4', '9:16', '16:9', '21:9').
    - thinking_level: Thought-token budget ('minimal', 'low', 'medium', 'high').
      Which values are accepted depends on the model.
    - image_size: Output resolution ('1K', '2K', '4K' - uppercase 'K' required).
      Model-dependent; omit to use the model default.
    - mime_type: Output encoding ('image/png' or 'image/jpeg').
    - store: Persist this turn's context on Google's servers (default True).
      Pass False to avoid persisting it; the result then CANNOT be edited
      statefully later via edit_image, since there is no stored context to chain
      from.
    """
    try:
        _validate_inputs(
            aspect_ratio=aspect_ratio,
            thinking_level=thinking_level,
            image_size=image_size,
            mime_type=mime_type,
        )
        img_data = _get_image_data(image_path)

        response_format = _build_response_format(
            aspect_ratio=aspect_ratio, image_size=image_size, mime_type=mime_type
        )

        generation_config: dict[str, Any] = {}
        resolved_thinking = _resolve_thinking(thinking_level)
        if resolved_thinking:
            generation_config["thinking_level"] = resolved_thinking.lower()

        ai_client = _get_client()
        interaction = ai_client.interactions.create(
            model=MODEL_NAME,
            input=[img_data, {"type": "text", "text": edit_prompt}],
            response_format=response_format,
            generation_config=generation_config,
            store=store,
        )

        return _handle_response(interaction, "edit_local")
    except Exception as e:
        logger.exception("Local image edit failed")
        raise ToolError(f"Local image edit failed: {e}") from e


@mcp.tool()
def get_help() -> str:
    """Provides help text and summarizes the configuration options and all available image generation/editing tools for this MCP server."""
    enterprise = _use_enterprise()
    if enterprise:
        auth_status = (
            f"Enterprise / ADC (project: "
            f"`{os.environ.get('GOOGLE_CLOUD_PROJECT') or 'NOT SET'}`, "
            f"location: `{os.environ.get('GOOGLE_CLOUD_LOCATION', 'global')}`)"
        )
    else:
        if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            auth_status = "API key: `Set` (from the environment)"
        elif os.environ.get("GEMINI_API_KEY_FILE"):
            try:
                found = bool(_read_api_key())
            except ValueError as e:
                auth_status = f"API key: `Not Set` - {e}"
            else:
                auth_status = (
                    "API key: `Set` (from `GEMINI_API_KEY_FILE`)"
                    if found
                    else "API key: `Not Set` - `GEMINI_API_KEY_FILE` is empty"
                )
        else:
            auth_status = (
                "API key: `Not Set` - set `GEMINI_API_KEY`, or "
                "`GEMINI_API_KEY_FILE` pointing at a file holding the key. "
                "A GUI-launched app does not read `~/.zshrc`, so a key "
                "exported there never reaches this server."
            )
    image_output_dir = os.environ.get("IMAGE_OUTPUT_DIR", ".")
    host_output_dir = os.environ.get("HOST_OUTPUT_DIR")
    caps = MODEL_CAPS.get(MODEL_NAME)
    if caps:
        model_note = (
            f"sizes: {', '.join(sorted(caps['sizes']))}; "
            f"thinking levels: {', '.join(sorted(caps['thinking']))}"
        )
    else:
        model_note = (
            "not in the local capability table - parameters are passed through "
            "and validated by the API"
        )

    return (
        "### 🌌 Gemini Image Editing Help & Configuration\n\n"
        "This MCP server interfaces with the stateful **Interactions API** using the Google GenAI SDK to generate and edit images.\n\n"
        "#### ⚙️ Configuration Options (Environment Variables)\n"
        "- **`GEMINI_API_KEY` / `GOOGLE_API_KEY`**: API keys used to authenticate with Gemini.\n"
        f"  - *Current Auth:* {auth_status}\n"
        "- **`GEMINI_API_KEY_FILE`**: Path to a file containing the key, used when the env vars are unset. Preferred when the server is launched by a GUI app (which does not source shell rc files) - bind-mount the file into the container.\n"
        f"  - *Current Value:* `{os.environ.get('GEMINI_API_KEY_FILE') or 'Not Set'}`\n"
        "- **`GOOGLE_GENAI_USE_ENTERPRISE`**: Set to `1`/`true`/`yes` to authenticate with Application Default Credentials instead of an API key. Requires **`GOOGLE_CLOUD_PROJECT`** (e.g. `your-project-id`); **`GOOGLE_CLOUD_LOCATION`** defaults to `global`.\n"
        f"  - *Current Value:* `{'enabled' if enterprise else 'disabled'}`\n"
        "  - *Caveat:* image output over the enterprise path is **not documented by Google and unverified**. The API-key path is the supported default.\n"
        "- **`GEMINI_MODEL_NAME`**: The name of the interactions model to use.\n"
        f"  - *Current Value:* `{MODEL_NAME}` ({model_note})\n"
        "- **`IMAGE_OUTPUT_DIR`**: The directory where generated and edited images are written.\n"
        f"  - *Current Value:* `{os.path.abspath(image_output_dir)}`\n"
        "- **`HOST_OUTPUT_DIR`**: Display-only path translation. When set, saved files are *reported* under this directory instead of `IMAGE_OUTPUT_DIR` — useful in Docker, where the container path (e.g. `/images/...`) is not openable on the host. The actual write still goes to `IMAGE_OUTPUT_DIR`.\n"
        f"  - *Current Value:* `{host_output_dir or 'Not Set'}`\n"
        "- **`GEMINI_TIMEOUT_SECONDS`**: Per-request timeout.\n"
        f"  - *Current Value:* `{_timeout_seconds():g}`\n"
        "- **`GEMINI_RETRY_ATTEMPTS`**: Retry attempts with exponential backoff on HTTP 429 and 5xx.\n"
        f"  - *Current Value:* `{int(_env_float('GEMINI_RETRY_ATTEMPTS', DEFAULT_RETRY_ATTEMPTS))}`\n"
        "- **`MAX_INPUT_IMAGE_MB`**: Size cap for local images sent to `edit_local_image`.\n"
        f"  - *Current Value:* `{_env_float('MAX_INPUT_IMAGE_MB', DEFAULT_MAX_INPUT_IMAGE_MB):g}`\n\n"
        "#### 🎛️ Supported Parameter Values\n"
        f"- **Aspect ratios:** `{'`, `'.join(sorted(ASPECT_RATIOS))}`\n"
        f"- **Image sizes:** `{'`, `'.join(sorted(IMAGE_SIZES))}` (uppercase `K` is required — lowercase is rejected by the API)\n"
        f"- **Output mime types:** `{'`, `'.join(sorted(MIME_TYPES))}`\n"
        "- **Thinking levels:** `minimal`, `low`, `medium`, `high` — *which* values a model accepts varies, and defaults differ (`medium` for nano-banana-2.1, `minimal` for the 3.1 models).\n"
        "- Per-model limits are enforced locally only for **known** models. An unrecognised model (e.g. a newer release) is passed through to the API, which is the authority on what it accepts.\n\n"
        "#### 🧰 Available MCP Tools\n\n"
        "Below is a summary of the tools exposed by this agent:\n\n"
        "##### 🎨 Image Generation & Editing\n"
        "- **`generate_image`**: Generates a new image from a text prompt and saves it locally.\n"
        "  - *Arguments*:\n"
        "    - `prompt` (str, required): Natural language description of the image.\n"
        "    - `aspect_ratio` (str, optional): Output ratio (default: `'1:1'`).\n"
        "    - `thinking_level` (str, optional): Latency vs. quality steps. Omit it and the server picks a level the active model accepts.\n"
        "    - `image_size` (str, optional): `1K`/`2K`/`4K`, model-dependent.\n"
        "    - `mime_type` (str, optional): `image/png` or `image/jpeg`.\n"
        "    - `store` (bool, optional): Persist context for later stateful edits (default: `True`).\n"
        "- **`edit_image`**: Edits a previously generated image using its interaction ID, maintaining continuity.\n"
        "  - *Arguments*:\n"
        "    - `previous_interaction_id` (str, required): The unique interaction ID returned from the previous turn.\n"
        "    - `edit_prompt` (str, required): Natural language description of changes/additions to apply.\n"
        "    - `thinking_level` (str, optional): Latency vs. quality steps. Omit it and the server picks a level the active model accepts.\n"
        "    - `image_size` (str, optional): `1K`/`2K`/`4K`, model-dependent.\n"
        "- **`edit_local_image`**: Uploads a local image file in-line via Base64 and describes edits to apply.\n"
        "  - *Arguments*:\n"
        "    - `image_path` (str, required): Path to the local image file.\n"
        "    - `edit_prompt` (str, required): Natural language description of modifications to apply.\n"
        "    - `aspect_ratio` (str, optional): Output ratio (default: `'1:1'`).\n"
        "    - `thinking_level` (str, optional): Latency vs. quality steps. Omit it and the server picks a level the active model accepts.\n"
        "    - `image_size` (str, optional): `1K`/`2K`/`4K`, model-dependent.\n"
        "    - `mime_type` (str, optional): `image/png` or `image/jpeg`.\n"
        "    - `store` (bool, optional): Persist context for later stateful edits (default: `True`).\n"
        "- **`get_help`**: Provides this help text and summarizes the server configuration and available tools.\n\n"
        "#### 💾 File Output & Stateful Session Management\n"
        "- All successful generation and edit requests save the output image locally under `IMAGE_OUTPUT_DIR` using a concurrent-safe naming format: `<prefix>_<timestamp>_<uuid_hex>.<extension>` (e.g. `gen_1780123456_a3b2c1d0.png`). The extension is derived from the response mime type, defaulting to `.png`.\n"
        "- Calls set `store=True` by default, generating a persistent `interaction_id` session context on Google's servers. Pass this ID as `previous_interaction_id` in subsequent `edit_image` calls to perform multi-turn modifications with high character, style, and pixel continuity. Passing `store=False` avoids that server-side persistence, but the resulting image cannot then be edited statefully.\n"
        "- Failures surface as **MCP tool errors** (protocol-level), not as success strings — so a calling agent cannot mistake a failure for a result."
    )


if __name__ == "__main__":
    # stdio is MCPServer.run()'s default, but it is stated explicitly because
    # the Dockerfile ENTRYPOINT runs this file directly and Claude Code speaks
    # stdio to it - a silent default change would break the server.
    mcp.run(transport="stdio")
