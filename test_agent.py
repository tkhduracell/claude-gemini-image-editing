import asyncio
import unittest
import os
import tempfile
from unittest.mock import MagicMock, patch

# Import functions to test from server
import server
from server import (
    ASPECT_RATIOS,
    DEFAULT_THINKING,
    IMAGE_SIZES,
    MODEL_CAPS,
    ToolError,
    _extension_for,
    _get_image_data,
    _handle_response,
    _resolve_thinking,
    _validate_inputs,
    edit_image,
    edit_local_image,
    generate_image,
    get_help,
    mcp,
)


def _mock_client(
    mime_type="image/png", data=b"dummy_png_bytes", interaction_id="int_1"
):
    """Build a mocked genai client returning one successful interaction."""
    mock_client = MagicMock()
    mock_interaction = MagicMock()
    mock_interaction.id = interaction_id
    mock_interaction.output_image = MagicMock()
    mock_interaction.output_image.mime_type = mime_type
    mock_interaction.output_image.data = data
    mock_client.interactions.create.return_value = mock_interaction
    return mock_client


class TestGeminiImageAgent(unittest.TestCase):
    def setUp(self):
        # Create a temporary file for image tests
        self.test_dir = tempfile.TemporaryDirectory()
        self.test_image_path = os.path.join(self.test_dir.name, "test.png")
        # Write dummy png bytes
        with open(self.test_image_path, "wb") as f:
            f.write(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )

    def tearDown(self):
        self.test_dir.cleanup()

    def test_get_image_data_valid(self):
        """Test image encoding helper with a valid image file."""
        data = _get_image_data(self.test_image_path)
        self.assertEqual(data["type"], "image")
        self.assertEqual(data["mime_type"], "image/png")
        self.assertTrue(len(data["data"]) > 0)

    def test_get_image_data_missing(self):
        """Test image encoding helper raises FileNotFoundError if file is missing."""
        with self.assertRaises(FileNotFoundError):
            _get_image_data("non_existent_file.png")

    @patch("server._get_client")
    def test_generate_image_success(self, mock_get_client):
        """Test generate_image tool with mocked Interactions API response.

        thinking_level='medium' is valid under the default model
        (gemini-nano-banana-2.1), so this must still succeed.
        """
        mock_get_client.return_value = _mock_client(interaction_id="int_123")

        with patch("server.open", unittest.mock.mock_open()):
            result = generate_image(
                prompt="test prompt",
                aspect_ratio="1:1",
                thinking_level="medium",
            )
            self.assertIn("🟢 Image successfully saved!", result)
            self.assertIn("int_123", result)

    @patch("server._get_client")
    def test_generate_image_no_output_image(self, mock_get_client):
        """Test generate_image tool when no output image is returned by the API."""
        mock_client = MagicMock()
        mock_interaction = MagicMock()
        mock_interaction.id = "int_123"
        mock_interaction.output_image = None
        mock_client.interactions.create.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        result = generate_image(
            prompt="test prompt", aspect_ratio="1:1", thinking_level="medium"
        )
        self.assertIn("🟢 Interaction completed successfully.", result)
        self.assertIn("No direct image output was found", result)

    @patch("server._get_client")
    def test_generate_image_failure_raises(self, mock_get_client):
        """API failures must raise a real MCP ToolError, not return a string."""
        mock_client = MagicMock()
        mock_client.interactions.create.side_effect = Exception("API Key expired")
        mock_get_client.return_value = mock_client

        with self.assertRaises(ToolError) as ctx:
            generate_image(prompt="test prompt")
        self.assertIn("API Key expired", str(ctx.exception))

    @patch("server._get_client")
    def test_edit_image_success(self, mock_get_client):
        """Test edit_image tool with mocked Interactions API response."""
        # Use a valid base64-encoded string
        mock_get_client.return_value = _mock_client(
            mime_type="image/jpeg",
            data="ZHVtbXlfanBlZ19iYXNlNjRfc3RyaW5n",
            interaction_id="int_456",
        )

        with patch("server.open", unittest.mock.mock_open()):
            result = edit_image(
                previous_interaction_id="int_123", edit_prompt="add stars"
            )
            self.assertIn("🟢 Image successfully saved!", result)
            self.assertIn("int_456", result)

    @patch("server._get_client")
    def test_edit_image_always_stores(self, mock_get_client):
        """edit_image must keep store=True so the edit chain stays usable."""
        mock_client = _mock_client()
        mock_get_client.return_value = mock_client

        with patch("server.open", unittest.mock.mock_open()):
            edit_image(previous_interaction_id="int_1", edit_prompt="add stars")

        self.assertIs(mock_client.interactions.create.call_args.kwargs["store"], True)

    @patch("server._get_client")
    def test_edit_image_failure_raises(self, mock_get_client):
        """edit_image surfaces failures as ToolError."""
        mock_client = MagicMock()
        mock_client.interactions.create.side_effect = Exception("bad chain")
        mock_get_client.return_value = mock_client

        with self.assertRaises(ToolError):
            edit_image(previous_interaction_id="int_1", edit_prompt="x")

    def test_mcp_tools_registered(self):
        """Verify that the expected tools are registered to the MCP server.

        MCPServer.list_tools() is the public mcp 2.x API and is a coroutine, so
        it is driven with asyncio.run rather than reaching into _tool_manager.
        Registration is local - no network call is made.
        """
        tools = [t.name for t in asyncio.run(mcp.list_tools())]
        self.assertIn("generate_image", tools)
        self.assertIn("edit_image", tools)
        self.assertIn("edit_local_image", tools)
        self.assertIn("get_help", tools)

    def test_get_help(self):
        """Verify that get_help returns correct info containing the available tools and variables."""
        result = get_help()
        self.assertIn("generate_image", result)
        self.assertIn("edit_image", result)
        self.assertIn("edit_local_image", result)
        self.assertIn("get_help", result)
        self.assertIn("GEMINI_MODEL_NAME", result)
        # New configuration surface must be documented.
        self.assertIn("HOST_OUTPUT_DIR", result)
        self.assertIn("GOOGLE_GENAI_USE_ENTERPRISE", result)
        self.assertIn("MAX_INPUT_IMAGE_MB", result)
        self.assertIn("GEMINI_TIMEOUT_SECONDS", result)

    def test_get_help_never_leaks_a_real_project_name(self):
        """The help text must only use a placeholder project id."""
        self.assertIn("your-project-id", get_help())

    @patch.dict(
        os.environ,
        {"GOOGLE_GENAI_USE_ENTERPRISE": "1", "GOOGLE_CLOUD_PROJECT": "your-project-id"},
    )
    def test_get_help_reports_enterprise_mode(self):
        """Enterprise mode status is reflected in get_help."""
        result = get_help()
        self.assertIn("enabled", result)
        self.assertIn("your-project-id", result)


