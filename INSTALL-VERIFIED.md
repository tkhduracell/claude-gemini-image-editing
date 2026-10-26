# Plugin install — verified walkthrough

Every step below was executed on 2026-10-25 against the published
`ghcr.io/tkhduracell/gemini-image:0.3.0`, with the actual output recorded. This
is not a dry run written from the docs.

## Prerequisites

- **Docker** running. The plugin launches the server as a container; without
  Docker the MCP server fails at startup.
- **A funded Gemini API key.** Image generation is billable. A key with no
  credits authenticates fine and lists models, but every generation returns
  `HTTP 402 — prepayment credits are depleted`.

## 1. Store the API key

```bash
mkdir -p ~/.config/gemini
printf '%s' 'YOUR_KEY_HERE' > ~/.config/gemini/api.key
chmod 600 ~/.config/gemini/api.key
```

**Use the key file, not an environment variable.** The plugin mounts this file
read-only into the container and reads it via `GEMINI_API_KEY_FILE`.

A GUI-launched app — the Claude desktop app included — does **not** source
`~/.zshrc` or `~/.bash_profile`. A key exported there is invisible to the
plugin, which then reports `API key: Not Set` even though your terminal has it.
The file works for both.

An exported `GEMINI_API_KEY` still takes precedence when present, so a
terminal-launched Claude Code keeps working as before.

> Verified: with `GEMINI_API_KEY` and `GOOGLE_API_KEY` stripped from the
> environment, a headless `claude -p` run reported
> `Current Auth: API key: `Set` (from `GEMINI_API_KEY_FILE`)`. `${HOME}`
> expands correctly in the manifest, and an unset `HOST_OUTPUT_DIR` is handled
> safely rather than erroring.

## 2. Pull the image (optional)

```bash
docker pull ghcr.io/tkhduracell/gemini-image:0.3.0
```

Optional — Docker pulls on first run. Doing it up front turns a slow first
tool call into a visible download, and confirms registry access before you
involve Claude Code.

> Verified: pulls anonymously, no `docker login` needed. The manifest is a
> multi-arch index carrying `linux/amd64` and `linux/arm64`.

## 3. Add the marketplace

```bash
claude plugin marketplace add tkhduracell/claude-gemini-image-editing
```

> Verified output:
> ```
> ✔ Successfully added marketplace: claude-gemini-image-editing (declared in user settings)
> ```

## 4. Install the plugin

```bash
claude plugin install gemini-image-editing@claude-gemini-image-editing
```

> Verified output:
> ```
> ✔ Successfully installed plugin: gemini-image-editing@claude-gemini-image-editing (scope: user)
> ```

Confirm with `claude plugin list` — the entry should read **Version: 0.2.0,
Status: ✔ enabled**. The list is alphabetical and can be long; grep for
`gemini` rather than reading the first screen.

## 5. Verify the MCP server connects

From the project directory you want images written into:

```bash
claude mcp list
```

> Verified output:
> ```
> plugin:gemini-image-editing:gemini-image: docker run --rm -i -e GEMINI_API_KEY
>   -e GEMINI_MODEL_NAME -e HOST_OUTPUT_DIR -v /your/project/images:/images
>   ghcr.io/tkhduracell/gemini-image:0.3.0 - ✔ Connected
> ```

Two things this confirms beyond "it starts": `${CLAUDE_PROJECT_DIR}` expanded to
the real project path (so the bind mount is correct), and the container
completed an MCP handshake rather than merely launching.

## 6. Verify end to end in Claude Code

Restart Claude Code in the project, then:

1. **Check configuration first.** Ask Claude to call `get_help`. It reports auth
   mode, active model, output directory and `HOST_OUTPUT_DIR`. If auth is
   missing, stop here — every other tool will fail.
2. **Generate.** "Generate an image of a red maple leaf on wet slate, 16:9."
   You should get a saved path and an **interaction ID**.
3. **Edit statefully.** "Add water droplets to the leaf." The result should keep
   the same leaf, slate texture and lighting, changing only what you asked.
   This is the whole point of the Interactions API — if the second image looks
   like a fresh generation, the interaction ID was not chained.
4. **Open the file.** The reported path should be openable on the host.

> Verified directly against the live API (outside the plugin, through the same
> server code): generate returned a 1376x768 JPEG, and the stateful edit
> preserved the leaf, slate cracks and lighting while adding only droplets.

## Expected behaviour worth knowing

**Thinking levels are not uniform, and the published docs are wrong.** Measured
against the live API:

| Model | `image_size` | `thinking_level` |
| :--- | :--- | :--- |
| `gemini-nano-banana-2.1` *(default)* | 1K, 2K, 4K | minimal, medium, high |
| `gemini-3.1-flash-image` | 1K, 2K, 4K | minimal, high |
| `gemini-3-pro-image` | 1K, 2K, 4K | low, high |
| `gemini-3.1-flash-lite-image` | 1K only | minimal, high |

No level works everywhere: `low` is rejected by the whole 3.1 family *and* by
nano-banana-2.1, while `minimal` is rejected by `gemini-3-pro-image`. **Omit
`thinking_level`** and the server picks one the active model accepts.

**Output paths.** Images are written to `./images` in the project. Inside the
container that is `/images`; `HOST_OUTPUT_DIR` makes the server report the
host-visible path instead.

**Errors are real MCP errors.** A failure arrives as a protocol error, not as
text that looks like success.

## Troubleshooting

| Symptom | Cause |
| :--- | :--- |
| Server fails at startup | Docker not running, or the image cannot be pulled. Try `docker pull ghcr.io/tkhduracell/gemini-image:0.3.0` directly. |
| `get_help` reports no key | The key file is missing or empty. GUI apps do not read `~/.zshrc`, so an exported env var does not reach the plugin — create `~/.config/gemini/api.key` as in step 1 and restart Claude Code. |
| `HTTP 402 … prepayment credits are depleted` | Account-level billing, not a bug. Affects text calls too. Top up at the `ai.studio/projects` link in the error. |
| `HTTP 400 … thinking level X is not supported` | That level is not valid for the active model. See the table above, or omit `thinking_level`. |
| `Unsupported image size '1k'` | Uppercase `K` is required. |
| Edit looks like a fresh image | A stale interaction ID forks from an older state rather than erroring. Always chain the most recent ID. |
| Changing aspect ratio mid-session degrades quality | Expected. Pick the ratio at generation time; stateful edits inherit it. |

## Uninstall

```bash
claude plugin uninstall gemini-image-editing@claude-gemini-image-editing
claude plugin marketplace remove claude-gemini-image-editing
```
