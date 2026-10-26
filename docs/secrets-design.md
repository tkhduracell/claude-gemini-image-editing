# Passing secrets to a Dockerised MCP server

Why this project uses a mounted key file, what the alternatives are, and when
each is right. Written after the env-var approach silently failed under the
Claude desktop app.

## The short answer

For a **local stdio MCP server**, a **mounted secret file** is the best
practice, with an env var accepted as a fallback. This is the `_FILE`
convention most official Docker images already use.

**OAuth is not the answer here** — see "Why not OAuth" below. That is not a
cost-cutting judgement; the MCP spec itself says so.

## The three contexts

| Context | How the server is launched | What works |
| :--- | :--- | :--- |
| **Claude Code in a terminal** | Inherits your shell env | Env var **or** key file |
| **Claude desktop app** | GUI process; **does not** source `~/.zshrc` | **Key file only** |
| **Cloud / CI** | Runner injects secrets | Env var from the platform's secret store, or a mounted file |

The middle row is the trap. A GUI-launched app inherits `launchd`'s
environment, not your shell's, so `export GEMINI_API_KEY=…` in `~/.zshrc` is
invisible to it. The container then starts fine and reports the key as "Not
Set" — a silent, confusing failure, not a crash.

A file is readable in all three contexts, which is the whole reason to prefer
it.

## Why a file beats an env var

Measured on this image, not quoted from a blog:

```
# env var
docker run -e GEMINI_API_KEY=SECRET … 
docker inspect <container> | grep SECRET   → 1 match (the key, in cleartext)

# mounted file
docker run -e GEMINI_API_KEY_FILE=/run/secrets/gemini.key \
           -v ~/.config/gemini/api.key:/run/secrets/gemini.key:ro …
docker inspect <container> | grep "$KEY"   → 0 matches
```

An env-var secret is readable by **anyone with Docker socket access**, via
`docker inspect`. It also reaches `/proc/<pid>/environ`, crash dumps, and any
library that logs the environment on error. A mounted file appears in the
container config only as a path.

The file also outlives the process cleanly: no shell history, no risk of it
landing in a `.env` that gets committed.

## Why not OAuth / device flow

Tempting, but wrong for this shape of server, and the spec is explicit.

The MCP authorization specification scopes OAuth to **HTTP transports**. For
stdio servers it says the opposite: retrieve credentials from the environment,
and do *not* run an HTTP OAuth flow. The reasoning is structural — a stdio
server is a child process of the client, not a web service. There is no
inbound HTTP request to issue a `401` challenge against, no redirect URI, and
no browser session to attach to. The trust boundary is already the local
machine and the launching client.

Concretely, a device flow inside this container would mean:

- The container prints a code and URL to **stderr**, which the client may not
  surface to you at all.
- It then needs somewhere to persist the resulting token — a mounted volume,
  so you are back to mounting a file, but now with refresh logic and an expiry
  to debug.
- `--rm` containers are per-invocation, so without that volume you would
  re-authorise constantly.
- Gemini API keys are not OAuth credentials anyway. The nearest equivalent is
  the ADC/enterprise path, which this server already supports behind
  `GOOGLE_GENAI_USE_ENTERPRISE` — and that is exactly a case where you mount
  the ADC file.

Where OAuth **is** right: a **remote** MCP server over HTTP, serving multiple
users, needing per-user identity, scoped consent and revocation. That is a
different product. A single-user local tool holding one API key gains
complexity and no security.

Note ADC has the same container problem and the same answer: mount
`~/.config/gcloud/application_default_credentials.json` read-only. It is still
a long-lived refresh token crossing a container boundary, which is one reason
the API-key path stays the default here.

## Making it not suck to install

A file the user must create by hand before the plugin works is a bad install.
Three things move that work off the user:

1. **The server searches conventional paths.** With no `GEMINI_API_KEY_FILE`
   set it tries `/run/secrets/api.key` (the plugin mount), then
   `~/.config/gemini/api.key`, `~/.gemini/api.key`, `~/.gemini.key`,
   `~/gemini.key`. Dropping a key in any of them is enough.
2. **A `/gemini-setup-key` command** writes the file with the right
   permissions, so the user never runs `mkdir`/`chmod` themselves.
3. **The no-key error is the instructions.** It names the command, gives the
   manual fallback, and links to where keys come from. `get_help` lists every
   path it searched.

The manifest also mounts the config **directory**, not the key file. Bind-
mounting a file that does not exist yet makes Docker create a *directory* at
that path, which then shadows the real key when the user creates it - so
install order would have mattered. Mounting the parent removes that trap.

## What this project does