class TestValidation(unittest.TestCase):
    """Per-model capability validation."""

    def test_21_9_aspect_ratio_is_now_valid(self):
        """21:9 is a supported ratio and must NOT be rejected."""
        self.assertIn("21:9", ASPECT_RATIOS)
        _validate_inputs(aspect_ratio="21:9")  # must not raise

    def test_all_documented_aspect_ratios_accepted(self):
        for ratio in [
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
        ]:
            with self.subTest(ratio=ratio):
                _validate_inputs(aspect_ratio=ratio)

    def test_validation_invalid_aspect_ratio(self):
        """A genuinely unsupported ratio is rejected."""
        with self.assertRaises(ValueError) as ctx:
            _validate_inputs(aspect_ratio="7:3")
        self.assertIn("7:3", str(ctx.exception))

    @patch("server._get_client")
    def test_validation_invalid_aspect_ratio_raises_toolerror(self, mock_get_client):
        """Through the tool surface, bad input becomes a ToolError."""
        mock_get_client.return_value = _mock_client()
        with self.assertRaises(ToolError) as ctx:
            generate_image(prompt="test", aspect_ratio="7:3")
        self.assertIn("7:3", str(ctx.exception))

    @patch("server._get_client")
    def test_validation_invalid_thinking_level(self, mock_get_client):
        """An unsupported thinking level raises ToolError (was a 🔴 string)."""
        mock_get_client.return_value = _mock_client()
        with self.assertRaises(ToolError) as ctx:
            generate_image(prompt="test", thinking_level="ultra")
        self.assertIn("ultra", str(ctx.exception))

    def test_thinking_level_is_per_model(self):
        """Accepted levels differ per model; values measured against the API.

        No level is universal: 'low' is rejected by the whole 3.1 family and by
        nano-banana-2.1, while 'minimal' is rejected by gemini-3-pro-image.
        """
        # nano-banana-2.1: minimal/medium/high, NOT low
        _validate_inputs(thinking_level="medium", model="gemini-nano-banana-2.1")
        _validate_inputs(thinking_level="minimal", model="gemini-nano-banana-2.1")
        with self.assertRaises(ValueError):
            _validate_inputs(thinking_level="low", model="gemini-nano-banana-2.1")

        # the 3.1 family: minimal/high only
        for model in ("gemini-3.1-flash-lite-image", "gemini-3.1-flash-image"):
            _validate_inputs(thinking_level="minimal", model=model)
            _validate_inputs(thinking_level="high", model=model)
            for bad in ("low", "medium"):
                with self.assertRaises(ValueError):
                    _validate_inputs(thinking_level=bad, model=model)

        # pro-image is the odd one out: low/high, rejecting minimal/medium
        _validate_inputs(thinking_level="low", model="gemini-3-pro-image")
        for bad in ("minimal", "medium"):
            with self.assertRaises(ValueError):
                _validate_inputs(thinking_level=bad, model="gemini-3-pro-image")

    def test_default_thinking_level_is_accepted_by_its_model(self):
        """Every model's default must be in its own accepted set.

        Regression guard: the tools used to default to a hardcoded 'low', which
        the default model rejects outright - so every call failed out of the box.
        """
        for model, default in DEFAULT_THINKING.items():
            self.assertIn(default, MODEL_CAPS[model]["thinking"], model)
            _validate_inputs(thinking_level=default, model=model)

    def test_resolve_thinking_falls_back_per_model(self):
        """None resolves to the model's default; an unknown model sends nothing."""
        self.assertEqual(_resolve_thinking(None, "gemini-3-pro-image"), "low")
        self.assertEqual(_resolve_thinking(None, "gemini-nano-banana-2.1"), "minimal")
        self.assertEqual(_resolve_thinking("high", "gemini-3-pro-image"), "high")
        self.assertIsNone(_resolve_thinking(None, "some-future-model"))

    def test_image_size_is_per_model(self):
        """flash-lite is 1K-only; every other model goes up to 4K.

        Sizes measured against the live API - flash-image does accept 4K,
        contrary to what the published docs list.
        """
        _validate_inputs(image_size="1K", model="gemini-3.1-flash-lite-image")
        for bad in ("2K", "4K"):
            with self.assertRaises(ValueError) as ctx:
                _validate_inputs(image_size=bad, model="gemini-3.1-flash-lite-image")
            self.assertIn(bad, str(ctx.exception))

        for model in (
            "gemini-3-pro-image",
            "gemini-3.1-flash-image",
            "gemini-nano-banana-2.1",
        ):
            for size in ("1K", "2K", "4K"):
                _validate_inputs(image_size=size, model=model)

    def test_image_size_requires_uppercase_k(self):
        """Lowercase 'k' is rejected by the API, so reject it locally too."""
        for bad in ["1k", "2k", "4k"]:
            with self.subTest(size=bad):
                with self.assertRaises(ValueError) as ctx:
                    _validate_inputs(image_size=bad, model="gemini-nano-banana-2.1")
                self.assertIn("uppercase", str(ctx.exception))
        self.assertEqual(IMAGE_SIZES, {"1K", "2K", "4K"})

    def test_unknown_model_passes_through(self):
        """A future model is never rejected locally - the API is the authority."""
        future = "gemini-9-future-image"
        self.assertNotIn(future, MODEL_CAPS)
        # None of these would be valid for any known model, yet all pass.
        _validate_inputs(thinking_level="ultra", model=future)
        _validate_inputs(aspect_ratio="7:3", model=future)
        _validate_inputs(image_size="8K", model=future)

    def test_known_model_still_validated(self):
        """Known models keep their local guardrails."""
        with self.assertRaises(ValueError):
            _validate_inputs(thinking_level="ultra", model="gemini-nano-banana-2.1")

    def test_invalid_mime_type_rejected(self):
        with self.assertRaises(ValueError):
            _validate_inputs(mime_type="image/gif")
        _validate_inputs(mime_type="image/png")
        _validate_inputs(mime_type="image/jpeg")

    @patch("server._get_client")
    def test_image_size_and_mime_type_threaded_into_response_format(
        self, mock_get_client
    ):
        """New params must reach response_format."""
        mock_client = _mock_client()
        mock_get_client.return_value = mock_client

        with patch("server.open", unittest.mock.mock_open()):
            generate_image(
                prompt="p",
                aspect_ratio="16:9",
                thinking_level="medium",
                image_size="2K",
                mime_type="image/jpeg",
            )

        rf = mock_client.interactions.create.call_args.kwargs["response_format"]
        self.assertEqual(rf["type"], "image")
        self.assertEqual(rf["aspect_ratio"], "16:9")
        self.assertEqual(rf["image_size"], "2K")
        self.assertEqual(rf["mime_type"], "image/jpeg")


