# Modernization proposal — claude-gemini-image-editing fork

Fork: [tkhduracell/claude-gemini-image-editing](https://github.com/tkhduracell/claude-gemini-image-editing)
Upstream last pushed 2026-07-22. Reviewed 2026-10-24.

Target shape: **packaged as a Claude Code plugin whose manifest launches a
Docker-hosted MCP server, authenticated with a Gemini API key, model selectable
via env.** `claude mcp add` is kept as a documented fallback — §2b explains why
the plugin is the better primary path. The gcloud/ADC route is possible but not
the default; §7 has the finding.

---

## 1. The model surface is out of date (highest value, smallest change)

The server hardcodes constraints that the live API has since widened. `server.py`
currently enforces:

```python
SUPPORTED_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4"}
SUPPORTED_THINKING_LEVELS = {"low", "high"}
```

Both sets are now wrong, and because `_validate_inputs` raises before the API is
ever called, the server **rejects valid requests locally**. Per the current
[Interactions image-generation docs](https://ai.google.dev/gemini-api/docs/interactions/image-generation):

| Repo assumes | Actually supported now |
| :--- | :--- |
| 5 aspect ratios | `1:1`, `3:2`, `2:3`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` (10, for flash-lite-image) |
| `thinking_level` ∈ {low, high} | `minimal`, `low`, `medium`, `high` — **varies by model**; default is `medium` on nano-banana-2.1, `minimal` on the 3.1 models |
| no `image_size` at all | `1K` / `2K` / `4K` (uppercase K required; lowercase rejected). flash-lite-image is 1K-only |
| no `mime_type` control | `response_format.mime_type` accepts `image/png`, `image/jpeg` |

Note the `thinking_level` reversal: `CLAUDE.md` claims "the live API rejects
`minimal`/`medium` for this model (HTTP 400)". That was true for the July-era
model but the supported set is now model-dependent, so a hardcoded allowlist is
the wrong design regardless of which values are right today.

**Newer models available** ([Firebase model list](https://firebase.google.com/docs/ai-logic/models), all Stable):

| Model | Product | Released | Notes |
| :--- | :--- | :--- | :--- |
| `gemini-nano-banana-2.1` | Nano Banana 2.1 | 2026-10-06 | newest; **short-term availability** |
| `gemini-3.1-flash-image` | Nano Banana 2 | 2026-05-28 | shutdown no earlier than 2027-05-28 |
| `gemini-3.1-flash-lite-image` | Nano Banana 2 Lite | 2026-06-30 | repo default; **short-term availability** |
| `gemini-3-pro-image` | Nano Banana Pro | 2026-05-28 | shutdown no earlier than 2027-05-28 |

The repo's own default is one of the two models flagged as short-term, so
pinning matters.

### Recommended change

Replace the hardcoded validation with a per-model capability table and let the
API be the authority on anything not in it:

```python
MODEL_CAPS = {
    "gemini-3.1-flash-lite-image": {"sizes": {"1K"}, "thinking": {"low", "high"}},
    "gemini-3.1-flash-image":      {"sizes": {"1K", "2K"}, "thinking": {"minimal", "low", "medium", "high"}},
    "gemini-3-pro-image":          {"sizes": {"1K", "2K", "4K"}, "thinking": {"low", "medium", "high"}},
    "gemini-nano-banana-2.1":      {"sizes": {"1K", "2K", "4K"}, "thinking": {"minimal", "low", "medium", "high"}},
}
ASPECT_RATIOS = {"1:1","3:2","2:3","3:4","4:3","4:5","5:4","9:16","16:9","21:9"}
```

For an unknown model name (a future release), pass the request through rather
than rejecting it — a clear API error beats a stale local allowlist. Add
`image_size` and `mime_type` as optional tool params, and expose a
`list_models` tool so the agent can see what's configured instead of guessing.

---

## 2. Docker as the primary install path

The existing `Dockerfile` is close but needs four fixes to be the main path.

**Current problems:**
- `FROM python:3.12-slim` with **unpinned** `requirements.txt` (`google-genai`,
  `mcp` — no versions at all). Every rebuild pulls whatever is on PyPI; latest
  today is `google-genai 2.33.0`, `mcp 2.3.0`. A silent major bump in either
  breaks the server. Also note `interaction.output_image` requires the SDK at
  **2.3.0 or later** — the code depends on it with nothing enforcing it.
- Runs as **root**. Images are written into a mounted host directory, so files
  land root-owned on the host.
- `WORKDIR /images` is both the output dir and the mount point; combined with
  the `-v "$PWD:$PWD" -w "$PWD"` trick in the README this is confusing.
- No healthcheck/metadata, no multi-arch build (you're on arm64 Mac; Docker Hub
  tags `0.1.1`/`latest` are presumably amd64 only).

**Recommended Dockerfile:**

```dockerfile
FROM python:3.13-slim

# Pin both deps. google-genai>=2.3.0 is required for interaction.output_image.
COPY requirements.txt /opt/gemini-image/requirements.txt
RUN pip install --no-cache-dir -r /opt/gemini-image/requirements.txt

COPY server.py /opt/gemini-image/server.py

# Non-root so images written to a bind mount aren't root-owned on the host.
RUN useradd --create-home --uid 1000 gemini-image
USER gemini-image

ENV PYTHONUNBUFFERED=1 \
    IMAGE_OUTPUT_DIR=/images
WORKDIR /images

ENTRYPOINT ["python", "/opt/gemini-image/server.py"]
```

with `requirements.txt` pinned:

```
google-genai>=2.33.0,<3
mcp>=2.3.0,<3
```

Build multi-arch so it runs natively on your Mac and on amd64 CI:

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t <you>/gemini-image:0.2.1 --push .
```

### Registering the Docker server with Claude Code

This is the **fallback** path for a server-only install (see §2b — the plugin is
the primary path). Still an improvement on the current
`init.sh`/`set_env.sh`/`project-setup.sh` trio, since it needs no pip and no venv:

```bash
claude mcp add gemini-image \
  --env GEMINI_API_KEY=<key> \
  --env GEMINI_MODEL_NAME=gemini-nano-banana-2.1 \
  --env IMAGE_OUTPUT_DIR=/images \
  -- docker run --rm -i \
       -e GEMINI_API_KEY -e GEMINI_MODEL_NAME -e IMAGE_OUTPUT_DIR \
       -v "$PWD/images:/images" \
       <you>/gemini-image:0.2.1
```

One caveat worth handling explicitly: the server returns
`os.path.abspath(full_output_path)` in its success message, which inside the
container is `/images/gen_….png` — **not a path the host or Claude can open.**
Either keep the README's same-path mount trick (`-v "$PWD:$PWD" -w "$PWD"`) or,
better, add a `HOST_OUTPUT_DIR` env var the server uses purely for display, so
the reported path is the one the user can actually click.

---

## 2b. Plugin vs. `claude mcp add` — use the plugin

**Recommendation: package as a plugin, and have the plugin manifest launch the
Docker container.** These aren't competing options; the plugin is the delivery
mechanism and Docker is what it runs.

Why the plugin wins:

| | `claude mcp add` | Plugin install |
| :--- | :--- | :--- |
| Ships the **skill** | No — MCP server only | Yes, skill + server together |
| Install | one long command with env flags | `/plugin install` |
| Per-project setup | repeat the command each project | once, marketplace is global |
| Updates | re-run, or hand-edit `.mcp.json` | `/plugin update` |
| Key in `.mcp.json` | yes, if passed with a value | no — manifest has no key |
| Versioned | no | yes, via `plugin.json` |

The decisive point: the skill is the actual product here. `claude mcp add`
registers only the server, so the agent gets four tools but none of
`SKILL.md`'s guidance on when to use them, how to chain interaction IDs, or not
to change aspect ratio mid-session. The plugin delivers both.

The manifest already auto-registers the server and deliberately carries no key —
the server reads `GEMINI_API_KEY` from the environment. Point it at Docker:

```json
{
  "mcpServers": {
    "gemini-image": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "GEMINI_API_KEY",
        "-e", "GEMINI_MODEL_NAME",
        "-v", "${CLAUDE_PROJECT_DIR}/images:/images",
        "<you>/gemini-image:0.2.1"
      ]
    }
  }
}
```

The bare `-e GEMINI_API_KEY` (name only, no value) makes Docker inherit it from
the environment, so the key stays out of the manifest and out of git — exactly
the §4 goal, achieved by construction.

Then install becomes:

```
/plugin marketplace add <you>/claude-gemini-image-editing
/plugin install gemini-image-editing@claude-gemini-image-editing
```

**One real tradeoff:** the plugin requires Docker on the machine, and a missing
image fails at server startup with a Docker error rather than something
actionable. Mitigate by documenting a `docker pull` as step zero, and keeping
the pip-based `claude mcp add` form in the README as the no-Docker fallback.

So: **plugin as the primary path, Docker as what it runs, `claude mcp add`
documented as the fallback.** Note this means keeping a minimal version of the
skill-assembly step — see §3.

---

## 3. Drop the snapshot-sync machinery

The repo keeps **four copies** of `server.py` (root, `mcp/`,
`.claude/skills/…/mcp/`, `skills/…/mcp/`) kept in sync by `refresh_skill.py` +
`make skill`, with `CLAUDE.md` warning "never edit a snapshot directly." Same
for `requirements.txt` (×4) and the references doc (×3). That's ~900 duplicated
lines of Python and a whole sync tool to maintain.

If the server ships as a Docker image, the skill no longer needs to bundle it at
all. The skill becomes just `SKILL.md` + `references/` — no `mcp/` subdirectory,
because the plugin manifest (§2b) launches the container instead.

That deletes `refresh_skill.py`, `mcp/`, `dist/gemini-image-editing-skill.zip`, the
`make skill-package`/`skill-install` targets, `project-setup.sh`,
`mcp/project-setup.sh`, `init.sh`, `set_env.sh`, and the "snapshot sync model"
section of `CLAUDE.md`.

**What survives:** the plugin layout needs the skill at `skills/gemini-image-editing/`,
so keep one copy there as the source of truth and have `.claude/skills/` be a
symlink to it (so the skill is active when working in this repo, without a
second copy). Two text files, no Python to sync — `make skill` becomes
unnecessary rather than merely smaller.

Keep the pip path as a documented fallback for people who don't want Docker, but
stop generating copies for it: the fallback is `pip install -r requirements.txt`
plus the `claude mcp add` form from §2, both pointing at the single root
`server.py`.

---

## 4. Secret handling is the weakest part

Current flow (`set_env.sh`): reads or prompts for the key → writes it to
`~/gemini.key` **in plaintext with default permissions** → writes `.env` →
**injects the literal key into `.mcp.json`**. Three plaintext copies on disk.

`.gitignore` covers `.env` and `.mcp.json`, so it's not a leak-to-git risk, but:

- `~/gemini.key` is created with no `chmod 600`.
- `set_env.sh` does `source .env` without quoting — a key containing shell
  metacharacters would execute.
- As `CLAUDE.md` already notes, `set_env.sh` **replaces the entire `env` block**
  with just the two key vars, silently dropping any custom `GEMINI_MODEL_NAME`
  or `IMAGE_OUTPUT_DIR`. That's a real bug.
- `GOOGLE_API_KEY` is set to the same value "for backward compatibility", which
  doubles the exposure for no benefit.

**Recommended:** stop writing the key into `.mcp.json` entirely. Pass it by
reference — `--env GEMINI_API_KEY` with no value makes Docker inherit it from
the environment, so the key lives in exactly one place (your shell, a keychain,
or 1Password). If you keep `~/gemini.key`, `chmod 600` it on write and quote
every expansion. Drop the `GOOGLE_API_KEY` mirror.

---

## 5. No CI at all

`.github/` does not exist. `make lint` (ruff + `bash -n`) and `make test` exist
but nothing runs them. Minimum worth adding:

- **lint + test** on push/PR: `ruff check`, `ruff format --check`, `bash -n` on
  the scripts. Tests need a real API key, so gate the integration job on a repo
  secret and let it skip on forks.
- **Docker build** on PR (build only), **build + push multi-arch** on tag.
- **Dependabot** or Renovate for the pinned deps — the whole point of pinning is
  that something tells you when to move.
- **`claude plugin validate .`** as a CI step, since the manifests are a
  supported install path.
- A **model-drift check**: a weekly scheduled job that hits the models endpoint
  and opens an issue if the configured default disappears. Both short-term
  models in §1 make this more than theoretical.

---

## 6. Server code details

Worth fixing while you're in there:

- **`_handle_response` extension logic is backwards.** It defaults `ext = "jpg"`
  and only switches to png/webp if the mime type says so. But the API returns
  PNG by default, and if `mime_type` is missing (it's read with a `getattr`
  default of `""`) a PNG gets written as `.jpg`. Default to `png`, or better,
  derive it with `mimetypes.guess_extension`.
- **Errors are returned as `🔴 …` strings, not MCP errors.** `CLAUDE.md` flags
  this as something the caller must remember to check. FastMCP supports raising
  `ToolError` — use it, so failures surface as protocol errors instead of
  success-shaped text an agent may well treat as success.
- **No timeout or retry.** A hung API call hangs the tool indefinitely. Set an
  explicit timeout via `http_options` and retry 429/5xx with backoff.
- **Base64 round-trip is wasteful.** `_get_image_data` base64-encodes the whole
  file in memory; for a 4K input that's a large allocation. Also add a size cap
  with a clear error rather than letting the API reject it after the upload.
- **`store=True` is unconditional.** Every generation persists context on
  Google's servers. Make it a parameter — some users will not want that, and
  there's no tool to delete an interaction.
- **Blocking I/O in async server.** FastMCP tools here are sync `def`, so file
  reads/writes and the API call block the event loop. Either make them `async`
  with the async client, or accept it and document that the server is
  single-flight.
- **Typing.** Five `cast(Any, …)` calls paper over the SDK's real types. With
  `google-genai` pinned you can use the actual types and add a `mypy`/`pyright`
  step.

---

## 7. The gcloud/ADC question — why it isn't the default

You asked about `gcloud auth` first, so here's the finding: **it is technically
possible but not a good fit for this skill.**

The Interactions API does support ADC, via the enterprise path — not the
`vertexai=True` flag you might expect ([developer guide](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/interactions/developer-guide)):

```python
client = genai.Client(enterprise=True, project="PROJECT_ID", location="global")
```

Configured by `GOOGLE_GENAI_USE_ENTERPRISE`, `GOOGLE_CLOUD_PROJECT`,
`GOOGLE_CLOUD_LOCATION`, with `gcloud auth application-default login` for local
credentials. Caller needs `roles/aiplatform.user` plus the Agent Platform API
and billing enabled.

**Why not default to it:**

1. **Image output isn't documented on that path.** The enterprise developer
   guide documents `response_format` only for JSON schemas and names only
   `gemini-3.8-flash` in its examples. It does not document
   `response_format: {"type": "image"}` or confirm which models produce image
   output there. The image-generation docs that *do* cover it are the Developer
   API docs. So this needs an empirical test before anyone relies on it.
2. **Preview/regional friction.** Interactions is listed as preview on the
   platform side, and preview models are global-location-only.
3. **Docker + ADC is clumsy** — you'd mount
   `~/.config/gcloud/application_default_credentials.json` read-only into the
   container and set `GOOGLE_APPLICATION_CREDENTIALS`. Workable, but it's a
   refresh-token-bearing file crossing a container boundary, and it expires.

The local setup is ready for it if you want it (gcloud present, ADC file
present), so the pragmatic answer is:
**support both, default to the API key.** Pick the client in one place:

```python
def _get_client() -> genai.Client:
    if os.environ.get("GOOGLE_GENAI_USE_ENTERPRISE", "").lower() in ("1", "true"):
        return genai.Client(
            enterprise=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        )
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ValueError("Set GEMINI_API_KEY, or GOOGLE_GENAI_USE_ENTERPRISE=1 with ADC.")
    return genai.Client(api_key=key)
```

That's ~10 lines and keeps the gcloud door open without betting the default on
an undocumented capability.

---

## 8. Docs & repo hygiene

- **`CLAUDE.md` contradicts the live API** on `thinking_level` (§1) — it will
  actively mislead an agent working in the repo.
- **Three docs overlap heavily**: `GEMINI.md`, `references/gemini-interactions-api.md`,
  and the API tables in `CLAUDE.md`. Keep one vendored reference, have the
  others link to it.
- **`devto-article.md` + `devto-cover.jpg`** are upstream marketing; delete them
  from the fork.
- **Fork identity**: `plugin.json`, `marketplace.json`, and `SKILL.md` all still
  point at `xbill9` for author, homepage, repository, and the
  `/plugin marketplace add` command. Update them or the fork advertises upstream.
- **`.gitignore` ignores `*.png`/`*.jpg` globally** with one exception for the
  cover. Once that's deleted, scope the ignore to the output dir so legitimate
  doc screenshots aren't silently dropped.
- **Version is `0.1.1` in three places** (`plugin.json`, `Makefile`, README
  Docker tags) with nothing keeping them aligned. Single-source it.
- **LICENSE**: Apache-2.0 — keep it, preserve upstream copyright, and note the
  fork in `NOTICE`.

---

## Suggested order

| # | Change | Why first |
| :--- | :--- | :--- |
| 1 | Pin deps; fix model/aspect/thinking/image_size table; default to a current model | Fixes wrong behavior today; unblocks newest models |
| 2 | Harden Dockerfile (3.13, non-root, multi-arch); push the image | Everything below runs on it |
| 3 | Point `plugin.json` at Docker; fix fork identity in the manifests | Makes the plugin the real install path |
| 4 | Fix the host-vs-container output path | Otherwise reported paths aren't clickable |
| 5 | Secrets: bare `-e GEMINI_API_KEY`, drop key injection into `.mcp.json` | Mostly falls out of §3 |
| 6 | Add CI (lint, build, `claude plugin validate`, Dependabot) | Protects 1–5 |
| 7 | Delete snapshot machinery; collapse to one `server.py` + one skill copy | Big simplification, safe once the plugin is the path |
| 8 | Server internals (ToolError, timeouts, ext fix, `store` param) | Quality; independent of the above |
| 9 | Optional ADC/enterprise support behind an env flag | Needs an empirical image-output test |

## Sources

- [Interactions API — image generation](https://ai.google.dev/gemini-api/docs/interactions/image-generation)
- [Interactions API overview](https://ai.google.dev/gemini-api/docs/interactions-overview)
- [Interactions API developer guide (enterprise/ADC)](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/capabilities/interactions/developer-guide)
- [Firebase AI Logic — supported models](https://firebase.google.com/docs/ai-logic/models)
- [Google Gen AI Python SDK docs](https://googleapis.github.io/python-genai/)
- [Nano Banana 2 / Pro GA announcement](https://cloud.google.com/blog/products/ai-machine-learning/nano-banana-2-and-nano-banana-pro-are-generally-available)
- [Gemini 3 developer guide](https://ai.google.dev/gemini-api/docs/gemini-3)