```jsonc
"args": [
  "run", "--rm", "-i",
  "-e", "GEMINI_API_KEY",          // name only — inherited when present
  "-e", "GEMINI_API_KEY_FILE=/run/secrets/gemini.key",
  "-v", "${HOME}/.config/gemini/api.key:/run/secrets/gemini.key:ro",
  …
]
```

Resolution order in `_read_api_key()`:

1. `GEMINI_API_KEY` / `GOOGLE_API_KEY` from the environment — wins when set, so
   terminal and CI usage is unchanged.
2. The file at `GEMINI_API_KEY_FILE`, trimmed of whitespace.
3. Otherwise an error that **names the rc-file trap**, because the failure is
   otherwise baffling.

Deliberate details:

- **`:ro`** — the container has no reason to write the key.
- **Fails loudly on an unreadable configured file.** A missing file that
  silently falls through to "no key" produces a confusing downstream error
  instead of pointing at the real problem.
- **Empty file is treated as no key**, not as an empty key.
- **`chmod 600`** on the host file, so other local users cannot read it.
- **`get_help` reports the source** (`from the environment` vs
  `from GEMINI_API_KEY_FILE`), so diagnosing takes one call.

## Options not taken

| Option | Why not |
| :--- | :--- |
| **Key baked into the image** | Persists in layers, registries and build caches. Never do this. |
| **Key literal in `plugin.json`** | The manifest is committed to git. This is what the name-only `-e` form exists to avoid. |
| **Docker Swarm `secrets:`** | Real `/run/secrets` support needs Swarm or Compose; a plain `docker run` from a plugin manifest has neither. The bind mount is the equivalent. |
| **`docker mcp secret set`** | Docker's own MCP Toolkit keychain store. Good if you are already on the MCP Gateway, but it is Docker Desktop-oriented and reportedly unavailable on headless Linux — too much to require of an installer. |
| **OS keychain lookup in the server** | Means a platform-specific helper per OS, and the container cannot reach the host keychain without mounting something anyway. |

## If you want to go further

- **`pass`/1Password/`security find-generic-password`** can generate the key
  file at login, so the plaintext file never persists. The server needs no
  change — it still reads a path.
- **Short-lived keys.** Rotate the file; the next container run picks it up,
  since it is read at client construction.
- **Cloud/CI.** Use the platform's secret store, which injects an env var. That
  path is already supported and takes precedence.

## Elicitation: the setup step removed (implemented in 0.3.0)

The server now asks for the key itself. On a tool call with no key available
it sends an `elicitation/create` request; the client prompts the user; the key
comes back and is saved. Install is two slash commands and the first image
request collects the credential inline.

Verified over real stdio against the published container:

```
→ initialize (capabilities: {"elicitation": {"form": {}}})
← elicitation/create  "A Gemini API key is needed to generate images…"
                      requestedSchema properties: ["api_key"]
→ {"action": "accept", "content": {"api_key": "…"}}
← tool proceeds to call the API
  host file: exists, mode 0600
```

A second run reads the persisted key and does **not** prompt again.

### How the two open questions resolved

**Persistence.** The config mount is now writable (`:ro` dropped) and the
server writes the key with `os.open(..., 0o600)` — permissions set at
creation rather than created-then-chmodded, so there is no window where the
file is world-readable. Writing only happens to a path whose parent directory
already exists, so the server never builds a directory tree in someone's home.
Persisting is **best effort**: if nothing is writable the key is used for the
session and the user is asked again next time, rather than the request failing.

Dropping `:ro` is a real widening — the container can now write to
`~/.config/gemini`. It is scoped to that one directory, and it is what buys
"answer once" instead of "answer every session".

**Capability negotiation.** `_client_supports_elicitation()` checks what the
client advertised at initialize. A client that did not advertise it is never
sent a prompt it would ignore; it gets the actionable error instead. Verified
both ways: a client declaring `{"elicitation": {"form": {}}}` is prompted, a
client declaring `{}` gets the instructions.

### Deliberate behaviours

- **Decline and cancel are respected.** Neither re-prompts; both raise the
  actionable error. A user who says no is not asked again in a loop.
- **Elicitation failure degrades, never crashes.** The `except` is broad on
  purpose: the failure modes are whatever the client does — timeout, transport
  error, malformed response, an unimplemented method behind an advertised
  capability. All of them fall back to the instructions.
- **Enterprise/ADC mode skips elicitation entirely**, since no API key is
  wanted there.
- **The key is set in `os.environ` after collection**, so a later call in the
  same session does not prompt again even when persisting failed.
- **`ctx` is hidden from the tool schema** — the client-visible parameters are
  unchanged.

### Cost

The three image tools became `async def`, because `Context.elicit` is a
coroutine. `get_help` stays synchronous. The public tool surface did not
change.