class TestStoreParameter(unittest.TestCase):
    @patch("server._get_client")
    def test_store_defaults_true(self, mock_get_client):
        mock_client = _mock_client()
        mock_get_client.return_value = mock_client
        with patch("server.open", unittest.mock.mock_open()):
            generate_image(prompt="p")
        self.assertIs(mock_client.interactions.create.call_args.kwargs["store"], True)

    @patch("server._get_client")
    def test_store_can_be_disabled(self, mock_get_client):
        mock_client = _mock_client()
        mock_get_client.return_value = mock_client
        with patch("server.open", unittest.mock.mock_open()):
            generate_image(prompt="p", store=False)
        self.assertIs(mock_client.interactions.create.call_args.kwargs["store"], False)

    @patch("server._get_client")
    def test_edit_local_image_store_can_be_disabled(self, mock_get_client):
        mock_client = _mock_client()
        mock_get_client.return_value = mock_client
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "in.png")
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            with patch("server.open", unittest.mock.mock_open(read_data=b"x")):
                edit_local_image(image_path=path, edit_prompt="e", store=False)
        self.assertIs(mock_client.interactions.create.call_args.kwargs["store"], False)


class TestExtension(unittest.TestCase):
    """The old logic defaulted to .jpg; the API returns PNG by default."""

    def test_missing_mime_type_defaults_to_png(self):
        self.assertEqual(_extension_for(""), "png")

    def test_png_mime_type(self):
        self.assertEqual(_extension_for("image/png"), "png")

    def test_jpeg_mime_type_normalised_to_jpg(self):
        self.assertEqual(_extension_for("image/jpeg"), "jpg")

    def test_webp_mime_type(self):
        self.assertEqual(_extension_for("image/webp"), "webp")

    def test_mime_type_with_parameters(self):
        self.assertEqual(_extension_for("image/png; charset=binary"), "png")

    def test_unknown_mime_type_defaults_to_png(self):
        self.assertEqual(_extension_for("application/octet-stream"), "png")

    def test_response_with_missing_mime_type_is_saved_as_png(self):
        """Regression: a PNG with no mime_type used to be written as .jpg."""
        interaction = MagicMock()
        interaction.id = "int_1"
        # An object whose mime_type attribute is genuinely absent.
        interaction.output_image = type(
            "ImageOut", (), {"data": b"bytes", "mime_type": ""}
        )()

        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {"IMAGE_OUTPUT_DIR": d}, clear=False):
                os.environ.pop("HOST_OUTPUT_DIR", None)
                result = _handle_response(interaction, "gen")
            self.assertIn(".png", result)
            self.assertNotIn(".jpg", result)
            written = os.listdir(d)
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].endswith(".png"))


class TestHostOutputDir(unittest.TestCase):
    """HOST_OUTPUT_DIR rewrites the REPORTED path only; the write is unchanged."""

    def _interaction(self):
        interaction = MagicMock()
        interaction.id = "int_1"
        interaction.output_image = type(
            "ImageOut", (), {"data": b"bytes", "mime_type": "image/png"}
        )()
        return interaction

    def test_without_host_output_dir_reports_real_abspath(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"IMAGE_OUTPUT_DIR": d}
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("HOST_OUTPUT_DIR", None)
                result = _handle_response(self._interaction(), "gen")
            self.assertIn(os.path.abspath(d), result)

    def test_host_output_dir_rewrites_reported_path(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"IMAGE_OUTPUT_DIR": d, "HOST_OUTPUT_DIR": "/Users/me/pics"}
            with patch.dict(os.environ, env, clear=False):
                result = _handle_response(self._interaction(), "gen")

            # Reported under the host dir...
            self.assertIn("/Users/me/pics/gen_", result)
            self.assertNotIn(d, result)
            # ...but actually written to IMAGE_OUTPUT_DIR.
            written = os.listdir(d)
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].startswith("gen_"))
            # The host path reuses the real filename.
            self.assertIn(written[0], result)

    def test_host_output_dir_container_style_path(self):
        """The Docker case: /images inside, a real host dir outside."""
        with tempfile.TemporaryDirectory() as d:
            env = {"IMAGE_OUTPUT_DIR": d, "HOST_OUTPUT_DIR": "/host/out"}
            with patch.dict(os.environ, env, clear=False):
                path = server._display_path(os.path.join(d, "gen_1.png"))
        self.assertEqual(path, "/host/out/gen_1.png")

    def test_file_outside_image_output_dir_is_not_rewritten(self):
        """Don't invent a host path for a file that isn't under the output dir."""
        with patch.dict(
            os.environ,
            {"IMAGE_OUTPUT_DIR": "/some/out", "HOST_OUTPUT_DIR": "/host/out"},
            clear=False,
        ):
            path = server._display_path("/elsewhere/gen_1.png")
        self.assertEqual(path, "/elsewhere/gen_1.png")


class TestInputSizeCap(unittest.TestCase):
    def test_oversized_input_image_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "big.png")
            with open(path, "wb") as f:
                f.write(b"\x00" * (2 * 1024 * 1024))  # 2 MB
            with patch.dict(os.environ, {"MAX_INPUT_IMAGE_MB": "1"}, clear=False):
                with self.assertRaises(ValueError) as ctx:
                    _get_image_data(path)
        msg = str(ctx.exception)
        self.assertIn("too large", msg)
        self.assertIn("MAX_INPUT_IMAGE_MB", msg)

    def test_within_cap_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ok.png")
            with open(path, "wb") as f:
                f.write(b"\x00" * 1024)
            with patch.dict(os.environ, {"MAX_INPUT_IMAGE_MB": "1"}, clear=False):
                data = _get_image_data(path)
        self.assertEqual(data["mime_type"], "image/png")

    @patch("server._get_client")
    def test_oversized_input_surfaces_as_toolerror(self, mock_get_client):
        mock_get_client.return_value = _mock_client()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "big.png")
            with open(path, "wb") as f:
                f.write(b"\x00" * (2 * 1024 * 1024))
            with patch.dict(os.environ, {"MAX_INPUT_IMAGE_MB": "1"}, clear=False):
                with self.assertRaises(ToolError) as ctx:
                    edit_local_image(image_path=path, edit_prompt="e")
        self.assertIn("too large", str(ctx.exception))

    def test_default_cap_is_20_mb(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MAX_INPUT_IMAGE_MB", None)
            self.assertEqual(server._max_input_image_bytes(), 20 * 1024 * 1024)


class TestClientConfiguration(unittest.TestCase):
    """Auth paths and timeout/retry wiring. No real API calls are made."""

    def setUp(self):
        server.client = None

    def tearDown(self):
        server.client = None

    def test_api_key_path(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False) as _:
            os.environ.pop("GOOGLE_GENAI_USE_ENTERPRISE", None)
            with patch("server.genai.Client") as mock_cls:
                server._get_client()
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertNotIn("enterprise", kwargs)

    def test_missing_credentials_raises(self):
        with patch.dict(os.environ, {}, clear=False):
            for var in (
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "GOOGLE_GENAI_USE_ENTERPRISE",
            ):
                os.environ.pop(var, None)
            with self.assertRaises(ValueError) as ctx:
                server._get_client()
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))

    def test_enterprise_path_uses_enterprise_flag(self):
        """Verified: the Interactions API enterprise path uses enterprise=True,
        NOT vertexai=True."""
        env = {
            "GOOGLE_GENAI_USE_ENTERPRISE": "true",
            "GOOGLE_CLOUD_PROJECT": "your-project-id",
            "GOOGLE_CLOUD_LOCATION": "global",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("server.genai.Client") as mock_cls:
                server._get_client()
        kwargs = mock_cls.call_args.kwargs
        self.assertIs(kwargs["enterprise"], True)
        self.assertNotIn("vertexai", kwargs)
        self.assertEqual(kwargs["project"], "your-project-id")
        self.assertEqual(kwargs["location"], "global")
        self.assertNotIn("api_key", kwargs)

    def test_enterprise_location_defaults_to_global(self):
        env = {
            "GOOGLE_GENAI_USE_ENTERPRISE": "1",
            "GOOGLE_CLOUD_PROJECT": "your-project-id",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GOOGLE_CLOUD_LOCATION", None)
            with patch("server.genai.Client") as mock_cls:
                server._get_client()
        self.assertEqual(mock_cls.call_args.kwargs["location"], "global")

    def test_enterprise_without_project_raises(self):
        env = {"GOOGLE_GENAI_USE_ENTERPRISE": "1"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
            with self.assertRaises(ValueError) as ctx:
                server._get_client()
        self.assertIn("GOOGLE_CLOUD_PROJECT", str(ctx.exception))

    def test_client_is_cached(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=False):
            os.environ.pop("GOOGLE_GENAI_USE_ENTERPRISE", None)
            with patch("server.genai.Client") as mock_cls:
                first = server._get_client()
                second = server._get_client()
        self.assertIs(first, second)
        self.assertEqual(mock_cls.call_count, 1)

    def test_timeout_is_passed_in_milliseconds(self):
        """HttpOptions.timeout is in milliseconds, so seconds must be scaled."""
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "k", "GEMINI_TIMEOUT_SECONDS": "45"},
            clear=False,
        ):
            os.environ.pop("GOOGLE_GENAI_USE_ENTERPRISE", None)
            with patch("server.genai.Client") as mock_cls:
                server._get_client()
        http_options = mock_cls.call_args.kwargs["http_options"]
        self.assertEqual(http_options["timeout"], 45_000)

    def test_default_timeout(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=False):
            for var in ("GOOGLE_GENAI_USE_ENTERPRISE", "GEMINI_TIMEOUT_SECONDS"):
                os.environ.pop(var, None)
            with patch("server.genai.Client") as mock_cls:
                server._get_client()
        self.assertEqual(mock_cls.call_args.kwargs["http_options"]["timeout"], 120_000)

    def test_invalid_timeout_falls_back_to_default(self):
        for bad in ("abc", "0", "-5"):
            with self.subTest(value=bad):
                server.client = None
                with patch.dict(
                    os.environ,
                    {"GEMINI_API_KEY": "k", "GEMINI_TIMEOUT_SECONDS": bad},
                    clear=False,
                ):
                    os.environ.pop("GOOGLE_GENAI_USE_ENTERPRISE", None)
                    with patch("server.genai.Client") as mock_cls:
                        server._get_client()
                self.assertEqual(
                    mock_cls.call_args.kwargs["http_options"]["timeout"], 120_000
                )

    def test_retry_options_cover_429_and_5xx(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=False):
            os.environ.pop("GOOGLE_GENAI_USE_ENTERPRISE", None)
            with patch("server.genai.Client") as mock_cls:
                server._get_client()
        retry = mock_cls.call_args.kwargs["http_options"]["retry_options"]
        self.assertIn(429, retry["http_status_codes"])
        for code in (500, 502, 503, 504):
            self.assertIn(code, retry["http_status_codes"])
        self.assertGreater(retry["attempts"], 1)

    def test_enterprise_path_also_gets_timeout(self):
        env = {
            "GOOGLE_GENAI_USE_ENTERPRISE": "1",
            "GOOGLE_CLOUD_PROJECT": "your-project-id",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("server.genai.Client") as mock_cls:
                server._get_client()
        http_options = mock_cls.call_args.kwargs["http_options"]
        self.assertEqual(http_options["timeout"], 120_000)
        self.assertIn("retry_options", http_options)


class TestHttpOptionsAcceptedBySdk(unittest.TestCase):
    """Guard that the http_options dict we build is actually valid for the SDK.

    Constructs a real client object (no network I/O) so a future SDK change to
    the http_options shape fails here rather than at the first API call.
    """

    def setUp(self):
        server.client = None

    def tearDown(self):
        server.client = None

    def test_real_sdk_accepts_our_http_options(self):
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "k", "GEMINI_TIMEOUT_SECONDS": "30"},
            clear=False,
        ):
            os.environ.pop("GOOGLE_GENAI_USE_ENTERPRISE", None)
            real_client = server._get_client()
        resolved = real_client._api_client._http_options
        self.assertEqual(resolved.timeout, 30_000)
        self.assertIsNotNone(resolved.retry_options)
        self.assertIn(429, resolved.retry_options.http_status_codes)


if __name__ == "__main__":
    unittest.main()
